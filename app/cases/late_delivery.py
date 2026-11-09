"""AD-01..04: the carrier scans "Delivered" after the buyer reported the parcel missing.

From seller research (Nov 9, 2026): the most common pain in sellers' own videos is a parcel
refunded as lost that then turns up, with nobody telling the seller, and a carrier claim still
open for a parcel the carrier now says it delivered.

AD-01: the first delivered scan dated after a "not received" report, with no delivered scan
before the report, is recorded once on the order. Later scans change nothing.
AD-02: a carrier claim the seller has not submitted yet is withdrawn, because a claim for a
parcel the carrier says it delivered would be refused. A submitted claim is left to the seller.
AD-03: the buyer may tap "It arrived". That stores the buyer's confirmation as evidence, so a
refund still waiting is held for a person (the evidence fingerprint changes).
AD-04: nothing here moves money, changes the order's state or asks the buyer to pay. The seller
gets the facts and a neutral message; the buyer is never accused (hard rule 7).
AD-05: the scan is stored as evidence, so a refund that has not gone out yet is held for a person
by the pre-refund re-check (app/policy, PO-13). A refund PayPal already has is never touched.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cases.recovery import transition_recovery
from app.cases.signals import EvidenceKind
from app.orders.enums import MoneyActionStatus, RecoveryStatus
from app.orders.models import (
    AuditLog,
    Case,
    Evidence,
    MoneyAction,
    Order,
    Recovery,
    Shipment,
    TrackingEvent,
)

RECORDED = "order.delivered_after_report"
ARRIVED = "buyer.arrived_after_report"
NOT_RECEIVED = "not_received"
DELIVERED = "DELIVERED"
EVIDENCE_CHANGED = "evidence_changed"  # app/policy/reasons.py Reason.EVIDENCE_CHANGED


class NotDeliveredAfterReport(ValueError):
    """The buyer tapped "It arrived" on an order with no delivered scan after a report."""


@dataclass(frozen=True)
class LateDelivery:
    case_id: str
    delivered_at: datetime
    claim_withdrawn: bool
    buyer_says_arrived: bool
    refund_held: bool  # AD-05: a refund that had not gone out is held for the seller


def _audit(session: Session, order_id: str, action: str) -> AuditLog | None:
    return session.scalars(
        select(AuditLog)
        .where(
            AuditLog.entity_type == "order",
            AuditLog.entity_id == order_id,
            AuditLog.action == action,
        )
        .order_by(AuditLog.at)
    ).first()


def _refund_held(session: Session, case_id: str) -> bool:
    blocked = session.scalars(
        select(MoneyAction).where(
            MoneyAction.case_id == case_id,
            MoneyAction.status == MoneyActionStatus.BLOCKED.value,
        )
    )
    return any((m.paypal_response or {}).get("blocked_code") == EVIDENCE_CHANGED for m in blocked)


def late_delivery(session: Session, order_id: str) -> LateDelivery | None:
    row = _audit(session, order_id, RECORDED)
    if row is None:
        return None
    case_id = str(row.after["case_id"])
    return LateDelivery(
        case_id=case_id,
        delivered_at=datetime.fromisoformat(str(row.after["delivered_at"])),
        claim_withdrawn=bool(row.after.get("claim_withdrawn")),
        buyer_says_arrived=_audit(session, order_id, ARRIVED) is not None,
        refund_held=_refund_held(session, case_id),
    )


def record_delivered_scan(
    session: Session, order: Order, event: TrackingEvent, *, now: datetime
) -> bool:
    """AD-01/02. True when this scan is the parcel turning up after a "not received" report."""
    if event.status != DELIVERED:
        return False
    case = session.scalars(
        select(Case)
        .where(Case.order_id == order.id, Case.reason == NOT_RECEIVED)
        .order_by(Case.opened_at.desc())
    ).first()
    if case is None or event.occurred_at <= case.opened_at:
        return False  # delivered before the report: an ordinary "delivered but not here" case
    shipments = select(Shipment.id).where(Shipment.order_id == order.id)
    earlier = session.scalar(
        select(TrackingEvent.id).where(
            TrackingEvent.shipment_id.in_(shipments),
            TrackingEvent.status == DELIVERED,
            TrackingEvent.occurred_at <= case.opened_at,
        )
    )
    if earlier is not None or _audit(session, order.id, RECORDED) is not None:
        return False
    claim = session.scalars(select(Recovery).where(Recovery.case_id == case.id)).first()
    withdrawn = claim is not None and claim.status == RecoveryStatus.PREPARED.value
    if withdrawn:
        assert claim is not None
        transition_recovery(
            session,
            claim,
            RecoveryStatus.WITHDRAWN,
            actor="carrier",
            reason="the carrier now scans the parcel as delivered",
            now=now,
        )
    session.add(  # AD-05: new evidence, so the pre-refund re-check holds a waiting refund
        Evidence(
            order_id=order.id,
            kind=EvidenceKind.CARRIER_DELIVERED_AFTER_REPORT.value,
            ai_result={"case_id": case.id, "delivered_at": event.occurred_at.isoformat()},
            created_at=now,
        )
    )
    session.add(
        AuditLog(
            actor="carrier",
            action=RECORDED,
            entity_type="order",
            entity_id=order.id,
            before={},
            after={
                "case_id": case.id,
                "delivered_at": event.occurred_at.isoformat(),
                "claim_withdrawn": withdrawn,
            },
            at=now,
        )
    )
    session.flush()
    return True


def buyer_says_arrived(session: Session, order: Order, *, actor: str, now: datetime) -> None:
    """AD-03. Idempotent: a second tap changes nothing."""
    if late_delivery(session, order.id) is None:
        raise NotDeliveredAfterReport(f"order {order.id} has no delivered scan after a report")
    if _audit(session, order.id, ARRIVED) is not None:
        return
    session.add(
        Evidence(
            order_id=order.id,
            kind=EvidenceKind.BUYER_CONFIRMATION.value,
            ai_result={"source": actor, "after_report": True},
            created_at=now,
        )
    )
    session.add(
        AuditLog(
            actor=actor,
            action=ARRIVED,
            entity_type="order",
            entity_id=order.id,
            before={},
            after={},
            at=now,
        )
    )
    session.flush()
