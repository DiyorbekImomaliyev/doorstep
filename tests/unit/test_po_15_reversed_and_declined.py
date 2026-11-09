"""PO-15 and CK-11: PayPal takes back or declines a payment after the fact.

From the Nov 9 gap audit. Shapes from PayPal's own webhook simulator
(POST /v1/notifications/simulate-event, sandbox, Nov 9):
- PAYMENT.CAPTURE.REVERSED is a refund resource (id, amount, status COMPLETED) whose "up" link names
  the capture: PayPal returned the money to the buyer outside Doorstep.
- PAYMENT.CAPTURE.DECLINED (Payments v2) and PAYMENT.CAPTURE.DENIED are capture resources with
  status DECLINED: a capture that was pending did not go through.

PO-15: a reversal is recorded as money already back with the buyer, so Doorstep can never pay it
twice, and a case that could still pay out goes to a person (HOLD_FOR_REVIEW). Holding accuses no
one (rule 7). A case PayPal is deciding in a dispute, or one already finished, is left alone.

CK-11: a pending capture that PayPal declines leaves the order unpaid and marked declined, so the
buyer can pay another way (CK-09). A capture PayPal already completed is never un-paid by an event.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.checkout.retry import can_pay_again
from app.orders.enums import CaseState, OrderState
from app.orders.models import AuditLog, MoneyAction
from tests.factories import make_case, make_order, make_seller
from tests.unit.test_wh_paypal_events import _handle


def _reversal(order, *, refund_id: str = "09E71677NS257044M", value: str = "60.00") -> dict:
    return {
        "id": refund_id,
        "status": "COMPLETED",
        "amount": {"currency_code": order.currency, "value": value},
        "links": [
            {"rel": "self", "href": f"https://api/v2/payments/refunds/{refund_id}"},
            {"rel": "up", "href": f"https://api/v2/payments/captures/{order.capture_id}"},
        ],
    }


def _declined(capture_id: str) -> dict:
    return {
        "id": capture_id,
        "status": "DECLINED",
        "amount": {"currency_code": "GBP", "value": "60.00"},
        "links": [{"rel": "up", "href": "https://api/v2/payments/authorizations/2W543679"}],
    }


# ---------------------------------------------------------------- PO-15 reversal


def test_PO_15_a_reversal_is_money_already_back_with_the_buyer(db, rulebook, clock):
    order = make_order(db, make_seller(db))
    _handle(db, rulebook, clock, "PAYMENT.CAPTURE.REVERSED", _reversal(order))
    _handle(db, rulebook, clock, "PAYMENT.CAPTURE.REVERSED", _reversal(order))  # redelivery
    [ma] = db.scalars(select(MoneyAction).where(MoneyAction.order_id == order.id)).all()
    assert (ma.external, ma.amount_minor, ma.paypal_refund_id) == (True, 6000, "09E71677NS257044M")
    audit = db.scalars(
        select(AuditLog).where(
            AuditLog.entity_id == order.id, AuditLog.action == "paypal.capture_reversed"
        )
    ).all()
    assert len(audit) == 1 and audit[0].after["refund_id"] == "09E71677NS257044M"


@pytest.mark.parametrize(
    "state", [CaseState.DECIDED, CaseState.AUTO_REFUND, CaseState.AWAITING_SELLER]
)
def test_PO_15_a_case_that_could_still_pay_goes_to_a_person(db, rulebook, clock, state):
    order = make_order(db, make_seller(db))
    case = make_case(db, order, state=state)
    _handle(db, rulebook, clock, "PAYMENT.CAPTURE.REVERSED", _reversal(order))
    assert case.state == CaseState.HOLD_FOR_REVIEW.value


@pytest.mark.parametrize(
    "state",
    [CaseState.DISPUTE_EVIDENCE, CaseState.SETTLED, CaseState.CLOSED, CaseState.HOLD_FOR_REVIEW],
)
def test_PO_15_a_dispute_or_finished_case_is_left_alone(db, rulebook, clock, state):
    order = make_order(db, make_seller(db))
    case = make_case(db, order, state=state)
    _handle(db, rulebook, clock, "PAYMENT.CAPTURE.REVERSED", _reversal(order))
    assert case.state == state.value


def test_PO_15_an_unknown_capture_is_skipped(db, rulebook, clock):
    order = make_order(db, make_seller(db))
    resource = _reversal(order)
    resource["links"][1]["href"] = "https://api/v2/payments/captures/NOT-OURS"
    _handle(db, rulebook, clock, "PAYMENT.CAPTURE.REVERSED", resource)
    assert db.scalars(select(MoneyAction)).all() == []


# ---------------------------------------------------------------- CK-11 declined after pending


@pytest.mark.parametrize("event", ["PAYMENT.CAPTURE.DECLINED", "PAYMENT.CAPTURE.DENIED"])
def test_CK_11_a_pending_capture_declined_lets_the_buyer_pay_another_way(
    db, rulebook, clock, event
):
    order = make_order(
        db, make_seller(db), state=OrderState.CREATED, capture_id="CAP-P1", capture_status="PENDING"
    )
    _handle(db, rulebook, clock, event, _declined("CAP-P1"))
    assert (order.state, order.capture_status) == (OrderState.CREATED.value, "DECLINED")
    assert can_pay_again(order)
    assert db.scalars(
        select(AuditLog).where(
            AuditLog.entity_id == order.id, AuditLog.action == "paypal.capture_declined"
        )
    ).one()


@pytest.mark.parametrize("event", ["PAYMENT.CAPTURE.DECLINED", "PAYMENT.CAPTURE.DENIED"])
def test_CK_11_a_completed_capture_is_never_unpaid_by_an_event(db, rulebook, clock, event):
    order = make_order(db, make_seller(db), state=OrderState.PAID)
    _handle(db, rulebook, clock, event, _declined(order.capture_id))
    assert (order.state, order.capture_status) == (OrderState.PAID.value, "COMPLETED")
    assert db.scalars(
        select(AuditLog).where(
            AuditLog.entity_id == order.id, AuditLog.action == "paypal.capture_declined_ignored"
        )
    ).one()


def test_CK_11_an_unknown_capture_is_skipped(db, rulebook, clock):
    _handle(db, rulebook, clock, "PAYMENT.CAPTURE.DECLINED", _declined("CAP-UNKNOWN"))
    assert db.scalars(select(AuditLog)).all() == []
