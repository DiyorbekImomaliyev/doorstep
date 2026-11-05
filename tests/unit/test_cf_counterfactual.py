"""CF-01, CF-02: what would have changed a decision, and prevention suggestions, in code only."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import select

from app.ai.explainer import _ACCUSING, Explainer
from app.api.deps import AppDeps, Services
from app.cases.counterfactual import (
    PROOF_TEXT,
    SAFER_METHOD,
    _band,
    counterfactual,
    prevention,
)
from app.cases.engine import AddressCheck, ClaimReason
from app.cases.pipeline import CasePipeline
from app.main import create_app
from app.orders.enums import CaseState, ProofKind
from app.orders.models import Case, Decision, TrackingEvent
from app.policy.rules import load_rules
from app.proof.receipts import ReceiptSigner
from app.runtime import ROOT
from app.security.links import LinkSigner
from tests.factories import add_decision, make_case, make_order, make_seller, make_shipment
from tuning import engine_config as cfg

RULES = load_rules(ROOT / "rules")
UK = RULES.select("GB")
US = RULES.select("US")
LIABILITY = list(RULES.liability)
proof_sets = st.frozensets(st.sampled_from(list(ProofKind)))


# ---------------------------------------------------------------- CF-01


def test_CF_01_uk_no_proof_names_every_proof_that_would_have_changed_it():
    assert UK is not None
    cf = counterfactual(UK, frozenset({ProofKind.DELIVERED_SCAN}))
    assert not cf.possession_proven and cf.rule_id == "uk_possession_v1"
    assert set(cf.would_change) == set(UK.possession_proof)
    assert ProofKind.BUYER_SEAL_SCAN in cf.would_change
    assert cf.text.startswith("Under uk_possession_v1, ")
    assert "a scan of the seal packed inside the parcel" in cf.text
    assert cf.text.endswith("would have made this the buyer's risk.")


def test_CF_01_present_proof_is_named_instead():
    assert UK is not None
    cf = counterfactual(UK, frozenset({ProofKind.BUYER_SEAL_SCAN, ProofKind.DELIVERED_SCAN}))
    assert cf.possession_proven and cf.present == [ProofKind.BUYER_SEAL_SCAN]
    assert cf.would_change == [] and cf.would_change_together == []
    assert cf.text == (
        "Possession was proven under uk_possession_v1 by a scan of the seal packed inside the "
        "parcel."
    )


def test_CF_01_us_names_the_all_of_set_and_a_discounted_scan():
    assert US is not None
    proofs = frozenset({ProofKind.DELIVERED_SCAN, ProofKind.DELIVERY_PHOTO})
    plain = counterfactual(US, proofs)
    assert plain.possession_proven and set(plain.present) == set(US.proof_required)
    contradicted = counterfactual(US, proofs, scan_contradicted=True)
    assert not contradicted.possession_proven and contradicted.scan_discounted
    assert contradicted.would_change_together == list(US.proof_required)
    assert contradicted.scan_must_be_supported
    assert (
        "a delivered scan that the carrier's own data supports together with a delivery photo"
        in contradicted.text
    )
    assert "the carrier's own data contradicts it" in contradicted.text


def test_CF_01_contradicted_scan_counts_where_the_rule_says_so():
    assert UK is not None  # the UK rule never relied on the scan, so nothing is discounted
    cf = counterfactual(UK, frozenset({ProofKind.DELIVERED_SCAN}), scan_contradicted=True)
    assert not cf.scan_discounted and "contradicts" not in cf.text
    assert US is not None  # no scan present: nothing to discount
    assert not counterfactual(US, frozenset(), scan_contradicted=True).scan_discounted


def test_CF_01_no_rule_set_means_a_person_reviews():
    cf = counterfactual(None, frozenset({ProofKind.SIGNATURE}))
    assert cf.rule_id is None and cf.would_change == [] and not cf.possession_proven
    assert "a person reviews it" in cf.text


def test_CF_01_a_rule_where_proof_changes_nothing_says_so():
    assert UK is not None
    same = UK.model_copy(update={"when_possession_proven": UK.when_no_possession_proof})
    cf = counterfactual(same, frozenset())
    assert cf.would_change == [] and cf.text == (
        "Under uk_possession_v1, no proof changes who is responsible."
    )


def test_CF_01_a_rule_with_only_an_all_of_set():
    assert US is not None
    only_all = US.model_copy(update={"possession_proof": []})
    cf = counterfactual(only_all, frozenset())
    assert cf.would_change == [] and cf.would_change_together == list(US.proof_required)
    assert cf.text == (
        "Under us_seller_policy_v1, a delivered scan together with a delivery photo would have "
        "made this the buyer's risk."
    )


@given(proofs=proof_sets, contradicted=st.booleans(), rule=st.sampled_from(LIABILITY))
def test_CF_01_every_named_proof_really_changes_the_outcome(proofs, contradicted, rule):
    """The counterfactual is true, not decoration: adding any one named proof (or the whole
    all-of set) proves possession under the same rule, and the text never blames anyone."""
    cf = counterfactual(rule, proofs, scan_contradicted=contradicted)
    base = rule.evaluate(proofs, scan_contradicted=contradicted)
    assert cf.possession_proven == base.possession_proven
    # In the counterfactual world a named delivered scan is one the carrier's data supports.
    world = contradicted and not cf.scan_must_be_supported
    for proof in cf.would_change:
        assert rule.evaluate(proofs | {proof}, scan_contradicted=world).possession_proven
    if cf.would_change_together:
        together = proofs | set(cf.would_change_together)
        assert rule.evaluate(together, scan_contradicted=world).possession_proven
    if cf.scan_must_be_supported:
        assert "a delivered scan that the carrier's own data supports" in cf.text
    if cf.possession_proven:
        assert cf.present and not cf.would_change
        assert rule.possession_proven(frozenset(cf.present))
    assert not _ACCUSING.search(cf.text)
    assert all(p in PROOF_TEXT for p in [*cf.present, *cf.would_change])


# ---------------------------------------------------------------- CF-02


def _case(db, seller, clock, *, amount_minor: int, method: str, party: str | None = "seller"):
    order = make_order(db, seller, amount_minor=amount_minor)
    make_shipment(db, order).method = method
    if party is None:  # an order with no case at all
        return order
    case = make_case(db, order, state=CaseState.SETTLED)
    decision = add_decision(db, case)
    decision.signals = {"liability": {"liable_party": party}}
    decision.created_at = clock.now()
    db.flush()
    return order


def test_CF_02_groups_seller_liable_cases_by_method_and_value_band(db, clock):
    seller = make_seller(db)
    for amount in (3000, 4500, 7000):  # three home deliveries of £25 to £75 cost the seller
        _case(db, seller, clock, amount_minor=amount, method="home")
    _case(db, seller, clock, amount_minor=5000, method="home", party=None)  # delivered fine
    _case(db, seller, clock, amount_minor=6000, method="home", party="buyer")  # possession proven
    [tip] = prevention(db, seller.id)
    assert (tip.method, tip.suggested_method, tip.currency) == ("home", "locker", "GBP")
    assert (tip.band, tip.band_min_minor, tip.band_max_minor) == ("£25 to £75", 2500, 7500)
    assert (tip.cases, tip.orders, tip.money_minor) == (3, 5, 14500)
    assert tip.text == (
        "3 of your 5 home delivery orders of £25 to £75 became cases you paid for (£145.00). "
        "Offer locker pickup for this band: a locker needs the buyer's pickup code, which "
        "proves possession."
    )
    assert not _ACCUSING.search(tip.text)


def test_CF_02_needs_enough_cases_and_a_safer_method(db, clock):
    seller = make_seller(db)
    _case(db, seller, clock, amount_minor=3000, method="home")  # one case: below the minimum
    for _ in range(3):
        _case(db, seller, clock, amount_minor=30000, method="signature")  # nothing safer
    assert cfg.PREVENTION_MIN_CASES == 2 and "signature" not in SAFER_METHOD
    assert prevention(db, seller.id) == []


def test_CF_02_most_money_first_capped_and_per_seller(db, clock):
    seller, other = make_seller(db), make_seller(db, region="US")
    for amount, method in [(1000, "home")] * 2 + [(25000, "locker")] * 2 + [(9000, "home")] * 2:
        _case(db, seller, clock, amount_minor=amount, method=method)
    for _ in range(2):  # a fourth group, same money as the smallest: dropped by the cap
        _case(db, seller, clock, amount_minor=1000, method="locker")
    for _ in range(2):
        _case(db, other, clock, amount_minor=5000, method="home")
    tips = prevention(db, seller.id)
    assert len(tips) == cfg.PREVENTION_MAX_SUGGESTIONS == 3
    assert [(t.method, t.band) for t in tips] == [
        ("locker", "£200 and over"),
        ("home", "£75 to £200"),
        ("home", "under £25"),
    ]
    assert tips[0].suggested_method == "signature" and "a signature proves who received it" in (
        tips[0].text
    )
    assert [t.currency for t in prevention(db, other.id)] == ["GBP"]


def test_CF_02_only_the_latest_decision_per_order_counts(db, clock):
    seller = make_seller(db)
    orders = [_case(db, seller, clock, amount_minor=3000, method="home") for _ in range(2)]
    assert [t.cases for t in prevention(db, seller.id)] == [2]
    # A reviewer's later decision on the first order found possession proven.
    first_case = db.scalars(select(Case).where(Case.order_id == orders[0].id)).one()
    later = add_decision(db, first_case)
    later.signals = {"liability": {"liable_party": "buyer"}}
    later.created_at = clock.now() + timedelta(minutes=5)
    db.flush()
    assert prevention(db, seller.id) == []


@pytest.mark.parametrize(
    ("amount", "band"),
    [(0, (0, 2500)), (2499, (0, 2500)), (2500, (2500, 7500)), (19999, (7500, 20000))],
)
def test_CF_02_band_edges(amount, band):
    assert _band(amount) == band
    assert _band(20000) == (20000, None)


# ---------------------------------------------------------------- wiring: decision, API

SECRET = "counterfactual-test-secret-0123456789"


def _reported_uk(db, clock, rulebook, settings):
    seller = make_seller(db)
    order = make_order(db, seller, amount_minor=6000)
    order.address_check = AddressCheck.VALID.value
    shipment = make_shipment(db, order, carrier="royal_mail", tracking_number="RM123456785GB")
    for status, hours in (("TRANSIT", 30), ("DELIVERED", 6)):
        db.add(
            TrackingEvent(
                shipment_id=shipment.id,
                status=status,
                occurred_at=clock.now() - timedelta(hours=hours),
                raw={"tracking_status": {"status": status}},
            )
        )
    db.flush()
    pipeline = CasePipeline(db, rulebook, clock, settings, Explainer(), ReceiptSigner.generate())
    report = pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")
    return seller, report


def test_CF_01_every_decision_stores_its_counterfactual(db, clock, rulebook, settings):
    _, report = _reported_uk(db, clock, rulebook, settings)
    decision = db.scalars(select(Decision).where(Decision.case_id == report.case_id)).one()
    stored = decision.signals["counterfactual"]
    assert stored["rule_id"] == decision.rule_id == "uk_possession_v1"
    assert stored["rule_version"] == decision.rule_version
    assert not stored["possession_proven"] and "buyer_seal_scan" in stored["would_change"]


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


def test_CF_01_CF_02_the_seller_sees_both(db, clock, rulebook, settings, api):
    seller, report = _reported_uk(db, clock, rulebook, settings)
    for _ in range(2):
        _case(db, seller, clock, amount_minor=4000, method="home")
    db.commit()
    token = LinkSigner(SECRET, clock).issue(seller.id, scope="seller")
    page = api.get(f"/api/seller/{token}/cases/{report.case_id}").json()
    assert page["counterfactual"]["text"].startswith("Under uk_possession_v1, ")
    tips = api.get(f"/api/seller/{token}/prevention").json()
    assert [(t["method"], t["band"], t["cases"]) for t in tips] == [("home", "£25 to £75", 3)]
    assert api.get("/api/seller/forged/prevention").status_code in (401, 403, 404)
