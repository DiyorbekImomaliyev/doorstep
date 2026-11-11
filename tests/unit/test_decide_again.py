"""A held case whose refund was blocked or failed has a way forward.

Before this, approve() on such a case returned the old blocked money action and did nothing,
decline works only from AWAITING_SELLER, and HOLD_FOR_REVIEW -> GATHERING_EVIDENCE (SPEC section 8)
was never called: the "money waiting for a person" alert on /healthz had no button that cleared it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.ai.explainer import Explainer
from app.api.views import case_view
from app.cases.engine import ClaimReason
from app.cases.pipeline import CasePipeline
from app.orders.enums import ApproverKind, CaseState, MoneyActionStatus
from app.orders.models import Case, Decision, Evidence
from app.policy.service import (
    REFUND_JOB,
    Approver,
    PolicyService,
    PolicyStateError,
    RefundJobHandler,
)
from app.proof.receipts import ReceiptSigner
from tests.factories import money_actions
from tests.helpers import money, run_jobs
from tests.unit.test_sl_seal import _shipped

REVIEWER = Approver(kind=ApproverKind.REVIEWER, id="reviewer-1")


@pytest.fixture
def pipeline(db, rulebook, clock, settings) -> CasePipeline:
    return CasePipeline(db, rulebook, clock, settings, Explainer(), ReceiptSigner.generate())


@pytest.fixture
def run_refunds(session_factory, fake_paypal, rulebook, clock, settings, db):
    def run() -> None:
        db.commit()
        handler = RefundJobHandler(fake_paypal, rulebook, clock)
        run_jobs(session_factory, {REFUND_JOB: handler}, clock, settings)
        db.expire_all()

    return run


@pytest.fixture
def held(db, clock, fake_paypal, pipeline, run_refunds) -> Case:
    """An auto-refund the pre-refund re-check blocked: new evidence arrived after the decision."""
    _, order, _ = _shipped(db, clock)
    fake_paypal.add_capture(order.paypal_order_id, order.capture_id, money(order.amount_minor))
    report = pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")
    assert report.route == CaseState.AUTO_REFUND
    db.add(Evidence(order_id=order.id, kind="seller_note", uri="note://late"))
    run_refunds()
    case = db.get(Case, report.case_id)
    assert case is not None and case.state == CaseState.HOLD_FOR_REVIEW
    [ma] = money_actions(db, case_id=case.id)
    assert ma.status == MoneyActionStatus.BLOCKED
    return case


def test_approving_a_blocked_refund_says_so(db, rulebook, clock, held):
    with pytest.raises(PolicyStateError, match="blocked; decide again"):
        PolicyService(db, rulebook, clock).approve(held, REVIEWER)


def test_a_held_case_with_a_stopped_refund_offers_only_a_fresh_decision(db, held):
    assert case_view(db, held).actions == ["re-check"]


def test_a_held_case_without_money_offers_approve_and_a_fresh_decision(db, clock, pipeline):
    _, order, _ = _shipped(db, clock, amount_minor=4000)
    report = pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")
    case = db.get(Case, report.case_id)
    case.state = CaseState.HOLD_FOR_REVIEW.value  # e.g. a contradiction: no money action yet
    for ma in money_actions(db, case_id=case.id):
        db.delete(ma)
    db.flush()
    assert case_view(db, case).actions == ["approve", "re-check"]


def test_a_fresh_decision_refunds_once(db, held, pipeline, run_refunds, fake_paypal):
    report = pipeline.redecide(held, actor="reviewer:reviewer-1")
    assert report.route == CaseState.AUTO_REFUND
    decisions = db.scalar(select(func.count()).where(Decision.case_id == held.id))
    assert decisions == 2
    run_refunds()
    statuses = sorted(ma.status for ma in money_actions(db, case_id=held.id))
    assert statuses == sorted([MoneyActionStatus.BLOCKED, MoneyActionStatus.COMPLETED])
    assert len(fake_paypal.refund_calls) == 1
    assert db.get(Case, held.id).state == CaseState.SETTLED


def test_a_refund_that_landed_unrecorded_is_never_repeated(
    db, held, pipeline, run_refunds, fake_paypal
):
    """A FAILED refund may still have reached PayPal; the new action meets the live re-check."""
    [capture] = fake_paypal.captures.values()
    fake_paypal.external_refund(capture.capture_id, 1000)  # PayPal holds a refund we lack
    pipeline.redecide(held, actor="reviewer:reviewer-1")
    run_refunds()
    assert fake_paypal.refund_calls == []
    newest = money_actions(db, case_id=held.id)[-1]
    assert newest.status == MoneyActionStatus.BLOCKED
    assert newest.paypal_response["blocked_code"] == "unknown_refund_at_paypal"


def _held_with_a_queued_refund(db, clock, pipeline, rulebook):
    """Held for a reviewer, who approves: the refund job is queued and the case stays held."""
    _, order, _ = _shipped(db, clock, amount_minor=4000)
    report = pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")
    case = db.get(Case, report.case_id)
    case.state = CaseState.HOLD_FOR_REVIEW.value
    for ma in money_actions(db, case_id=case.id):
        db.delete(ma)  # as if the decision had held the case without queuing money
    db.flush()
    queued = PolicyService(db, rulebook, clock).approve(case, REVIEWER)
    assert queued is not None and queued.status == MoneyActionStatus.QUEUED
    assert case.state == CaseState.HOLD_FOR_REVIEW
    return case


def test_no_fresh_decision_while_a_refund_is_on_its_way(db, clock, pipeline, rulebook):
    """The queued job would land under a newer decision."""
    case = _held_with_a_queued_refund(db, clock, pipeline, rulebook)
    assert case_view(db, case).actions == []
    with pytest.raises(PolicyStateError, match="refund is on its way"):
        pipeline.redecide(case, actor="reviewer:reviewer-1")
    assert case.state == CaseState.HOLD_FOR_REVIEW


def test_only_a_held_case_can_be_decided_again(db, clock, pipeline):
    _, order, _ = _shipped(db, clock)
    report = pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")
    with pytest.raises(PolicyStateError, match="cannot be decided again"):
        pipeline.redecide(db.get(Case, report.case_id), actor="reviewer:reviewer-1")


# ---------------------------------------------------------------- the seller's button (HTTP)


@pytest.fixture
def api(session_factory, settings, clock):
    from app.api.deps import Services
    from app.security.links import LinkSigner
    from tests.fakes_journey import RecordingPayPal
    from tests.unit.test_api_journey import SECRET, _client

    paypal = RecordingPayPal()
    services = Services(
        links=LinkSigner(SECRET, clock),
        receipts=ReceiptSigner.generate(),
        orders=paypal,
        approved=paypal,
    )
    return _client(session_factory, settings, clock, services)


def test_the_seller_can_ask_for_a_fresh_decision_on_a_held_case(api, session_factory):
    from tests.unit.test_api_journey import _by_key

    demo = api.post("/demo/seed?scratch=1").json()
    seller, held = demo["seller_token"], _by_key(demo, "empty-box")["case_id"]
    assert api.get(f"/api/seller/{seller}/cases/{held}").json()["actions"] == [
        "approve",
        "re-check",
    ]
    out = api.post(f"/api/seller/{seller}/cases/{held}/re-check")
    assert out.status_code == 200
    assert out.json()["state"] == CaseState.HOLD_FOR_REVIEW  # the contradiction still stands
    with session_factory() as s:
        assert s.scalar(select(func.count()).where(Decision.case_id == held)) == 2
    diyorbek = demo["buyers"]["sam-jacket"]
    # AWAITING_SELLER
    waiting = api.post(f"/api/buyer/{diyorbek}/not-here", json={}).json()["case_id"]
    again = api.post(f"/api/seller/{seller}/cases/{waiting}/re-check")
    assert again.status_code == 409


def test_a_case_held_twice_counts_once_on_the_health_signal(db, held, pipeline, run_refunds, clock):
    from app.ops.signals import pipeline_signals

    pipeline.redecide(held, actor="reviewer:reviewer-1")
    db.add(Evidence(order_id=held.order_id, kind="seller_note", uri="note://later"))
    run_refunds()  # blocked again: two stopped money actions, one held case
    assert db.get(Case, held.id).state == CaseState.HOLD_FOR_REVIEW
    assert len(money_actions(db, case_id=held.id)) == 2
    assert pipeline_signals(db, clock.now()).money_waiting_for_a_person == 1
