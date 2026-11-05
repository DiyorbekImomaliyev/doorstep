"""DE-01..04: when the buyer has opened a PayPal dispute, the seller's one tap sends facts-only
evidence (tracking, seal scan, scan audit, receipt link) through app.policy, exactly once."""

from __future__ import annotations

import ast
import hashlib
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.ai.explainer import Explainer
from app.api.deps import AppDeps, Services
from app.cases.actions import CaseActions, seller_approver
from app.cases.dispute_evidence import UnsafeEvidence, build_submission
from app.cases.engine import AddressCheck, ClaimReason
from app.cases.pipeline import CasePipeline
from app.cases.signals import EvidenceKind
from app.main import create_app
from app.orders.enums import CaseState, MoneyActionKind, MoneyActionStatus, OrderState
from app.orders.models import (
    Approval,
    AuditLog,
    Case,
    Decision,
    Dispute,
    Evidence,
    MoneyAction,
    Shipment,
    TrackingEvent,
)
from app.paypal_client.errors import PayPalClientError, PayPalServerError
from app.paypal_client.types import DisputeEvidence
from app.policy.evidence import (
    DISPUTE_EVIDENCE_JOB,
    SUBMITTED,
    DisputeEvidenceJobHandler,
    EvidenceSubmission,
    queue_dispute_evidence,
)
from app.policy.service import MissingRecord, PolicyError, PolicyService, PolicyStateError
from app.proof.models import Receipt
from app.proof.receipts import ReceiptSigner, verify_chain
from app.proof.seal import record_scan
from app.runtime import ROOT
from app.security.links import LinkSigner
from app.worker.models import Job
from app.worker.simulated import SIMULATED_STATUS, RoutingRefundGateway
from tests.factories import (
    GB_ADDRESS,
    add_money_action,
    make_case,
    make_order,
    make_seller,
    make_shipment,
)
from tests.fakes import FakePayPal
from tests.helpers import run_jobs

SECRET = "evidence-test-secret-0123456789ab"
VERIFY = "https://doorstep.example/cases/CASE/verify"


def _disputed(db, clock, pipeline, *, seal: bool = True, dispute_id: str = "PP-D-0001"):
    """A US order the carrier delivered; the buyer scanned the seal, then opened a dispute."""
    seller = make_seller(db, region="US")
    order = make_order(db, seller, amount_minor=4500, currency="USD", buyer_country="US")
    order.shipping_address = {**GB_ADDRESS, "postal_code": "94107", "country": "US"}
    order.address_check = AddressCheck.VALID.value
    shipment = make_shipment(db, order, carrier="usps", tracking_number="9400111202555842")
    shipment.delivery_address = dict(order.shipping_address)
    for status, hours, zip_code in (("TRANSIT", 30, None), ("DELIVERED", 6, "94107")):
        location = {"zip": zip_code, "country": "US"} if zip_code else None
        db.add(
            TrackingEvent(
                shipment_id=shipment.id,
                status=status,
                occurred_at=clock.now() - timedelta(hours=hours),
                raw={"tracking_status": {"status": status, "location": location}},
            )
        )
    db.add(
        Evidence(order_id=order.id, kind=EvidenceKind.PACKING_PHOTO, ai_result={"verdict": "match"})
    )
    db.flush()
    if seal:
        record_scan(db, order, clock.now() - timedelta(hours=2))
    db.add(Dispute(order_id=order.id, paypal_dispute_id=dispute_id, status="open"))
    db.flush()
    report = pipeline.report(
        order, ClaimReason.NOT_RECEIVED, reported_by="buyer", note="my address is 1 Secret Lane"
    )
    case = db.get(Case, report.case_id)
    assert case is not None and case.state == CaseState.DISPUTE_EVIDENCE
    return seller, order, case


@pytest.fixture
def pipeline(db, rulebook, clock, settings, signer) -> CasePipeline:
    return CasePipeline(db, rulebook, clock, settings, Explainer(), signer)


@pytest.fixture
def signer() -> ReceiptSigner:
    return ReceiptSigner.generate()


@pytest.fixture
def actions(db, rulebook, clock, signer) -> CaseActions:
    return CaseActions(db, rulebook, clock, signer)


def _evidence_actions(db, case_id: str) -> list[MoneyAction]:
    return list(
        db.scalars(
            select(MoneyAction).where(
                MoneyAction.case_id == case_id, MoneyAction.kind == MoneyActionKind.EVIDENCE.value
            )
        )
    )


def _jobs(db) -> list[Job]:
    return list(db.scalars(select(Job).where(Job.kind == DISPUTE_EVIDENCE_JOB)))


class Flaky:
    """A gateway whose evidence call fails with the given errors first, then succeeds."""

    def __init__(self, inner: FakePayPal, *errors: Exception) -> None:
        self.inner = inner
        self.errors = list(errors)
        self.calls: list[str] = []

    def provide_dispute_evidence(self, dispute_id, *, evidence, request_id) -> None:  # type: ignore[no-untyped-def]
        self.calls.append(request_id)
        if self.errors:
            raise self.errors.pop(0)
        self.inner.provide_dispute_evidence(dispute_id, evidence=evidence, request_id=request_id)


# ---------------------------------------------------------------- DE-01 one action, one job


def test_DE_01_send_evidence_creates_one_evidence_action_and_one_job(db, clock, pipeline, actions):
    seller, _, case = _disputed(db, clock, pipeline)
    ma = actions.act(case, "send-evidence", seller_approver(seller.id), verify_url=VERIFY)
    assert ma is not None and ma.kind == MoneyActionKind.EVIDENCE
    assert (ma.amount_minor, ma.currency, ma.status) == (0, "USD", MoneyActionStatus.QUEUED)
    assert ma.request_id and "PP-D-0001" in ma.request_id
    [job] = _jobs(db)
    assert job.payload["money_action_id"] == ma.id and job.payload["dispute_id"] == "PP-D-0001"
    assert job.payload["evidence"]["evidence_type"] == "PROOF_OF_FULFILLMENT"
    assert case.state == CaseState.DISPUTE_EVIDENCE  # PayPal decides; Doorstep does not settle


def test_DE_01_evidence_needs_a_dispute_case_with_an_open_dispute(db, clock, pipeline, actions):
    seller, order, case = _disputed(db, clock, pipeline)
    db.scalars(select(Dispute).where(Dispute.order_id == order.id)).one().status = "resolved"
    with pytest.raises(PolicyError, match="no open PayPal dispute"):
        actions.act(case, "send-evidence", seller_approver(seller.id), verify_url=VERIFY)
    case.state = CaseState.AWAITING_SELLER.value
    with pytest.raises(PolicyStateError):
        actions.act(case, "send-evidence", seller_approver(seller.id), verify_url=VERIFY)
    assert _evidence_actions(db, case.id) == [] and _jobs(db) == []


def test_DE_01_no_decision_means_nothing_to_send(db, rulebook, clock, actions):
    order = make_order(db, make_seller(db), state=OrderState.CASE_OPEN)
    case = make_case(db, order)
    case.state = CaseState.DISPUTE_EVIDENCE.value
    with pytest.raises(PolicyError, match="no decision"):
        actions.send_evidence(case, seller_approver("s"), verify_url=VERIFY)
    submission = EvidenceSubmission(notes="facts")
    with pytest.raises(PolicyError, match="no decision"):
        queue_dispute_evidence(
            db, PolicyService(db, rulebook, clock), case, seller_approver("s"), submission
        )


def test_DE_01_only_app_policy_calls_the_dispute_write():
    allowed = {
        "app/paypal_client/writes/money.py",  # the write itself
        "app/policy/gateway.py",  # the only real caller
        "app/policy/ports.py",
        "app/policy/evidence.py",
        "app/worker/simulated.py",  # the simulated and routing gateways
    }
    callers = set()
    for path in (ROOT / "app").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            name = getattr(node, "attr", None) or getattr(node, "name", None)
            if name == "provide_dispute_evidence":
                callers.add(str(path.relative_to(ROOT)))
    assert callers <= allowed and "app/policy/evidence.py" in callers


# ---------------------------------------------------------------- DE-02 facts only


def test_DE_02_notes_hold_facts_only(db, clock, pipeline):
    _, order, case = _disputed(db, clock, pipeline)
    decision = db.scalars(select(Decision).where(Decision.case_id == case.id)).one()
    submission = build_submission(db, order, case, decision, VERIFY)
    notes = submission.notes
    assert "9400111202555842" in notes and "DELIVERED" in notes and "TRANSIT" in notes
    assert "QR code packed inside the parcel was scanned" in notes
    assert "Delivered-scan audit of the carrier's data: consistent" in notes
    assert VERIFY in notes
    assert (submission.carrier, submission.tracking_number) == ("USPS", "9400111202555842")
    for private in ("High Street", "Flat 2", "Sam", "94107", "Secret Lane"):
        assert private not in notes  # no address, no name, no buyer free text
    assert len(notes) <= 2000


def test_DE_02_an_unscanned_seal_is_never_mentioned(db, clock, pipeline):
    _, order, case = _disputed(db, clock, pipeline, seal=False)
    decision = db.scalars(select(Decision).where(Decision.case_id == case.id)).one()
    notes = build_submission(db, order, case, decision, VERIFY).notes
    assert "seal" not in notes.lower()


def test_DE_02_unknown_carrier_is_sent_as_other_and_no_shipment_still_works(db, clock, pipeline):
    _, order, case = _disputed(db, clock, pipeline)
    decision = db.scalars(select(Decision).where(Decision.case_id == case.id)).one()
    shipment = db.scalars(select(Shipment).where(Shipment.order_id == order.id)).one()
    shipment.carrier = "shippo"
    assert build_submission(db, order, case, decision, VERIFY).carrier is None
    db.delete(shipment)
    db.flush()
    bare = build_submission(db, order, case, decision, VERIFY)
    assert bare.tracking_number is None and "Carrier:" not in bare.notes


def test_DE_02_accusing_words_never_reach_paypal(db, clock, pipeline, actions):
    seller, order, case = _disputed(db, clock, pipeline)
    db.scalars(
        select(Shipment).where(Shipment.order_id == order.id)
    ).one().tracking_number = "FRAUD-1"
    with pytest.raises(UnsafeEvidence):
        actions.act(case, "send-evidence", seller_approver(seller.id), verify_url=VERIFY)
    assert _evidence_actions(db, case.id) == []


# ---------------------------------------------------------------- DE-03 never twice


def test_DE_03_second_tap_returns_the_first_submission(db, clock, pipeline, actions, signer):
    seller, _, case = _disputed(db, clock, pipeline)
    first = actions.act(case, "send-evidence", seller_approver(seller.id), verify_url=VERIFY)
    second = actions.act(case, "send-evidence", seller_approver(seller.id), verify_url=VERIFY)
    assert first is not None and second is not None and first.id == second.id
    assert len(_jobs(db)) == 1 and len(_evidence_actions(db, case.id)) == 1
    kinds = list(db.scalars(select(Receipt.kind).where(Receipt.case_id == case.id)))
    assert kinds.count("send-evidence") == 1


def test_DE_03_the_policy_itself_refuses_a_second_evidence_action(db, rulebook, clock, pipeline):
    """The guard lives in app.policy, not only in the caller: a direct second call returns the
    first action, even with different notes, and queues nothing new."""
    seller, _, case = _disputed(db, clock, pipeline)
    policy = PolicyService(db, rulebook, clock)
    first = queue_dispute_evidence(
        db, policy, case, seller_approver(seller.id), EvidenceSubmission(notes="first")
    )
    again = queue_dispute_evidence(
        db, policy, case, seller_approver(seller.id), EvidenceSubmission(notes="other")
    )
    assert again.id == first.id and again.request_id == first.request_id
    [job] = _jobs(db)
    assert job.payload["evidence"]["notes"] == "first"


def test_DE_03_retried_job_reuses_the_request_id_and_sends_once(
    db, session_factory, clock, settings, pipeline, actions, fake_paypal
):
    seller, _, case = _disputed(db, clock, pipeline)
    ma = actions.act(case, "send-evidence", seller_approver(seller.id), verify_url=VERIFY)
    assert ma is not None
    db.commit()
    flaky = Flaky(fake_paypal, PayPalServerError(503), PayPalClientError(429, "RATE_LIMIT_REACHED"))
    handler = DisputeEvidenceJobHandler(flaky, clock)
    run_jobs(
        session_factory,
        {DISPUTE_EVIDENCE_JOB: handler},
        clock,
        settings,
        rounds=5,
        advance_s=10_000,
    )
    assert len(flaky.calls) == 3 and set(flaky.calls) == {ma.request_id}
    assert len(fake_paypal.evidence_calls) == 1
    db.expire_all()
    done = db.get(MoneyAction, ma.id)
    assert done is not None and done.status == MoneyActionStatus.COMPLETED
    assert done.paypal_status == SUBMITTED
    # A redelivered job finds the action completed and sends nothing.
    handler.run(db, _jobs(db)[0])
    assert len(fake_paypal.evidence_calls) == 1


def test_DE_03_a_rejected_submission_is_marked_failed_once(
    db, session_factory, clock, settings, pipeline, actions, fake_paypal
):
    seller, _, case = _disputed(db, clock, pipeline)
    ma = actions.act(case, "send-evidence", seller_approver(seller.id), verify_url=VERIFY)
    assert ma is not None
    db.commit()
    flaky = Flaky(fake_paypal, PayPalClientError(422, "UNPROCESSABLE_ENTITY"))
    handler = DisputeEvidenceJobHandler(flaky, clock)
    run_jobs(
        session_factory,
        {DISPUTE_EVIDENCE_JOB: handler},
        clock,
        settings,
        rounds=3,
        advance_s=10_000,
    )
    db.expire_all()
    failed = db.get(MoneyAction, ma.id)
    assert failed is not None and failed.status == MoneyActionStatus.FAILED
    assert len(flaky.calls) == 1 and fake_paypal.evidence_calls == []
    handler.on_failed(db, _jobs(db)[0], RuntimeError("again"))  # already failed: no change
    handler.on_failed(
        db, Job(kind=DISPUTE_EVIDENCE_JOB, payload={"money_action_id": "x"}), RuntimeError()
    )
    assert failed.paypal_response == {"error": "PayPalClientError"}


def test_DE_03_a_dispute_resolved_before_sending_blocks_the_job(
    db, clock, pipeline, actions, fake_paypal
):
    seller, order, case = _disputed(db, clock, pipeline)
    ma = actions.act(case, "send-evidence", seller_approver(seller.id), verify_url=VERIFY)
    assert ma is not None
    db.scalars(select(Dispute).where(Dispute.order_id == order.id)).one().status = "resolved"
    DisputeEvidenceJobHandler(fake_paypal, clock).run(db, _jobs(db)[0])
    assert ma.status == MoneyActionStatus.BLOCKED and fake_paypal.evidence_calls == []


def test_DE_03_missing_money_action_is_permanent(db, clock, fake_paypal):
    handler = DisputeEvidenceJobHandler(fake_paypal, clock)
    with pytest.raises(MissingRecord):
        handler.run(db, Job(kind=DISPUTE_EVIDENCE_JOB, payload={"money_action_id": "missing"}))


# ---------------------------------------------------------------- DE-04 signed receipts


def test_DE_04_each_submission_writes_a_signed_receipt(
    db, clock, pipeline, actions, signer, fake_paypal
):
    seller, _, case = _disputed(db, clock, pipeline)
    ma = actions.act(case, "send-evidence", seller_approver(seller.id), verify_url=VERIFY)
    assert ma is not None
    [job] = _jobs(db)
    DisputeEvidenceJobHandler(fake_paypal, clock, signer).run(db, job)
    receipts = list(
        db.scalars(select(Receipt).where(Receipt.case_id == case.id).order_by(Receipt.seq))
    )
    assert [r.kind for r in receipts][-2:] == ["send-evidence", "evidence"]
    sent = receipts[-1].payload
    [(_, evidence, request_id)] = fake_paypal.evidence_calls
    assert sent["request_id"] == request_id == ma.request_id
    assert sent["notes_sha256"] == hashlib.sha256(evidence.notes.encode()).hexdigest()
    assert verify_chain(db, signer.public_key_b64, case_id=case.id).status == "ok"


def test_DE_04_without_a_signer_no_evidence_receipt(db, clock, pipeline, actions, fake_paypal):
    seller, _, case = _disputed(db, clock, pipeline)
    actions.act(case, "send-evidence", seller_approver(seller.id), verify_url=VERIFY)
    DisputeEvidenceJobHandler(fake_paypal, clock).run(db, _jobs(db)[0])
    kinds = list(db.scalars(select(Receipt.kind).where(Receipt.case_id == case.id)))
    assert "evidence" not in kinds


# ---------------------------------------------------------------- simulated disputes, API


def test_DE_01_seeded_disputes_are_simulated_and_labelled(
    db, clock, pipeline, actions, fake_paypal
):
    seller, _, case = _disputed(db, clock, pipeline, dispute_id="SIM-DISPUTE-1")
    real, sim = FakePayPal(), FakePayPal()
    actions.act(case, "send-evidence", seller_approver(seller.id), verify_url=VERIFY)
    DisputeEvidenceJobHandler(RoutingRefundGateway(real, sim), clock).run(db, _jobs(db)[0])
    assert real.evidence_calls == [] and len(sim.evidence_calls) == 1
    [ma] = _evidence_actions(db, case.id)
    assert ma.paypal_status == SIMULATED_STATUS
    RoutingRefundGateway(real, sim).provide_dispute_evidence(
        "PP-REAL",
        evidence=DisputeEvidence(evidence_type="PROOF_OF_FULFILLMENT", notes="x"),
        request_id="r",
    )
    assert len(real.evidence_calls) == 1


@pytest.fixture
def api(session_factory, settings, clock) -> TestClient:
    services = Services(
        links=LinkSigner(SECRET, clock),
        receipts=ReceiptSigner.generate(),
        orders=None,
        approved=None,
        app_base_url="https://doorstep.example",
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


def test_DE_01_seller_taps_send_evidence_once_in_the_case_page(db, clock, pipeline, api):
    seller, _, case = _disputed(db, clock, pipeline)
    db.commit()
    token = LinkSigner(SECRET, clock).issue(seller.id, scope="seller")
    page = api.get(f"/api/seller/{token}/cases/{case.id}").json()
    assert page["actions"] == ["send-evidence"]
    sent = api.post(f"/api/seller/{token}/cases/{case.id}/send-evidence")
    assert sent.status_code == 200 and sent.json()["money_action_id"]
    page = api.get(f"/api/seller/{token}/cases/{case.id}").json()
    assert page["actions"] == [] and page["money"][0]["kind"] == "evidence"
    job = db.scalars(select(Job).where(Job.kind == DISPUTE_EVIDENCE_JOB)).one()
    assert f"https://doorstep.example/cases/{case.id}/verify" in job.payload["evidence"]["notes"]
    rows = api.get(f"/api/seller/{token}/orders").json()
    assert rows[0]["refund_status"] is None  # evidence is not a refund


def test_DE_01_send_evidence_outside_a_dispute_answers_409(db, clock, pipeline, api):
    seller, _, case = _disputed(db, clock, pipeline)
    case.state = CaseState.AWAITING_SELLER.value
    db.commit()
    token = LinkSigner(SECRET, clock).issue(seller.id, scope="seller")
    assert api.post(f"/api/seller/{token}/cases/{case.id}/send-evidence").status_code == 409


# ---------------------------------------------------------------- audit golden, scoping
# Mutation gate (Nov 5): the money trail is exact, so every row is pinned in full.


def _audit(db, entity_id: str, action: str) -> AuditLog:
    return db.scalars(
        select(AuditLog).where(AuditLog.entity_id == entity_id, AuditLog.action == action)
    ).one()


def _queued(db, rulebook, clock, pipeline, **kw):
    seller, order, case = _disputed(db, clock, pipeline, **kw)
    decision = db.scalars(select(Decision).where(Decision.case_id == case.id)).one()
    ma = queue_dispute_evidence(
        db,
        PolicyService(db, rulebook, clock),
        case,
        seller_approver(seller.id),
        EvidenceSubmission(notes="facts only"),
    )
    return seller, order, case, decision, ma


def test_DE_01_queued_evidence_is_recorded_exactly(db, rulebook, clock, pipeline):
    seller, _, _, decision, ma = _queued(db, rulebook, clock, pipeline)
    assert ma.request_id == f"dispute:PP-D-0001:evidence:{ma.id}"
    assert ma.created_at == clock.now() and ma.decision_id == decision.id
    [job] = _jobs(db)
    assert job.dedupe_key == f"evidence:{ma.id}"
    approval = db.scalars(select(Approval).where(Approval.decision_id == decision.id)).one()
    assert (approval.rule_id, approval.at) == (decision.rule_id, clock.now())
    row = _audit(db, ma.id, "evidence.queued")
    assert (row.actor, row.entity_type, row.before, row.at) == (
        f"seller:{seller.id}",
        "money_action",
        {},
        clock.now(),
    )
    assert row.after == {
        "money_action_id": ma.id,
        "dispute_id": "PP-D-0001",
        "request_id": ma.request_id,
        "rule_id": decision.rule_id,
        "notes": "facts only",
    }


def test_DE_03_each_outcome_writes_its_exact_audit_row(db, rulebook, clock, pipeline, fake_paypal):
    _, _, _, decision, ma = _queued(db, rulebook, clock, pipeline)
    signer = ReceiptSigner.generate()
    DisputeEvidenceJobHandler(fake_paypal, clock, signer).run(db, _jobs(db)[0])
    done = {"dispute_id": "PP-D-0001", "paypal_status": SUBMITTED}
    assert (ma.status, ma.paypal_response, ma.completed_at) == ("completed", done, clock.now())
    row = _audit(db, ma.id, "evidence.completed")
    assert (row.actor, row.entity_type, row.before, row.after, row.at) == (
        "paypal",
        "money_action",
        {"status": "queued"},
        {"status": "completed", **done},
        clock.now(),
    )
    receipt = db.scalars(select(Receipt).where(Receipt.kind == "evidence")).one()
    [(_, evidence, _)] = fake_paypal.evidence_calls
    assert receipt.payload == {
        "money_action_id": ma.id,
        "decision_id": decision.id,
        "dispute_id": "PP-D-0001",
        "request_id": ma.request_id,
        "paypal_status": SUBMITTED,
        "notes_sha256": hashlib.sha256(evidence.notes.encode()).hexdigest(),
    }


def test_DE_03_blocked_and_failed_rows_name_the_policy(db, rulebook, clock, pipeline, fake_paypal):
    _, order, _, _, blocked = _queued(db, rulebook, clock, pipeline)
    db.scalars(select(Dispute).where(Dispute.order_id == order.id)).one().status = "resolved"
    DisputeEvidenceJobHandler(fake_paypal, clock).run(db, _jobs(db)[0])
    row = _audit(db, blocked.id, "evidence.blocked")
    assert (row.actor, row.entity_type, row.before, row.after, row.at) == (
        "policy",
        "money_action",
        {"status": "queued"},
        {"status": "blocked", "blocked": "the dispute is not open"},
        clock.now(),
    )
    _, _, _, _, failed = _queued(db, rulebook, clock, pipeline, dispute_id="PP-D-0002")
    job = next(j for j in _jobs(db) if j.payload["money_action_id"] == failed.id)
    DisputeEvidenceJobHandler(fake_paypal, clock).on_failed(db, job, PayPalClientError(422, "X"))
    row = _audit(db, failed.id, "evidence.failed")
    assert (row.actor, row.before, row.after) == (
        "policy",
        {"status": "queued"},
        {"status": "failed", "error": "PayPalClientError"},
    )


def test_DE_03_a_refund_or_another_cases_evidence_is_not_this_evidence(
    db, rulebook, clock, pipeline
):
    seller, _, _, _, first = _queued(db, rulebook, clock, pipeline)
    _, _, other_case, _, other = _queued(db, rulebook, clock, pipeline, dispute_id="PP-D-0002")
    assert other.id != first.id and other.case_id == other_case.id
    # A refund recorded on the same decision is not the evidence submission.
    _, _, third_case = _disputed(db, clock, pipeline, dispute_id="PP-D-0003")
    third_decision = db.scalars(select(Decision).where(Decision.case_id == third_case.id)).one()
    refund = add_money_action(db, third_case, third_decision, status="queued")
    evidence = queue_dispute_evidence(
        db,
        PolicyService(db, rulebook, clock),
        third_case,
        seller_approver(seller.id),
        EvidenceSubmission(notes="facts only"),
    )
    assert evidence.id != refund.id and evidence.kind == MoneyActionKind.EVIDENCE


def test_DE_01_another_orders_open_dispute_does_not_count(db, rulebook, clock, pipeline):
    seller, order, case = _disputed(db, clock, pipeline)
    db.scalars(select(Dispute).where(Dispute.order_id == order.id)).one().status = "resolved"
    _disputed(db, clock, pipeline, dispute_id="PP-D-0002")  # someone else's dispute is open
    with pytest.raises(PolicyError, match="no open PayPal dispute"):
        queue_dispute_evidence(
            db,
            PolicyService(db, rulebook, clock),
            case,
            seller_approver(seller.id),
            EvidenceSubmission(notes="facts only"),
        )


def test_DE_01_a_dispute_waiting_for_evidence_needs_the_seller(db, clock, pipeline, api):
    seller, _, case = _disputed(db, clock, pipeline)
    db.commit()
    token = LinkSigner(SECRET, clock).issue(seller.id, scope="seller")
    [row] = api.get(f"/api/seller/{token}/orders").json()
    assert row["case_state"] == "DISPUTE_EVIDENCE" and row["needs_action"] is True
    api.post(f"/api/seller/{token}/cases/{case.id}/send-evidence")
    [row] = api.get(f"/api/seller/{token}/orders").json()
    assert row["needs_action"] is False  # sent: PayPal decides now
