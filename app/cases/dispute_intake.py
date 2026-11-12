"""DI-01..DI-04: a dispute PayPal reports puts the order on the evidence route.

PayPal tells Doorstep about a dispute in two ways: the CUSTOMER.DISPUTE.* webhook, and a sweep
that asks PayPal for open disputes in case the webhook never comes (a server PayPal cannot reach
never gets one). Both call `take_dispute`, which is safe to repeat:

1. The dispute is on file, open.
2. The order's active case moves to DISPUTE_EVIDENCE when the state machine allows it, so the
   seller can send evidence. A held case stays held: a person decides it again, and that decision
   sees the open dispute. A refund already in the outbox is stopped by its own re-check.
3. An order that never had a case, and can have one, gets one opened by "paypal" and decided on
   the facts as they are. An order with no carrier scan yet is first brought up to date from
   the carrier (DI-06), as a server the carrier cannot reach gets no tracking webhook. The
   policy's first check routes the case to DISPUTE_EVIDENCE, as the dispute is open. Only
   delivery problems open a case: other dispute reasons are recorded for a person.

Nothing here calls PayPal or moves money: evidence goes only when the seller taps Send evidence,
through app.policy (hard rules 1 and 7).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cases.engine import ClaimReason
from app.cases.reactions import record_listed_disputes
from app.clock import Clock
from app.orders.enums import CaseState, OrderState
from app.orders.models import AuditLog, Case, Dispute, Order, Shipment, TrackingEvent
from app.orders.state import FINISHED_CASE_STATES

READ_ACTION = "paypal.dispute_read"  # DI-07: the audit action that keeps PayPal's dispute facts

# PayPal's dispute reasons that are delivery problems, and the buyer report each one matches.
PAYPAL_REASONS: dict[str, ClaimReason] = {
    "MERCHANDISE_OR_SERVICE_NOT_RECEIVED": ClaimReason.NOT_RECEIVED,
    "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED": ClaimReason.DAMAGED,
}
# The order states a buyer can report from (CasePipeline.report); the same gate applies here.
CASE_STATES_FROM = frozenset(
    {
        OrderState.IN_TRANSIT,
        OrderState.EXCEPTION,
        OrderState.DELIVERED,
        OrderState.PICKED_UP,
        OrderState.CONFIRMED,
    }
)

OpenCase = Callable[[Session, Order, ClaimReason], object]
Refresh = Callable[[Session, Order], None]  # DI-06: the carrier's latest scan, applied


def take_dispute(
    session: Session,
    order: Order,
    dispute_id: str,
    *,
    reason: str,
    clock: Clock,
    open_case: OpenCase | None = None,
    refresh: Refresh | None = None,
) -> str:
    """Record an open PayPal dispute on this order and route its case; returns a sentence for the
    audit and the logs. `open_case` opens and decides a case (CasePipeline.report); without it
    an order with no case is only recorded."""
    cases = session.scalars(
        select(Case).where(Case.order_id == order.id).order_by(Case.opened_at.desc())
    ).all()
    active = next((c for c in cases if CaseState(c.state) not in FINISHED_CASE_STATES), None)
    if active is not None:
        return record_listed_disputes(session, active, [dispute_id], clock=clock)
    _on_file(session, order, dispute_id, clock)
    claim = PAYPAL_REASONS.get(reason.upper())
    if cases or open_case is None or claim is None:
        return f"Recorded dispute {dispute_id}; the Disputes page shows it to the seller."
    if refresh is not None and (
        OrderState(order.state) not in CASE_STATES_FROM or not _has_scan(session, order)
    ):
        refresh(session, order)
    if OrderState(order.state) not in CASE_STATES_FROM:
        return f"Recorded dispute {dispute_id}; the order is {order.state.lower()}, so no case."
    open_case(session, order, claim)
    return f"Recorded dispute {dispute_id} and opened a case to send evidence through it."


def record_read(session: Session, dispute_id: str, facts: dict[str, Any], *, clock: Clock) -> bool:
    """DI-07: what PayPal says about a dispute on file (reason, stage, reply-by date, amount), kept
    for the Disputes page, as a server PayPal cannot reach gets no webhook to show. Written only
    when it changed; True when written."""
    row = session.scalar(select(Dispute).where(Dispute.paypal_dispute_id == dispute_id))
    if row is None or not facts:
        return False
    if latest_read(session, row.id) == facts:
        return False
    session.add(
        AuditLog(
            actor="paypal",
            action=READ_ACTION,
            entity_type="dispute",
            entity_id=row.id,
            before={},
            after=dict(facts),
            at=clock.now(),
        )
    )
    session.flush()
    return True


def latest_read(session: Session, dispute_row_id: str) -> dict[str, Any] | None:
    """The facts `record_read` last kept for this dispute, or None."""
    row = session.scalars(
        select(AuditLog)
        .where(
            AuditLog.entity_type == "dispute",
            AuditLog.entity_id == dispute_row_id,
            AuditLog.action == READ_ACTION,
        )
        .order_by(AuditLog.at.desc(), AuditLog.id.desc())
    ).first()
    return dict(row.after) if row is not None else None


def _has_scan(session: Session, order: Order) -> bool:
    """True when the carrier has sent any scan for this order's parcels."""
    shipments = select(Shipment.id).where(Shipment.order_id == order.id)
    found = session.scalar(
        select(TrackingEvent.id).where(TrackingEvent.shipment_id.in_(shipments)).limit(1)
    )
    return found is not None


def _on_file(session: Session, order: Order, dispute_id: str, clock: Clock) -> None:
    row = session.scalar(select(Dispute).where(Dispute.paypal_dispute_id == dispute_id))
    if row is None:
        session.add(
            Dispute(
                order_id=order.id,
                paypal_dispute_id=dispute_id,
                status="open",
                opened_at=clock.now(),
            )
        )
    elif row.order_id == order.id:
        row.status = "open"  # PayPal lists it open: a reopened dispute
    session.flush()
