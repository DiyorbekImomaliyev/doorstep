"""PO-13: a refund counts as sent only when PayPal says COMPLETED.

Found by the Nov 7 review: any 2xx answer to the refund call marked the refund completed and
settled the case, even when PayPal answered PENDING (an eCheck-funded refund), FAILED or
CANCELLED. Now:
- COMPLETED settles the case, as before.
- PENDING waits: the money action is "pending" and counts toward the refunded balance (so the same
  money can never be refunded twice), and the case stays open until PayPal confirms.
- FAILED, CANCELLED or a status Doorstep does not know: the refund failed, and a person reviews.
A pending refund is confirmed by PayPal's later answer, from the PAYMENT.CAPTURE.REFUNDED webhook
or from the worker's sweep, which reads the capture's refunds. Our own refund is recognised by its
custom_id (our money action id) as well as its PayPal id, so a webhook that races the refund job
is never recorded as a refund made outside Doorstep.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.orders.enums import CaseState, MoneyActionStatus
from app.orders.models import AuditLog, MoneyAction
from app.paypal_client.types import CaptureState, RefundResult
from app.policy.service import REFUND_JOB, PolicyService, RefundJobHandler
from app.worker.paypal_events import paypal_inbox_handlers
from tests.factories import make_case, make_order, make_seller, money_actions
from tests.fakes import FakePayPal
from tests.helpers import engine_result, facts_for, money, run_jobs
from tests.unit.test_wh_paypal_events import _row


class StatusPayPal(FakePayPal):
    """The fake PayPal, answering each new refund with a chosen status, which can change later."""

    def __init__(self) -> None:
        super().__init__()
        self.answer = "COMPLETED"
        self.status_of: dict[str, str] = {}

    def refund_capture(self, capture_id, *, amount, request_id, custom_id=None) -> RefundResult:
        result = super().refund_capture(
            capture_id, amount=amount, request_id=request_id, custom_id=custom_id
        )
        self.status_of.setdefault(result.refund_id, self.answer)
        return RefundResult(
            refund_id=result.refund_id, status=self.status_of[result.refund_id], raw=result.raw
        )

    def get_capture_state(self, paypal_order_id: str, capture_id: str) -> CaptureState:
        state = super().get_capture_state(paypal_order_id, capture_id)
        refunds = [
            r.model_copy(update={"status": self.status_of.get(r.refund_id, r.status)})
            for r in state.refunds
        ]
        return state.model_copy(update={"refunds": refunds})


@pytest.fixture
def paypal() -> StatusPayPal:
    return StatusPayPal()


@pytest.fixture
def queued(db, paypal, rulebook, clock):
    order = make_order(db, make_seller(db), amount_minor=4000)
    paypal.add_capture(order.paypal_order_id, order.capture_id, money(4000))
    case = make_case(db, order)
    PolicyService(db, rulebook, clock).decide(case, engine_result(), facts_for(order, None))
    db.commit()
    [ma] = money_actions(db, case_id=case.id)
    return order, case, ma


@pytest.fixture
def run_refund(session_factory, paypal, rulebook, clock, settings, db):
    def run() -> None:
        db.commit()
        handler = RefundJobHandler(paypal, rulebook, clock)
        run_jobs(session_factory, {REFUND_JOB: handler}, clock, settings, rounds=1)
        db.expire_all()

    return run


def _actions(db, ma_id: str) -> list[str]:
    rows = db.scalars(select(AuditLog).where(AuditLog.entity_id == ma_id).order_by(AuditLog.at))
    return [r.action for r in rows]


# ---------------------------------------------------------------- the refund call's answer


def test_PO_13_a_completed_refund_settles_the_case(queued, run_refund, db):
    _, case, ma = queued
    run_refund()
    assert (ma.status, ma.paypal_status) == (MoneyActionStatus.COMPLETED.value, "COMPLETED")
    assert case.state == CaseState.SETTLED.value


def test_PO_13_a_pending_refund_waits_and_the_case_stays_open(queued, run_refund, paypal, db):
    _, case, ma = queued
    before = case.state
    paypal.answer = "PENDING"
    run_refund()
    assert (ma.status, ma.paypal_status) == (MoneyActionStatus.PENDING.value, "PENDING")
    assert ma.paypal_refund_id and ma.completed_at is None
    assert case.state == before  # not settled until PayPal confirms
    assert "refund.pending" in _actions(db, ma.id)


def test_PO_13_a_pending_refund_counts_toward_the_refunded_balance(
    queued, run_refund, paypal, db, rulebook, clock
):
    order, _, _ = queued
    paypal.answer = "PENDING"
    run_refund()
    remaining = PolicyService(db, rulebook, clock).remaining_refundable(order)
    assert remaining.amount_minor == 0  # the same money can never go out twice


@pytest.mark.parametrize("status", ["FAILED", "CANCELLED", "SOMETHING_NEW"])
def test_PO_13_a_refund_paypal_did_not_complete_goes_to_a_person(
    queued, run_refund, paypal, db, status
):
    _, case, ma = queued
    paypal.answer = status
    run_refund()
    assert (ma.status, ma.paypal_status) == (MoneyActionStatus.FAILED.value, status)
    assert case.state == CaseState.HOLD_FOR_REVIEW.value
    assert "refund.failed" in _actions(db, ma.id)


# ---------------------------------------------------------------- confirming a pending refund


@pytest.fixture
def pending(queued, run_refund, paypal):
    paypal.answer = "PENDING"
    run_refund()
    return queued


def test_PO_13_paypal_completes_a_pending_refund_and_the_case_settles(pending, db, rulebook, clock):
    _, case, ma = pending
    settled = PolicyService(db, rulebook, clock).confirm_refund(ma, "COMPLETED")
    assert settled is True
    assert (ma.status, ma.paypal_status) == (MoneyActionStatus.COMPLETED.value, "COMPLETED")
    assert ma.completed_at == clock.now()
    assert case.state == CaseState.SETTLED.value
    assert "refund.completed" in _actions(db, ma.id)


@pytest.mark.parametrize("status", ["FAILED", "CANCELLED"])
def test_PO_13_a_pending_refund_paypal_then_fails_goes_to_a_person(
    pending, db, rulebook, clock, status
):
    _, case, ma = pending
    assert PolicyService(db, rulebook, clock).confirm_refund(ma, status) is False
    assert (ma.status, ma.paypal_status) == (MoneyActionStatus.FAILED.value, status)
    assert case.state == CaseState.HOLD_FOR_REVIEW.value


def test_PO_13_still_pending_changes_nothing(pending, db, rulebook, clock):
    _, case, ma = pending
    before = case.state
    assert PolicyService(db, rulebook, clock).confirm_refund(ma, "PENDING") is False
    assert ma.status == MoneyActionStatus.PENDING.value and case.state == before


def test_PO_13_confirming_twice_or_a_finished_refund_changes_nothing(pending, db, rulebook, clock):
    _, _, ma = pending
    policy = PolicyService(db, rulebook, clock)
    assert policy.confirm_refund(ma, "COMPLETED") is True
    assert policy.confirm_refund(ma, "FAILED") is False  # a completed refund stays completed
    assert ma.status == MoneyActionStatus.COMPLETED.value
    assert _actions(db, ma.id).count("refund.completed") == 1


# ---------------------------------------------------------------- the webhook


def _refunded_event(db, rulebook, clock, order, *, refund_id, status, custom_id=None):
    resource = {
        "id": refund_id,
        "status": status,
        "amount": {"currency_code": "GBP", "value": "40.00"},
        "links": [{"rel": "up", "href": f"https://api/v2/payments/captures/{order.capture_id}"}],
    }
    if custom_id is not None:
        resource["custom_id"] = custom_id
    handler = paypal_inbox_handlers(rulebook, clock)[("paypal", "PAYMENT.CAPTURE.REFUNDED")]
    handler(db, _row("PAYMENT.CAPTURE.REFUNDED", resource))
    db.flush()


def test_PO_13_the_webhook_completes_our_pending_refund(pending, db, rulebook, clock):
    order, case, ma = pending
    _refunded_event(db, rulebook, clock, order, refund_id=ma.paypal_refund_id, status="COMPLETED")
    assert ma.status == MoneyActionStatus.COMPLETED.value
    assert case.state == CaseState.SETTLED.value
    assert len(money_actions(db, case_id=case.id)) == 1


def test_PO_13_a_webhook_racing_the_refund_job_is_not_an_outside_refund(
    queued, db, rulebook, clock
):
    """PayPal sent the refund, but the job has not recorded its id yet: the custom_id says ours."""
    order, _, ma = queued
    _refunded_event(
        db, rulebook, clock, order, refund_id="R-RACE", status="COMPLETED", custom_id=ma.id
    )
    rows = db.scalars(select(MoneyAction).where(MoneyAction.order_id == order.id)).all()
    assert [r.id for r in rows] == [ma.id]  # no "external" refund recorded
    assert ma.status == MoneyActionStatus.QUEUED.value  # the job records it with its own answer


def test_PO_13_a_refund_made_outside_doorstep_is_still_recorded(queued, db, rulebook, clock):
    order, _, _ = queued
    _refunded_event(db, rulebook, clock, order, refund_id="REF-EXT-9", status="COMPLETED")
    external = db.scalars(
        select(MoneyAction).where(MoneyAction.order_id == order.id, MoneyAction.external)
    ).all()
    assert [e.paypal_refund_id for e in external] == ["REF-EXT-9"]


# ---------------------------------------------------------------- the sweep


def test_PO_13_the_sweep_reads_paypal_and_completes_a_pending_refund(
    pending, paypal, db, rulebook, clock
):
    _, case, ma = pending
    policy = PolicyService(db, rulebook, clock)
    assert policy.sweep_pending_refunds(paypal) == []  # PayPal still says PENDING
    paypal.status_of[ma.paypal_refund_id] = "COMPLETED"
    assert policy.sweep_pending_refunds(paypal) == [ma.id]
    assert ma.status == MoneyActionStatus.COMPLETED.value
    assert case.state == CaseState.SETTLED.value
    assert policy.sweep_pending_refunds(paypal) == []  # nothing left to confirm


def test_PO_13_the_sweep_leaves_a_refund_paypal_does_not_list(pending, paypal, db, rulebook, clock):
    order, _, ma = pending
    paypal.captures[order.capture_id].refunds.clear()
    assert PolicyService(db, rulebook, clock).sweep_pending_refunds(paypal) == []
    assert ma.status == MoneyActionStatus.PENDING.value  # never guess: it stays for the next sweep


# ---------------------------------------------------------------- what people see


def test_PO_13_buyer_and_seller_see_a_pending_refund_as_pending(pending, db):
    from app.api.views import buyer_outcome, case_view

    order, case, _ = pending
    message = buyer_outcome(db, order, case)
    assert message is not None and "still processing" in message
    assert "was sent to your" not in message
    view = case_view(db, case)
    assert view.actions == []  # nothing to approve twice while PayPal finishes it
    assert [m.status for m in view.money if m.kind == "refund"] == ["pending"]


# ---------------------------------------------------------------- mutation gaps


def test_PO_13_a_failed_refund_holds_the_case_with_its_reason(queued, run_refund, paypal, db):
    _, case, _ = queued
    paypal.answer = "FAILED"
    run_refund()
    actions = db.scalars(select(AuditLog.action).where(AuditLog.entity_id == case.id)).all()
    assert "transition: refund not completed" in actions


def test_PO_13_a_confirmed_refund_audit_names_who_approved_it(pending, db, rulebook, clock):
    _, _, ma = pending
    PolicyService(db, rulebook, clock).confirm_refund(ma, "COMPLETED")
    row = db.scalars(
        select(AuditLog).where(AuditLog.entity_id == ma.id, AuditLog.action == "refund.completed")
    ).one()
    assert row.after["approver"] is not None


class CountingPayPal(StatusPayPal):
    def __init__(self) -> None:
        super().__init__()
        self.reads: list[tuple[str, str]] = []

    def get_capture_state(self, paypal_order_id: str, capture_id: str) -> CaptureState:
        self.reads.append((paypal_order_id, capture_id))
        return super().get_capture_state(paypal_order_id, capture_id)


def test_PO_13_the_sweep_reads_the_order_and_capture_of_the_refund(pending, db, rulebook, clock):
    order, _, _ = pending
    counting = CountingPayPal()
    counting.captures = {}
    counting.add_capture(order.paypal_order_id, order.capture_id, money(4000))
    PolicyService(db, rulebook, clock).sweep_pending_refunds(counting)
    assert counting.reads == [(order.paypal_order_id, order.capture_id)]


def test_PO_13_the_sweep_matches_our_refund_not_another_on_the_capture(
    pending, paypal, db, rulebook, clock
):
    from tests.fakes import FakeRefund

    order, case, ma = pending
    refunds = paypal.captures[order.capture_id].refunds
    refunds.insert(0, FakeRefund(None, 100, "EXT-OTHER", external=True))  # listed first
    paypal.status_of["EXT-OTHER"] = "PENDING"
    paypal.status_of[ma.paypal_refund_id] = "COMPLETED"
    assert PolicyService(db, rulebook, clock).sweep_pending_refunds(paypal) == [ma.id]
    assert case.state == CaseState.SETTLED.value


def test_PO_13_the_sweep_finds_our_refund_by_its_id_alone(pending, paypal, db, rulebook, clock):
    order, _, ma = pending
    [ours] = paypal.captures[order.capture_id].refunds
    ours.custom_id = None  # PayPal did not echo our custom_id back
    paypal.status_of[ma.paypal_refund_id] = "COMPLETED"
    assert PolicyService(db, rulebook, clock).sweep_pending_refunds(paypal) == [ma.id]


def test_PO_13_a_custom_id_from_another_order_is_not_ours(queued, db, rulebook, clock):
    _, _, ma = queued
    other = make_order(db, make_seller(db), amount_minor=4000)
    _refunded_event(
        db, rulebook, clock, other, refund_id="R-ELSEWHERE", status="COMPLETED", custom_id=ma.id
    )
    [recorded] = db.scalars(select(MoneyAction).where(MoneyAction.order_id == other.id)).all()
    assert recorded.external and recorded.paypal_refund_id == "R-ELSEWHERE"
    assert ma.status == MoneyActionStatus.QUEUED.value


def test_PO_13_the_database_refuses_a_paypal_refund_id_twice(db, queued):
    from sqlalchemy.exc import IntegrityError

    order, _, ma = queued
    ma.paypal_refund_id = "R-ONCE"
    db.flush()
    db.add(
        MoneyAction(
            order_id=order.id,
            seller_id=order.seller_id,
            kind="refund",
            amount_minor=1,
            currency="GBP",
            status="completed",
            external=True,
            paypal_refund_id="R-ONCE",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_PO_13_many_money_actions_may_have_no_refund_id_yet(db, queued):
    order, _, _ = queued
    for _ in range(2):
        db.add(
            MoneyAction(
                order_id=order.id,
                seller_id=order.seller_id,
                kind="evidence",
                amount_minor=0,
                currency="GBP",
                status="queued",
            )
        )
    db.flush()
