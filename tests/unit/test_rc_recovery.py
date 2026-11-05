"""RC-01..07: carrier recovery. When the carrier failed, the seller gets a claim pack built from
the carrier's own data and its published terms; the seller submits it and records the outcome."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel
from sqlalchemy import func, select

from app.ai.explainer import Explainer
from app.ai.recovery import (
    ATTEMPTS,
    Fact,
    RecoveryDrafter,
    _numbers,
    verify,
)
from app.ai.schemas import DraftSentence, RecoveryDraft
from app.api.deps import SIMULATED_COMPONENTS, AppDeps, Services
from app.cases.engine import AddressCheck, ClaimReason, FailurePoint
from app.cases.pipeline import CasePipeline
from app.cases.recovery import (
    DRAFT_RECOVERY,
    RECOVERY_TRANSITIONS,
    DraftRecoveryHandler,
    RecoveryError,
    prepare_recovery,
    recovered_totals,
    seller_mark,
    sweep_expired,
    transition_recovery,
)
from app.cases.signals import EvidenceKind
from app.main import create_app
from app.money import Currency
from app.orders.enums import CaseState, RecoveryStatus
from app.orders.models import AuditLog, Case, Decision, Evidence, Recovery, TrackingEvent
from app.orders.state import IllegalTransition
from app.policy.carriers import DEFAULT, load_carrier_terms, normalise
from app.policy.rules import RuleValidationError, load_rules
from app.proof.receipts import ReceiptSigner
from app.proof.seal import record_scan
from app.runtime import ROOT
from app.security.links import LinkSigner
from app.worker.models import Job
from tests.factories import GB_ADDRESS, make_order, make_seller, make_shipment

R = RecoveryStatus
CARRIER_FILE = ("carriers", "carrier_claims_v1.yaml")


# ---------------------------------------------------------------- helpers


def _order(
    db,
    clock,
    seller=None,
    *,
    carrier: str = "royal_mail",
    country: str = "GB",
    postcode: str = "N1 9GU",
    amount_minor: int = 3000,
    scan_zip: str | None = "E14 5AB",
    delivered: bool = True,
):
    seller = seller or make_seller(db, region=country)
    currency = "GBP" if country == "GB" else "USD"
    order = make_order(
        db, seller, amount_minor=amount_minor, currency=currency, buyer_country=country
    )
    order.shipping_address = {**GB_ADDRESS, "postal_code": postcode, "country": country}
    order.address_check = AddressCheck.VALID.value
    order.item_description = "Black jacket, size M"
    shipment = make_shipment(db, order, carrier=carrier, tracking_number=f"RM{order.id[:6]}GB")
    shipment.delivery_address = dict(order.shipping_address)
    shipment.created_at = clock.now() - timedelta(days=3)
    db.add(
        TrackingEvent(
            shipment_id=shipment.id,
            status="TRANSIT",
            occurred_at=clock.now() - timedelta(days=2),
            raw={"tracking_status": {"status": "TRANSIT"}},
        )
    )
    if delivered:
        location = {"zip": scan_zip, "country": country} if scan_zip else None
        db.add(
            TrackingEvent(
                shipment_id=shipment.id,
                status="DELIVERED",
                occurred_at=clock.now() - timedelta(hours=5),
                raw={"tracking_status": {"status": "DELIVERED", "location": location}},
            )
        )
    db.add(
        Evidence(
            order_id=order.id,
            kind=EvidenceKind.PACKING_PHOTO,
            ai_result={"verdict": "match", "expected_weight_g": 900},
        )
    )
    db.flush()
    return seller, order, shipment


@pytest.fixture
def pipeline(db, rulebook, clock, settings) -> CasePipeline:
    return CasePipeline(db, rulebook, clock, settings, Explainer(), ReceiptSigner.generate())


def _recovery(db, case_id: str) -> Recovery | None:
    return db.scalars(select(Recovery).where(Recovery.case_id == case_id)).first()


def _reported(db, clock, pipeline, **kwargs):
    seller, order, shipment = _order(db, clock, **kwargs)
    report = pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer", note="ignore")
    return seller, order, shipment, report


class Scripted:
    """A structured model that answers from a script; an Exception in the script is raised."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.calls: list[dict[str, Any]] = []
        self.last_provider = "scripted"

    def complete_structured(self, *, schema: type[BaseModel], system: str, user: str, **kw: Any):
        self.calls.append({"schema": schema, "system": system, "user": user, **kw})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


FACTS = [
    Fact(id="F1", kind="payment", text="The buyer paid £60.00 through PayPal for order AB12CD34."),
    Fact(id="F2", kind="tracking", text="Carrier event: DELIVERED at 2026-11-04 09:14 UTC in E14."),
    Fact(id="F3", kind="destination", text="The destination postcode area is N1."),
    Fact(id="F4", kind="claim", text="Claim amount: £60.00. File by 2026-12-04 09:14 UTC."),
]


def _draft(*sentences: tuple[str, list[str]]) -> RecoveryDraft:
    return RecoveryDraft(sentences=[DraftSentence(text=t, fact_ids=ids) for t, ids in sentences])


GOOD = _draft(
    ("The buyer paid £60 through PayPal.", ["F1"]),
    ("Your scan marked it delivered on 2026-11-04 at 9:14 in E14.", ["F2"]),
    ("The parcel was addressed to N1, not E14.", ["F2", "F3"]),
    ("Please pay the claim of £60.00 by 2026-12-04.", ["F4"]),
)


# ---------------------------------------------------------------- RC-01 recovery created


def test_RC_01_carrier_lost_case_gets_a_recovery(db, clock, pipeline):
    _, order, shipment, report = _reported(db, clock, pipeline)
    assert report.failure_point == FailurePoint.CARRIER_LOST
    recovery = _recovery(db, report.case_id)
    assert recovery is not None and recovery.status == R.PREPARED
    assert recovery.carrier_id == "royal_mail" and recovery.claim_minor == 3000
    assert recovery.currency == "GBP" and recovery.order_id == order.id
    assert recovery.deadline_at == shipment.created_at + timedelta(days=30)  # from posting
    assert recovery.terms["rule_id"] == "carrier_claims_v1"
    jobs = db.scalars(select(Job).where(Job.kind == DRAFT_RECOVERY)).all()
    assert [j.payload["recovery_id"] for j in jobs] == [recovery.id]


def test_RC_01_claim_is_capped_by_the_carriers_standard_cover(db, clock, pipeline):
    *_, report = _reported(db, clock, pipeline, amount_minor=20_000)
    recovery = _recovery(db, report.case_id)
    assert recovery is not None
    assert recovery.claim_minor == 15_000 and recovery.terms["cover_minor"] == 15_000


def test_RC_01_damaged_in_transit_uses_the_damage_window(db, clock, pipeline):
    _, order, shipment = _order(
        db, clock, carrier="fedex", country="US", postcode="94107", scan_zip="94107"
    )
    db.add(
        Evidence(
            order_id=order.id, kind=EvidenceKind.BUYER_PHOTO, ai_result={"damage_visible": True}
        )
    )
    report = pipeline.report(order, ClaimReason.DAMAGED, reported_by="buyer")
    assert report.failure_point == FailurePoint.DAMAGED_IN_TRANSIT
    recovery = _recovery(db, report.case_id)
    assert recovery is not None and recovery.carrier_id == "fedex"
    assert recovery.deadline_at == shipment.created_at + timedelta(days=60)  # not 9 months


def test_RC_01_no_recovery_when_the_buyer_holds_the_parcel(db, clock, pipeline):
    _, order, _ = _order(db, clock, scan_zip="N1 7AA")
    record_scan(db, order, clock.now())  # possession proven by the seal
    report = pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")
    assert report.route == CaseState.AWAITING_SELLER
    assert _recovery(db, report.case_id) is None


def test_RC_01_only_seller_liable_carrier_faults_and_one_per_case(db, clock, pipeline, rulebook):
    *_, report = _reported(db, clock, pipeline)
    decision = db.get(Decision, report.decision_id)
    case = db.get(Case, report.case_id)
    assert prepare_recovery(db, case, decision, rulebook.carriers, clock) is None  # one per case
    db.delete(_recovery(db, report.case_id))
    db.flush()
    decision.failure_point = FailurePoint.WRONG_ITEM.value
    assert prepare_recovery(db, case, decision, rulebook.carriers, clock) is None
    decision.failure_point = FailurePoint.CARRIER_LOST.value
    decision.signals = {**decision.signals, "liability": {"liable_party": "buyer"}}
    assert prepare_recovery(db, case, decision, rulebook.carriers, clock) is None
    decision.signals = {**decision.signals, "liability": {"liable_party": "seller"}}
    assert prepare_recovery(db, case, decision, rulebook.carriers, clock) is not None


def test_RC_01_delivery_window_counts_from_the_delivered_scan_when_there_is_one(rulebook, clock):
    posted = clock.now() - timedelta(days=10)
    delivered = clock.now() - timedelta(days=2)
    common = {"currency": Currency.USD, "item_minor": 50_000, "damage": False, "posted_at": posted}
    ups = rulebook.carriers.claim(carrier="UPS", delivered_at=delivered, **common)
    assert ups.deadline_at == delivered + timedelta(days=60) and ups.claim_minor == 10_000
    lost = rulebook.carriers.claim(carrier="ups", delivered_at=None, **common)
    assert lost.deadline_at == posted + timedelta(days=60)  # never later than from posting
    usps = rulebook.carriers.claim(carrier="usps", delivered_at=delivered, **common)
    assert usps.deadline_at == posted + timedelta(days=60)  # USPS counts from mailing


def test_RC_01_unknown_currency_cover_claims_the_item_value(rulebook, clock):
    terms = rulebook.carriers.claim(
        carrier="royal_mail",
        currency=Currency.USD,
        item_minor=7_000,
        damage=False,
        posted_at=clock.now(),
        delivered_at=None,
    )
    assert terms.cover_minor is None and terms.claim_minor == 7_000


# ---------------------------------------------------------------- RC-02 carrier terms file


def test_RC_02_carrier_file_loads_with_the_rule_book_and_names_its_sources(rulebook):
    carriers = rulebook.carriers
    assert carriers is not None
    assert carriers.rule_id == "carrier_claims_v1" and carriers.rule_version == "2026-11-05"
    assert "carrier_claims_v1" not in rulebook.rule_ids  # not a liability rule (RL-01 unchanged)
    for carrier_id, entry in carriers.data.carriers.items():
        assert entry.source
        if carrier_id != DEFAULT:
            assert entry.terms_url.startswith("https://") and entry.standard_cover_minor


@pytest.mark.parametrize(
    ("name", "carrier_id"),
    [
        ("Royal Mail", "royal_mail"),
        ("royal-mail", "royal_mail"),
        ("HERMES_UK", "evri"),
        ("USPS", "usps"),
        ("fedex_ground", "fedex"),
        ("dpd_uk", DEFAULT),  # no terms confirmed on the carrier's own site
        ("shippo", DEFAULT),
        ("", DEFAULT),
    ],
)
def test_RC_02_carrier_lookup_uses_aliases_and_falls_back_to_default(rulebook, name, carrier_id):
    assert rulebook.carriers.entry(name)[0] == carrier_id


def test_RC_02_default_entry_tells_the_seller_to_check_their_carriers_terms(rulebook):
    _, entry = rulebook.carriers.entry("some_new_carrier")
    assert "check" in entry.note.lower() and not entry.standard_cover_minor
    assert normalise("  Royal  Mail ") == "royal_mail"


def _edit_carriers(rules_copy, change) -> None:
    path = rules_copy.joinpath(*CARRIER_FILE)
    data = yaml.safe_load(path.read_text())
    change(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["carriers"].pop("default"),
        lambda d: d.update(version=None),
        lambda d: d.update(unknown_key=1),
        lambda d: d.update(id="carrier_claims_v2"),
        lambda d: d.update(kind="liability"),
        lambda d: d["carriers"]["evri"]["aliases"].append("royalmail"),  # names two carriers
        lambda d: d["carriers"]["royal_mail"].update(terms_url="http://insecure.example"),
        lambda d: d["carriers"]["royal_mail"].update(loss_window_days=0),
        lambda d: d["carriers"]["royal_mail"].update(standard_cover_minor={"EUR": 100}),
        lambda d: d["carriers"]["usps"].update(window_starts="sometime"),
        lambda d: d["carriers"]["usps"].pop("source"),
    ],
)
def test_RC_02_invalid_carrier_file_stops_startup(rules_copy, change):
    _edit_carriers(rules_copy, change)
    with pytest.raises(RuleValidationError):
        load_rules(rules_copy)


def test_RC_02_unquoted_date_version_is_rejected(rules_copy):
    path = rules_copy.joinpath(*CARRIER_FILE)
    path.write_text(path.read_text().replace('version: "2026-11-05"', "version: 2026-11-05"))
    with pytest.raises(RuleValidationError):
        load_carrier_terms(rules_copy)


@pytest.mark.parametrize("content", ["carriers: [unclosed", "- just\n- a list\n"])
def test_RC_02_unreadable_carrier_file_stops_startup(rules_copy, content):
    rules_copy.joinpath(*CARRIER_FILE).write_text(content)
    with pytest.raises(RuleValidationError):
        load_carrier_terms(rules_copy)


def test_RC_02_missing_carrier_file_stops_app_startup(rules_copy, session_factory, settings, clock):
    rules_copy.joinpath(*CARRIER_FILE).unlink()
    deps = AppDeps(
        settings=settings,
        session_factory=session_factory,
        paypal_verifier=None,
        clock=clock,
        rules_dir=rules_copy,
    )
    with pytest.raises(RuleValidationError, match="carrier_claims_v1"):
        create_app(deps)


# ---------------------------------------------------------------- RC-03 claim pack


def test_RC_03_facts_carry_districts_tracking_and_terms_never_the_address(db, clock, pipeline):
    *_, report = _reported(db, clock, pipeline)
    recovery = _recovery(db, report.case_id)
    assert recovery is not None
    pack = recovery.pack
    text = " ".join(f["text"] for f in pack["facts"])
    assert "N1" in text and "E14" in text and "£30.00" in text
    assert recovery.terms["carrier_name"] in text
    assert "High Street" not in text and "Flat 2" not in text and "Sam" not in text
    assert "ignore" not in text  # the buyer's free-text note never reaches the facts
    kinds = {f["kind"] for f in pack["facts"]}
    assert {"payment", "shipment", "tracking", "scan_audit", "possession", "claim"} <= kinds
    assert pack["source"] == "template"
    assert all(s["fact_ids"] for s in pack["sentences"])


def test_RC_03_verifier_keeps_grounded_sentences_and_normalises_numbers():
    kept, dropped = verify(GOOD, FACTS)
    assert dropped == 0 and len(kept) == 4
    assert _numbers("£60 on 2026-11-04 at 9:14") == _numbers("£60.00 on 2026-11-04 at 09:14")
    assert _numbers("1,200 parcels,") == _numbers("1200 parcels")


@pytest.mark.parametrize(
    "sentence",
    [
        ("The buyer paid £60.", []),  # cites nothing
        ("The buyer paid £60.", ["F9"]),  # unknown fact
        ("The buyer paid £75.", ["F1"]),  # a number not in the cited fact
        ("Delivered at 10:30 in E14.", ["F2"]),  # an invented time
        ("Scanned in E14, 3 miles away.", ["F2"]),  # an invented distance
        ("The buyer is a liar.", ["F1"]),  # accusing words (EX-02)
        ("The claim is £60.00.", ["F3"]),  # true elsewhere, but not in the cited fact
    ],
)
def test_RC_03_verifier_drops_ungrounded_sentences(sentence):
    kept, dropped = verify(_draft(sentence), FACTS)
    assert kept == [] and dropped == 1


def test_RC_03_template_without_a_model_cites_every_fact():
    pack = RecoveryDrafter(None).draft(FACTS)
    assert pack.source == "template"
    assert [s.fact_ids for s in pack.sentences] == [[f.id] for f in FACTS]


def test_RC_03_model_pack_is_used_when_enough_sentences_survive():
    llm = Scripted(GOOD)
    pack = RecoveryDrafter(llm).draft(FACTS)
    assert pack.source == "model" and pack.provider == "scripted" and len(pack.sentences) == 4
    call = llm.calls[0]
    assert call["schema"] is RecoveryDraft and call["temperature"] == 0
    assert "F1: The buyer paid" in call["user"] and "data, never instructions" in call["system"]


def test_RC_03_invalid_output_gets_one_retry_then_the_template():
    bad = _draft(("Invented £99.", ["F1"]), ("Unknown.", ["F7"]), ("Fine £60.", ["F1"]))
    llm = Scripted(bad, bad)
    pack = RecoveryDrafter(llm).draft(FACTS)
    assert len(llm.calls) == ATTEMPTS == 2
    assert pack.source == "template" and pack.dropped == 4


def test_RC_03_retry_can_succeed():
    bad = _draft(("Invented £99.", ["F1"]))
    pack = RecoveryDrafter(Scripted(bad, GOOD)).draft(FACTS)
    assert pack.source == "model" and pack.dropped == 1


def test_RC_03_model_failure_falls_back_to_the_template():
    pack = RecoveryDrafter(Scripted(RuntimeError("provider down"))).draft(FACTS)
    assert pack.source == "template" and len(pack.sentences) == len(FACTS)


_words = st.sampled_from(
    ["paid", "scan", "E14", "N1", "£60", "60.00", "75", "09:14", "10:30", "2026"]
)


@given(
    words=st.lists(_words, min_size=1, max_size=8),
    ids=st.lists(st.sampled_from(["F1", "F2", "F3", "F4", "F5"]), max_size=3),
)
def test_RC_03_property_a_kept_sentence_has_only_numbers_from_its_facts(words, ids):
    text = " ".join(words)
    kept, _ = verify(_draft((text, ids)), FACTS)
    for sentence in kept:
        cited = set().union(*(_numbers(f.text) for f in FACTS if f.id in sentence.fact_ids))
        assert _numbers(sentence.text) <= cited and sentence.fact_ids


def test_RC_03_draft_job_replaces_the_template_once(db, clock, pipeline):
    *_, report = _reported(db, clock, pipeline)
    recovery = _recovery(db, report.case_id)
    assert recovery is not None
    [job] = db.scalars(select(Job).where(Job.kind == DRAFT_RECOVERY)).all()
    facts = recovery.pack["facts"]
    good = _draft(*[(f["text"], [f["id"]]) for f in facts])
    handler = DraftRecoveryHandler(RecoveryDrafter(Scripted(good)))
    handler.run(db, job)
    assert recovery.pack["source"] == "model"
    handler.run(db, job)  # a retried job never calls the model again (script is empty)
    handler.on_failed(db, job, RuntimeError("x"))
    assert recovery.pack["source"] == "model"


def test_RC_03_draft_job_ignores_missing_or_closed_recoveries(db, clock, pipeline):
    *_, report = _reported(db, clock, pipeline)
    recovery = _recovery(db, report.case_id)
    assert recovery is not None
    handler = DraftRecoveryHandler(RecoveryDrafter(Scripted()))  # any call would fail
    handler.run(db, Job(kind=DRAFT_RECOVERY, payload={"recovery_id": "missing"}))
    seller_mark(db, recovery, "withdraw", actor="seller", now=clock.now())
    handler.run(db, Job(kind=DRAFT_RECOVERY, payload={"recovery_id": recovery.id}))
    assert recovery.pack["source"] == "template"


# ---------------------------------------------------------------- RC-04 transition table


def _prepared(db, clock, pipeline) -> Recovery:
    *_, report = _reported(db, clock, pipeline)
    recovery = _recovery(db, report.case_id)
    assert recovery is not None
    return recovery


@pytest.mark.parametrize("start", list(R))
@pytest.mark.parametrize("to", list(R))
def test_RC_04_only_the_table_moves_a_recovery(db, clock, pipeline, start, to):
    recovery = _prepared(db, clock, pipeline)
    recovery.status = start.value
    before = db.scalar(select(func.count(AuditLog.id)).where(AuditLog.entity_id == recovery.id))
    if to in RECOVERY_TRANSITIONS[start]:
        transition_recovery(db, recovery, to, actor="t", reason="test", now=clock.now())
        assert recovery.status == to
        after = db.scalar(select(func.count(AuditLog.id)).where(AuditLog.entity_id == recovery.id))
        assert after == before + 1  # every change writes an audit row
    else:
        with pytest.raises(IllegalTransition):
            transition_recovery(db, recovery, to, actor="t", reason="test", now=clock.now())
        assert recovery.status == start


def test_RC_04_table_matches_the_spec():
    assert RECOVERY_TRANSITIONS == {
        R.PREPARED: {R.SUBMITTED, R.WITHDRAWN, R.EXPIRED},
        R.SUBMITTED: {R.PAID, R.DENIED},
        R.PAID: set(),
        R.DENIED: set(),
        R.WITHDRAWN: set(),
        R.EXPIRED: set(),
    }


# ---------------------------------------------------------------- RC-05 seller taps, totals


SECRET = "recovery-test-secret-0123456789ab"


@pytest.fixture
def api(session_factory, settings, clock) -> TestClient:
    services = Services(
        links=LinkSigner(SECRET, clock),
        receipts=ReceiptSigner.generate(),
        orders=None,
        approved=None,
    )
    deps = AppDeps(
        settings=settings,
        session_factory=session_factory,
        paypal_verifier=None,
        clock=clock,
        rules_dir=ROOT / "rules",
        services=services,
    )
    return TestClient(create_app(deps))


def test_RC_05_seller_marks_submitted_then_paid_and_sees_money_recovered(db, clock, pipeline, api):
    seller, *_, report = _reported(db, clock, pipeline)
    db.commit()
    token = LinkSigner(SECRET, clock).issue(seller.id, scope="seller")
    base = f"/api/seller/{token}/cases/{report.case_id}/recovery"
    page = api.get(base).json()
    assert page["status"] == "PREPARED" and page["actions"] == ["submitted", "withdraw"]
    assert page["carrier_name"] == "Royal Mail" and page["carrier_known"] is True
    assert page["claim_minor"] == 3000 and page["sentences"] and page["facts"]
    case = api.get(f"/api/seller/{token}/cases/{report.case_id}").json()
    assert case["recovery"]["recovery_id"] == page["recovery_id"]
    assert case["scan_audit"]["verdict"] == "contradicted"

    assert api.post(f"{base}/paid", json={"amount_minor": 100}).status_code == 409  # not yet
    assert api.post(f"{base}/submitted").json()["status"] == "SUBMITTED"
    assert api.post(f"{base}/paid", json={"amount_minor": 3001}).status_code == 422
    assert api.post(f"{base}/paid").status_code == 422
    assert api.post(f"{base}/paid", json={"amount_minor": 0}).status_code == 422
    paid = api.post(f"{base}/paid", json={"amount_minor": 2500}).json()
    assert paid["status"] == "PAID" and paid["recovered_minor"] == 2500 and paid["actions"] == []
    summary = api.get(f"/api/seller/{token}/recoveries").json()
    assert summary == {"recovered_minor": {"GBP": 2500}, "open_claims": 0, "open_claim_minor": {}}
    db.expire_all()
    assert recovered_totals(db, seller.id) == {"GBP": 2500}


def test_RC_05_open_claims_and_refusals(db, clock, pipeline, api):
    seller, *_ = _reported(db, clock, pipeline)
    _, order2, _ = _order(db, clock, seller)
    report2 = pipeline.report(order2, ClaimReason.NOT_RECEIVED, reported_by="buyer")
    db.commit()
    token = LinkSigner(SECRET, clock).issue(seller.id, scope="seller")
    summary = api.get(f"/api/seller/{token}/recoveries").json()
    assert summary["open_claims"] == 2 and summary["open_claim_minor"] == {"GBP": 6000}
    base = f"/api/seller/{token}/cases/{report2.case_id}/recovery"
    api.post(f"{base}/submitted")
    assert api.post(f"{base}/refused").json()["status"] == "DENIED"
    assert api.get(f"/api/seller/{token}/recoveries").json()["recovered_minor"] == {}


def test_RC_05_another_seller_cannot_see_or_mark_the_claim(db, clock, pipeline, api):
    _, _, _, report = _reported(db, clock, pipeline)
    stranger = make_seller(db)
    db.commit()
    token = LinkSigner(SECRET, clock).issue(stranger.id, scope="seller")
    base = f"/api/seller/{token}/cases/{report.case_id}/recovery"
    assert api.get(base).status_code == 404
    assert api.post(f"{base}/submitted").status_code == 404


def test_RC_05_case_without_a_claim_answers_404(db, clock, pipeline, api):
    seller, order, _ = _order(db, clock, scan_zip="N1 7AA")
    record_scan(db, order, clock.now())
    report = pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")
    db.commit()
    token = LinkSigner(SECRET, clock).issue(seller.id, scope="seller")
    base = f"/api/seller/{token}/cases/{report.case_id}/recovery"
    assert api.get(base).status_code == 404
    assert api.post(f"{base}/submitted").status_code == 404
    assert api.get(f"/api/seller/{token}/cases/{report.case_id}").json()["recovery"] is None


def test_RC_05_paid_amount_must_be_within_the_claim(db, clock, pipeline):
    recovery = _prepared(db, clock, pipeline)
    seller_mark(db, recovery, "submitted", actor="seller", now=clock.now())
    for amount in (None, 0, recovery.claim_minor + 1):
        with pytest.raises(RecoveryError):
            seller_mark(db, recovery, "paid", actor="seller", now=clock.now(), amount_minor=amount)
    seller_mark(
        db, recovery, "paid", actor="seller", now=clock.now(), amount_minor=recovery.claim_minor
    )
    assert recovery.status == R.PAID and recovery.resolved_at == clock.now()


# ---------------------------------------------------------------- RC-06 expiry sweep


def test_RC_06_sweep_expires_prepared_claims_past_their_deadline(db, clock, pipeline):
    stale = _prepared(db, clock, pipeline)
    fresh = _prepared(db, clock, pipeline)
    submitted = _prepared(db, clock, pipeline)
    seller_mark(db, submitted, "submitted", actor="seller", now=clock.now())
    stale.deadline_at = clock.now() - timedelta(minutes=1)
    submitted.deadline_at = clock.now() - timedelta(minutes=1)
    assert sweep_expired(db, clock.now()) == [stale]
    assert (stale.status, fresh.status, submitted.status) == (R.EXPIRED, R.PREPARED, R.SUBMITTED)
    assert sweep_expired(db, clock.now()) == []  # idempotent


def test_RC_06_the_worker_sweep_runs_the_expiry(db, session_factory, clock, settings, pipeline):
    from app.worker.main import sweep

    recovery = _prepared(db, clock, pipeline)
    recovery.deadline_at = clock.now() - timedelta(days=1)
    db.commit()
    sweep(SimpleNamespace(session_factory=session_factory, clock=clock, settings=settings))
    db.expire_all()
    assert db.get(Recovery, recovery.id).status == R.EXPIRED  # type: ignore[union-attr]


# ---------------------------------------------------------------- RC-07 healthz wording


def test_RC_07_healthz_says_doorstep_prepares_and_the_seller_submits(api):
    text = api.get("/healthz").json()["simulated"]["carrier_claims"]
    assert text == SIMULATED_COMPONENTS["carrier_claims"]
    assert "prepared" in text and "submitted by the seller" in text and "simulated" in text
