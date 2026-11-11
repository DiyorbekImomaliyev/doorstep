"""PO-16: a refund Doorstep marked failed, that PayPal sent after all, is recorded as sent.

Found by the Nov 11 review: when the refund call timed out until its retries ran out, the money
action was marked FAILED and the case held for a person. If PayPal had in fact made the refund, its
later PAYMENT.CAPTURE.REFUNDED webhook found our money action by custom_id but changed nothing,
because only a PENDING refund could be confirmed. No second refund could follow (the live re-check
sees PayPal's refunded total), but the record said FAILED while the money had moved.

Now PayPal's COMPLETED is the truth for any refund of ours that we had marked failed: the money
action becomes COMPLETED with PayPal's refund id, and a held case settles. Anything short of
COMPLETED changes nothing, and a case a person already closed stays closed.
"""

from __future__ import annotations

from sqlalchemy import select

from app.orders.enums import CaseState, MoneyActionStatus
from app.orders.models import AuditLog, MoneyAction
from app.orders.state import transition_case
from app.policy.service import REFUND_JOB, PolicyService, RefundJobHandler
from app.worker.models import Job
from tests.unit import test_po_13_refund_status as po13

# The PO-13 fixtures, shared: a queued refund, the PayPal fake and the refund job runner.
paypal, queued, run_refund = po13.paypal, po13.queued, po13.run_refund
_actions, _refunded_event = po13._actions, po13._refunded_event


def _timed_out(db, paypal, rulebook, clock, queued):
    """The worker's view after the refund call timed out until its retries ran out."""
    order, case, ma = queued
    [job] = db.scalars(select(Job).where(Job.kind == REFUND_JOB)).all()
    RefundJobHandler(paypal, rulebook, clock).on_failed(db, job, TimeoutError("timed out"))
    db.flush()
    assert ma.status == MoneyActionStatus.FAILED.value
    assert case.state == CaseState.HOLD_FOR_REVIEW.value
    return order, case, ma


def test_PO_16_paypal_sent_a_refund_we_marked_failed_and_the_record_follows(
    queued, paypal, db, rulebook, clock
):
    order, case, ma = _timed_out(db, paypal, rulebook, clock, queued)
    _refunded_event(
        db, rulebook, clock, order, refund_id="R-LATE", status="COMPLETED", custom_id=ma.id
    )
    assert (ma.status, ma.paypal_status) == (MoneyActionStatus.COMPLETED.value, "COMPLETED")
    assert ma.paypal_refund_id == "R-LATE"
    assert ma.completed_at == clock.now()
    assert case.state == CaseState.SETTLED.value
    rows = db.scalars(select(MoneyAction).where(MoneyAction.order_id == order.id)).all()
    assert [r.id for r in rows] == [ma.id]  # ours, never recorded as an outside refund


def test_PO_16_the_audit_shows_failed_then_completed(queued, paypal, db, rulebook, clock):
    order, _, ma = _timed_out(db, paypal, rulebook, clock, queued)
    _refunded_event(
        db, rulebook, clock, order, refund_id="R-LATE", status="COMPLETED", custom_id=ma.id
    )
    assert _actions(db, ma.id)[-2:] == ["refund.failed", "refund.completed"]
    row = db.scalars(
        select(AuditLog).where(AuditLog.entity_id == ma.id, AuditLog.action == "refund.completed")
    ).one()
    assert row.before == {"status": MoneyActionStatus.FAILED.value}


def test_PO_16_confirm_refund_says_it_completed(queued, paypal, db, rulebook, clock):
    _, _, ma = _timed_out(db, paypal, rulebook, clock, queued)
    policy = PolicyService(db, rulebook, clock)
    assert policy.confirm_refund(ma, "COMPLETED", paypal_refund_id="R-LATE") is True
    assert policy.confirm_refund(ma, "COMPLETED", paypal_refund_id="R-LATE") is False  # once
    assert _actions(db, ma.id).count("refund.completed") == 1


def test_PO_16_a_refund_paypal_failed_then_completed_is_completed(
    queued, run_refund, paypal, db, rulebook, clock
):
    """PayPal's own FAILED answer, later contradicted by PayPal: the latest word on money wins."""
    order, case, ma = queued
    paypal.answer = "FAILED"
    run_refund()
    refund_id = ma.paypal_refund_id
    assert ma.status == MoneyActionStatus.FAILED.value and refund_id
    _refunded_event(db, rulebook, clock, order, refund_id=refund_id, status="COMPLETED")
    assert ma.status == MoneyActionStatus.COMPLETED.value and ma.paypal_refund_id == refund_id
    assert case.state == CaseState.SETTLED.value


def test_PO_16_anything_short_of_completed_changes_nothing(queued, paypal, db, rulebook, clock):
    _, case, ma = _timed_out(db, paypal, rulebook, clock, queued)
    policy = PolicyService(db, rulebook, clock)
    for status in ("PENDING", "FAILED", "CANCELLED", "SOMETHING_NEW"):
        assert policy.confirm_refund(ma, status, paypal_refund_id="R-X") is False
    assert ma.status == MoneyActionStatus.FAILED.value
    assert ma.paypal_refund_id is None
    assert case.state == CaseState.HOLD_FOR_REVIEW.value


def test_PO_16_a_case_a_person_closed_stays_closed(queued, paypal, db, rulebook, clock):
    order, case, ma = _timed_out(db, paypal, rulebook, clock, queued)
    transition_case(db, case, CaseState.CLOSED, actor="seller", reason="closed by a person")
    _refunded_event(
        db, rulebook, clock, order, refund_id="R-LATE", status="COMPLETED", custom_id=ma.id
    )
    assert ma.status == MoneyActionStatus.COMPLETED.value  # the money record follows PayPal
    assert case.state == CaseState.CLOSED.value  # the person's decision on the case stands


def test_PO_16_a_known_refund_id_is_kept(queued, paypal, db, rulebook, clock):
    _, _, ma = _timed_out(db, paypal, rulebook, clock, queued)
    ma.paypal_refund_id = "R-FIRST"
    PolicyService(db, rulebook, clock).confirm_refund(ma, "COMPLETED", paypal_refund_id="R-OTHER")
    assert ma.paypal_refund_id == "R-FIRST"


def test_PO_16_a_refund_id_is_optional(queued, paypal, db, rulebook, clock):
    _, _, ma = _timed_out(db, paypal, rulebook, clock, queued)
    assert PolicyService(db, rulebook, clock).confirm_refund(ma, "COMPLETED") is True
    assert ma.paypal_refund_id is None and ma.status == MoneyActionStatus.COMPLETED.value
