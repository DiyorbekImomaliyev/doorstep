"""PayPal webhook events processed from the inbox (SPEC section 9): capture completed, refunds
made outside Doorstep (PO-06), disputes (PO-05), and the approved order (CK-01)."""

from __future__ import annotations

from sqlalchemy import select

from app.orders.enums import OrderState
from app.orders.models import Dispute, MoneyAction, Order
from app.paypal_client.approved import parse_approved_order
from app.worker.models import WebhookInbox
from app.worker.paypal_events import paypal_inbox_handlers
from tests.factories import make_order, make_seller


def _row(event_type: str, resource: dict) -> WebhookInbox:
    return WebhookInbox(
        event_id=f"WH-{event_type}-{resource.get('id', 'x')}",
        source="paypal",
        event_type=event_type,
        payload={"id": "WH-1", "event_type": event_type, "resource": resource},
        status="pending",
        attempts=0,
    )


def _handle(db, rulebook, clock, event_type: str, resource: dict) -> None:
    handlers = paypal_inbox_handlers(rulebook, clock)
    handlers[("paypal", event_type)](db, _row(event_type, resource))
    db.flush()


def test_capture_completed_marks_a_created_order_paid(db, rulebook, clock):
    order = make_order(
        db, make_seller(db), state=OrderState.CREATED, capture_id=None, capture_status=None
    )
    _handle(
        db,
        rulebook,
        clock,
        "PAYMENT.CAPTURE.COMPLETED",
        {
            "id": "CAP-777",
            "status": "COMPLETED",
            "supplementary_data": {"related_ids": {"order_id": order.paypal_order_id}},
        },
    )
    assert (order.state, order.capture_id, order.capture_status) == ("PAID", "CAP-777", "COMPLETED")


def test_capture_completed_twice_is_harmless(db, rulebook, clock):
    order = make_order(
        db, make_seller(db), state=OrderState.CREATED, capture_id=None, capture_status=None
    )
    resource = {
        "id": "CAP-1",
        "status": "COMPLETED",
        "supplementary_data": {"related_ids": {"order_id": order.paypal_order_id}},
    }
    _handle(db, rulebook, clock, "PAYMENT.CAPTURE.COMPLETED", resource)
    _handle(db, rulebook, clock, "PAYMENT.CAPTURE.COMPLETED", resource)
    assert order.state == "PAID"


def test_PO_06_refund_made_outside_doorstep_is_recorded(db, rulebook, clock):
    order = make_order(db, make_seller(db))
    resource = {
        "id": "REF-EXT-1",
        "status": "COMPLETED",
        "amount": {"currency_code": "GBP", "value": "15.00"},
        "links": [{"rel": "up", "href": f"https://api/v2/payments/captures/{order.capture_id}"}],
    }
    _handle(db, rulebook, clock, "PAYMENT.CAPTURE.REFUNDED", resource)
    _handle(db, rulebook, clock, "PAYMENT.CAPTURE.REFUNDED", resource)  # redelivery
    [ma] = db.scalars(select(MoneyAction).where(MoneyAction.order_id == order.id)).all()
    assert (ma.external, ma.amount_minor, ma.paypal_refund_id) == (True, 1500, "REF-EXT-1")


def test_PO_05_dispute_created_then_resolved(db, rulebook, clock):
    order = make_order(db, make_seller(db))
    resource = {
        "dispute_id": "PP-D-1",
        "status": "OPEN",
        "disputed_transactions": [{"seller_transaction_id": order.capture_id}],
    }
    _handle(db, rulebook, clock, "CUSTOMER.DISPUTE.CREATED", resource)
    [d] = db.scalars(select(Dispute)).all()
    assert (d.order_id, d.status) == (order.id, "open")
    _handle(db, rulebook, clock, "CUSTOMER.DISPUTE.RESOLVED", {**resource, "status": "RESOLVED"})
    assert d.status == "resolved"


def test_unknown_order_events_are_skipped_not_failed(db, rulebook, clock):
    _handle(
        db, rulebook, clock, "PAYMENT.CAPTURE.COMPLETED", {"id": "CAP-X", "status": "COMPLETED"}
    )
    _handle(
        db,
        rulebook,
        clock,
        "CUSTOMER.DISPUTE.CREATED",
        {"dispute_id": "D-X", "disputed_transactions": [{"seller_transaction_id": "nope"}]},
    )
    assert db.scalars(select(Order)).all() == []


def test_CK_01_approved_order_is_parsed_from_paypal():
    approved = parse_approved_order(
        {
            "id": "PPO-1",
            "payer": {"payer_id": "PAYER-9"},
            "purchase_units": [
                {
                    "shipping": {
                        "name": {"full_name": "Sam Carter"},
                        "address": {
                            "address_line_1": "Rosewood Court, 10 High Street",
                            "admin_area_2": "London",
                            "postal_code": "N1 9GU",
                            "country_code": "GB",
                        },
                    }
                }
            ],
        }
    )
    assert approved.payer_id == "PAYER-9"
    assert approved.address.line1 == "Rosewood Court, 10 High Street"
    assert (approved.address.city, approved.address.country) == ("London", "GB")
