"""PayPal webhook events, processed from the inbox by the worker (SPEC section 9).

- PAYMENT.CAPTURE.COMPLETED: backup to the capture job; stores the capture and marks PAID.
- PAYMENT.CAPTURE.REFUNDED: records refunds made outside Doorstep, so the refundable balance
  stays right (PO-06). Doorstep's own refunds are recognised by their PayPal refund id.
- CUSTOMER.DISPUTE.CREATED/UPDATED/RESOLVED: keeps the disputes table current, so the policy
  routes to DISPUTE_EVIDENCE instead of refunding outside the dispute (PO-05).
Events for orders Doorstep does not know are logged and skipped, never failed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import Clock
from app.money import Money
from app.orders.enums import OrderState
from app.orders.models import Dispute, Order
from app.orders.state import transition_order
from app.policy.rules import RuleBook
from app.policy.service import PolicyService
from app.proof.seller_protection import record_seller_protection
from app.worker.models import WebhookInbox

log = logging.getLogger("doorstep.paypal_events")

Handler = Callable[[Session, WebhookInbox], None]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _resource(row: WebhookInbox) -> dict[str, Any]:
    return _dict(row.payload.get("resource"))


def _order_by_capture(s: Session, capture_id: str) -> Order | None:
    if not capture_id:
        return None
    return s.scalars(select(Order).where(Order.capture_id == capture_id)).first()


def _make_capture_completed(clock: Clock) -> Handler:
    def handle(s: Session, row: WebhookInbox) -> None:
        res = _resource(row)
        related = _dict(_dict(res.get("supplementary_data")).get("related_ids"))
        ppo = str(related.get("order_id") or "")
        order = (
            s.scalars(select(Order).where(Order.paypal_order_id == ppo)).first() if ppo else None
        )
        if order is None:
            log.info("capture event %s for unknown order; skipped", row.event_id)
            return
        order.capture_id = str(res.get("id") or order.capture_id or "")
        order.capture_status = str(res.get("status") or order.capture_status or "")
        protection = _dict(res.get("seller_protection"))
        categories = protection.get("dispute_categories")
        record_seller_protection(  # PP-11: information only
            s,
            order.id,
            str(protection.get("status") or ""),
            [str(c) for c in categories] if isinstance(categories, list) else [],
            clock.now(),
        )
        if order.capture_status == "COMPLETED" and order.state == OrderState.CREATED.value:
            transition_order(s, order, OrderState.PAID, actor="paypal", reason="capture completed")

    return handle


def _capture_id_from_links(res: dict[str, Any]) -> str:
    for link in res.get("links") or []:
        link = _dict(link)
        if link.get("rel") == "up" and "/captures/" in str(link.get("href", "")):
            return str(link["href"]).rstrip("/").rsplit("/", 1)[-1]
    return ""


def _make_refunded(rulebook: RuleBook, clock: Clock) -> Handler:
    def handle(s: Session, row: WebhookInbox) -> None:
        res = _resource(row)
        order = _order_by_capture(s, _capture_id_from_links(res))
        amount = _dict(res.get("amount"))
        if order is None or not res.get("id") or not amount:
            log.info("refund event %s not matched to an order; skipped", row.event_id)
            return
        money = Money.from_paypal(str(amount.get("value")), str(amount.get("currency_code")))
        PolicyService(s, rulebook, clock).record_external_refund(
            order, money, paypal_refund_id=str(res["id"])
        )

    return handle


def _make_dispute(clock: Clock) -> Handler:
    def handle(s: Session, row: WebhookInbox) -> None:
        res = _resource(row)
        dispute_id = str(res.get("dispute_id") or "")
        order = None
        for tx in res.get("disputed_transactions") or []:
            order = _order_by_capture(s, str(_dict(tx).get("seller_transaction_id") or ""))
            if order is not None:
                break
        if order is None or not dispute_id:
            log.info("dispute event %s not matched to an order; skipped", row.event_id)
            return
        resolved = row.event_type.endswith("RESOLVED") or res.get("status") == "RESOLVED"
        status = "resolved" if resolved else "open"
        dispute = s.scalars(select(Dispute).where(Dispute.paypal_dispute_id == dispute_id)).first()
        if dispute is None:
            s.add(
                Dispute(
                    order_id=order.id,
                    paypal_dispute_id=dispute_id,
                    status=status,
                    opened_at=clock.now(),
                )
            )
        else:
            dispute.status = status
        s.flush()

    return handle


def paypal_inbox_handlers(rulebook: RuleBook, clock: Clock) -> dict[tuple[str, str], Handler]:
    dispute = _make_dispute(clock)
    return {
        ("paypal", "PAYMENT.CAPTURE.COMPLETED"): _make_capture_completed(clock),
        ("paypal", "PAYMENT.CAPTURE.REFUNDED"): _make_refunded(rulebook, clock),
        ("paypal", "CUSTOMER.DISPUTE.CREATED"): dispute,
        ("paypal", "CUSTOMER.DISPUTE.UPDATED"): dispute,
        ("paypal", "CUSTOMER.DISPUTE.RESOLVED"): dispute,
    }
