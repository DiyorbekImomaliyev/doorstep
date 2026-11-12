"""PayPal webhook events, processed from the inbox by the worker.

- CHECKOUT.ORDER.APPROVED: if the buyer does not come back from PayPal, a follow-up job checks the
  address and captures (CK-08).
- PAYMENT.CAPTURE.COMPLETED: backup to the capture job; stores the capture and marks PAID.
- PAYMENT.CAPTURE.REFUNDED: records refunds made outside Doorstep, so the refundable balance
  stays right (PO-06). Doorstep's own refunds are recognised by their PayPal refund id or their
  custom_id; a pending one of ours is confirmed by PayPal's status here (PO-13).
- PAYMENT.CAPTURE.REVERSED: PayPal returned the money to the buyer (a refund resource, like
  REFUNDED). Recorded the same way, so Doorstep never pays it twice, and a case that could still
  pay out goes to a person (PO-15).
- PAYMENT.CAPTURE.DECLINED / DENIED: a pending capture did not go through. The order stays unpaid
  and is marked declined, so the buyer can pay another way; a completed capture is never un-paid
  by an event (CK-11).
- CUSTOMER.DISPUTE.CREATED/UPDATED/RESOLVED: keeps the disputes table current (and puts the
  order's case on the evidence route, DI-01), so the policy
  routes to DISPUTE_EVIDENCE instead of refunding outside the dispute (PO-05). RESOLVED closes the
  case that was waiting on the dispute, since PayPal decided it (PO-14).
Events for orders Doorstep does not know are logged and skipped, never failed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cases.dispute_intake import OpenCase, Refresh, take_dispute
from app.clock import Clock
from app.money import Money
from app.orders.enums import CaseState, OrderState
from app.orders.models import AuditLog, Case, Dispute, MoneyAction, Order
from app.orders.state import transition_case, transition_order
from app.policy.rules import RuleBook
from app.policy.service import PolicyService
from app.proof.receipts import ReceiptSigner, issue_refund_receipt
from app.proof.seller_protection import record_seller_protection
from app.worker.approved import awaiting_return, schedule_completion
from app.worker.models import WebhookInbox

log = logging.getLogger("doorstep.paypal_events")
# The audit action is "transition: dispute resolved: <code>" in a 64-character column.
_OUTCOME_CHARS = 30

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


def _make_refunded(rulebook: RuleBook, clock: Clock, receipts: ReceiptSigner | None) -> Handler:
    def handle(s: Session, row: WebhookInbox) -> None:
        res = _resource(row)
        order = _order_by_capture(s, _capture_id_from_links(res))
        amount = _dict(res.get("amount"))
        if order is None or not res.get("id") or not amount:
            log.info("refund event %s not matched to an order; skipped", row.event_id)
            return
        money = Money.from_paypal(str(amount.get("value")), str(amount.get("currency_code")))
        ma = PolicyService(s, rulebook, clock).record_external_refund(
            order,
            money,
            paypal_refund_id=str(res["id"]),
            status=str(res.get("status") or "COMPLETED"),
            custom_id=str(res.get("custom_id") or "") or None,
        )
        if receipts is not None:
            issue_refund_receipt(s, receipts, ma, at=clock.now())

    return handle


# PO-15: the cases a reversal could still pay out on. A case in a PayPal dispute is PayPal's to
# decide; a settled, closed or held one has nothing left to pay.
_PAYABLE = frozenset(
    {CaseState.DECIDED.value, CaseState.AUTO_REFUND.value, CaseState.AWAITING_SELLER.value}
)
DECLINED = "DECLINED"  # the capture status the buyer's "pay another way" reads (CK-09)


def _audit(s: Session, order: Order, action: str, after: dict[str, Any], clock: Clock) -> None:
    s.add(
        AuditLog(
            actor="paypal",
            action=action,
            entity_type="order",
            entity_id=order.id,
            before={},
            after=after,
            at=clock.now(),
        )
    )


def _make_reversed(rulebook: RuleBook, clock: Clock, receipts: ReceiptSigner | None) -> Handler:
    """PO-15: PayPal reversed a capture, so the money is back with the buyer."""
    record = _make_refunded(rulebook, clock, receipts)

    def handle(s: Session, row: WebhookInbox) -> None:
        res = _resource(row)
        order = _order_by_capture(s, _capture_id_from_links(res))
        refund_id = str(res.get("id") or "")
        if order is None or not refund_id:
            log.info("reversal event %s not matched to an order; skipped", row.event_id)
            return
        seen = s.scalars(
            select(MoneyAction.id).where(MoneyAction.paypal_refund_id == refund_id)
        ).first()
        record(s, row)
        if seen is not None:
            return  # already recorded: a redelivery, or a refund Doorstep made itself
        _audit(s, order, "paypal.capture_reversed", {"refund_id": refund_id}, clock)
        for case in s.scalars(
            select(Case).where(Case.order_id == order.id, Case.state.in_(_PAYABLE))
        ).all():
            transition_case(
                s, case, CaseState.HOLD_FOR_REVIEW, actor="paypal", reason="PayPal reversed it"
            )

    return handle


def _make_capture_declined(clock: Clock) -> Handler:
    """CK-11: a pending capture PayPal declined. Only an unpaid order changes."""

    def handle(s: Session, row: WebhookInbox) -> None:
        res = _resource(row)
        capture_id = str(res.get("id") or "")
        order = _order_by_capture(s, capture_id)
        if order is None:
            log.info("declined capture event %s not matched to an order; skipped", row.event_id)
            return
        facts = {"capture_id": capture_id, "event": row.event_type}
        if order.state != OrderState.CREATED.value or order.capture_status == "COMPLETED":
            _audit(s, order, "paypal.capture_declined_ignored", facts, clock)
            return
        order.capture_status = DECLINED
        _audit(s, order, "paypal.capture_declined", facts, clock)

    return handle


def _make_dispute(clock: Clock, open_case: OpenCase | None, refresh: Refresh | None) -> Handler:
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
        elif dispute.status != "resolved":
            dispute.status = status
        else:  # resolved is final for webhooks: a late or reordered event never reopens it
            log.info("dispute %s already resolved; %s ignored", dispute_id, row.event_type)
            return
        s.flush()
        if resolved:
            _close_disputed_case(s, order, _dict(res.get("dispute_outcome")))
            return
        said = take_dispute(  # DI-01: the case goes on the evidence route
            s,
            order,
            dispute_id,
            reason=str(res.get("reason") or ""),
            clock=clock,
            open_case=open_case,
            refresh=refresh,
        )
        log.info("dispute %s: %s", dispute_id, said)

    return handle


def record_resolved(s: Session, dispute: Dispute, outcome_code: str) -> None:
    """DI-05: the sweep's way to PO-14, for a resolved webhook that never came. PayPal says the
    dispute is resolved, so it is recorded resolved and the case waiting on it is finished."""
    if dispute.status == "resolved":
        return
    dispute.status = "resolved"
    s.flush()
    order = s.get(Order, dispute.order_id)
    if order is not None:
        _close_disputed_case(s, order, {"outcome_code": outcome_code})


def _close_disputed_case(s: Session, order: Order, outcome: dict[str, Any]) -> None:
    """PO-14: PayPal decided the dispute, so the case waiting on it is finished. PayPal's outcome
    code goes into the audit word for word (cut to fit); Doorstep does not interpret it."""
    case = s.scalars(
        select(Case).where(
            Case.order_id == order.id, Case.state == CaseState.DISPUTE_EVIDENCE.value
        )
    ).first()
    if case is None:
        return
    code = str(outcome.get("outcome_code") or "no outcome code")[:_OUTCOME_CHARS]
    transition_case(s, case, CaseState.CLOSED, actor="paypal", reason=f"dispute resolved: {code}")


def _make_order_approved(clock: Clock) -> Handler:
    """CK-08: PayPal says the buyer approved. If they do not come back within the grace period,
    the follow-up job checks the address and captures."""

    def handle(s: Session, row: WebhookInbox) -> None:
        paypal_order_id = str(_resource(row).get("id") or "")
        order = s.scalars(select(Order).where(Order.paypal_order_id == paypal_order_id)).first()
        if order is None or not paypal_order_id:
            log.info("approved event %s not matched to an order; skipped", row.event_id)
            return
        if awaiting_return(order):
            schedule_completion(s, order, clock=clock, sweep=False)

    return handle


def paypal_inbox_handlers(
    rulebook: RuleBook,
    clock: Clock,
    receipts: ReceiptSigner | None = None,
    open_case: OpenCase | None = None,
    refresh: Refresh | None = None,
) -> dict[tuple[str, str], Handler]:
    dispute = _make_dispute(clock, open_case, refresh)
    return {
        ("paypal", "CHECKOUT.ORDER.APPROVED"): _make_order_approved(clock),
        ("paypal", "PAYMENT.CAPTURE.COMPLETED"): _make_capture_completed(clock),
        ("paypal", "PAYMENT.CAPTURE.REFUNDED"): _make_refunded(rulebook, clock, receipts),
        ("paypal", "PAYMENT.CAPTURE.REVERSED"): _make_reversed(rulebook, clock, receipts),
        ("paypal", "PAYMENT.CAPTURE.DECLINED"): _make_capture_declined(clock),
        ("paypal", "PAYMENT.CAPTURE.DENIED"): _make_capture_declined(clock),
        ("paypal", "CUSTOMER.DISPUTE.CREATED"): dispute,
        ("paypal", "CUSTOMER.DISPUTE.UPDATED"): dispute,
        ("paypal", "CUSTOMER.DISPUTE.RESOLVED"): dispute,
    }
