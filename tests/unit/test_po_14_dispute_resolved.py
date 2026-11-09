"""PO-14: when PayPal resolves a dispute, the case it was waiting on is finished.

Found by the Nov 7 review: a resolved dispute was recorded, but its case stayed in
DISPUTE_EVIDENCE for ever, still asking the seller to send evidence. PayPal decides a dispute, so
Doorstep closes the case when PayPal says it is resolved, whatever the outcome. The audit keeps
PayPal's outcome code word for word; Doorstep does not interpret it.
"""

from __future__ import annotations

from sqlalchemy import select

from app.orders.enums import CaseState
from app.orders.models import AuditLog
from app.worker.paypal_events import paypal_inbox_handlers
from tests.factories import make_case, make_order, make_seller
from tests.unit.test_wh_paypal_events import _row


def _event(db, rulebook, clock, event_type, order, **extra):
    resource = {
        "dispute_id": "PP-D-14",
        "status": "OPEN",
        "disputed_transactions": [{"seller_transaction_id": order.capture_id}],
        **extra,
    }
    paypal_inbox_handlers(rulebook, clock)[("paypal", event_type)](db, _row(event_type, resource))
    db.flush()


def _case_in_dispute(db, rulebook, clock):
    order = make_order(db, make_seller(db))
    case = make_case(db, order, state=CaseState.DISPUTE_EVIDENCE)
    _event(db, rulebook, clock, "CUSTOMER.DISPUTE.CREATED", order)
    return order, case


def test_PO_14_a_resolved_dispute_closes_its_case(db, rulebook, clock):
    order, case = _case_in_dispute(db, rulebook, clock)
    _event(
        db,
        rulebook,
        clock,
        "CUSTOMER.DISPUTE.RESOLVED",
        order,
        status="RESOLVED",
        dispute_outcome={"outcome_code": "SOME_OUTCOME"},
    )
    assert case.state == CaseState.CLOSED.value
    actions = db.scalars(select(AuditLog.action).where(AuditLog.entity_id == case.id)).all()
    assert "transition: dispute resolved: SOME_OUTCOME" in actions
    assert all(len(a) <= 64 for a in actions)  # the audit action column is 64 characters


def test_PO_14_an_update_that_is_not_resolved_leaves_the_case(db, rulebook, clock):
    order, case = _case_in_dispute(db, rulebook, clock)
    _event(db, rulebook, clock, "CUSTOMER.DISPUTE.UPDATED", order, status="UNDER_REVIEW")
    assert case.state == CaseState.DISPUTE_EVIDENCE.value


def test_PO_14_a_case_not_waiting_on_the_dispute_is_left_alone(db, rulebook, clock):
    order = make_order(db, make_seller(db))
    case = make_case(db, order, state=CaseState.SETTLED)
    _event(db, rulebook, clock, "CUSTOMER.DISPUTE.RESOLVED", order, status="RESOLVED")
    assert case.state == CaseState.SETTLED.value


def test_PO_14_a_resolved_dispute_with_no_case_is_just_recorded(db, rulebook, clock):
    order = make_order(db, make_seller(db))
    _event(db, rulebook, clock, "CUSTOMER.DISPUTE.RESOLVED", order, status="RESOLVED")
    assert order.state != "RESOLVED"


def test_PO_14_a_long_outcome_code_still_fits_the_audit(db, rulebook, clock):
    order, case = _case_in_dispute(db, rulebook, clock)
    code = "X" * 80
    _event(
        db,
        rulebook,
        clock,
        "CUSTOMER.DISPUTE.RESOLVED",
        order,
        status="RESOLVED",
        dispute_outcome={"outcome_code": code},
    )
    assert case.state == CaseState.CLOSED.value
    actions = db.scalars(select(AuditLog.action).where(AuditLog.entity_id == case.id)).all()
    assert all(len(a) <= 64 for a in actions)


def test_PO_14_seller_and_buyer_are_told_paypal_decided(db, rulebook, clock):
    from app.api.views import buyer_outcome, case_view

    order, case = _case_in_dispute(db, rulebook, clock)
    _event(db, rulebook, clock, "CUSTOMER.DISPUTE.RESOLVED", order, status="RESOLVED")
    assert case_view(db, case).dispute_resolved is True
    assert "PayPal resolved your dispute" in (buyer_outcome(db, order, case) or "")
