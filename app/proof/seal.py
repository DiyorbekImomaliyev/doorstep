"""Possession seal (SPEC section 7a.1, SL-01..06, ADR-035).

A QR code (or an NFC sticker holding the same URL) on the packing slip inside the parcel. The
URL carries a signed token scoped ``seal`` to one order. Scanning it with the buyer's own phone
proves that someone opened the parcel, which is the physical possession that CRA 2015 s.29 turns
on. There is no app, no login, no typing and no location.

A scan counts as possession proof only if the carrier had already handled the parcel (a transit,
delivered, failure or returned event at or before the scan). That stops a seller scanning the seal
while packing. A scan that does not count is still recorded, with the reason.
"""

from __future__ import annotations

from datetime import datetime

import segno
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cases.signals import EvidenceKind
from app.orders.enums import OrderState
from app.orders.models import Evidence, Order, Shipment, TrackingEvent
from app.security.links import LinkSigner

SEAL_SCOPE = "seal"
SEAL_TTL_S = 180 * 24 * 3600  # long enough for slow post and late unboxing
# Carrier events that mean the carrier physically had the parcel.
HANDLED_STATUSES = frozenset({"TRANSIT", "DELIVERED", "FAILURE", "RETURNED"})
_NOT_SHIPPED = frozenset({OrderState.CREATED, OrderState.PAID, OrderState.PACKED})
AFTER_HANDOVER = "after_carrier_handover"
BEFORE_HANDOVER = "before_carrier_handover"


class SealError(Exception):
    """The order cannot take a seal scan yet (callers answer 409)."""


def issue_seal(links: LinkSigner, order_id: str) -> str:
    return links.issue(order_id, scope=SEAL_SCOPE, ttl_s=SEAL_TTL_S)


def seal_url(app_base_url: str, token: str) -> str:
    return f"{app_base_url.rstrip('/')}/s/{token}"


def seal_svg(url: str) -> str:
    """The QR code as inline SVG (medium error correction survives a crumpled slip)."""
    return segno.make(url, error="m").svg_inline(scale=5, border=2)


def carrier_handled_by(session: Session, order: Order, at: datetime) -> bool:
    shipment_ids = select(Shipment.id).where(Shipment.order_id == order.id)
    found = session.scalar(
        select(TrackingEvent.id)
        .where(TrackingEvent.shipment_id.in_(shipment_ids))
        .where(TrackingEvent.status.in_(HANDLED_STATUSES))
        .where(TrackingEvent.occurred_at <= at)
        .limit(1)
    )
    return found is not None


def seal_scans(session: Session, order_id: str) -> list[Evidence]:
    return list(
        session.scalars(
            select(Evidence)
            .where(Evidence.order_id == order_id, Evidence.kind == EvidenceKind.SEAL_SCAN.value)
            .order_by(Evidence.created_at)
        )
    )


def counted_scan(session: Session, order_id: str) -> Evidence | None:
    return next((e for e in seal_scans(session, order_id) if e.ai_result.get("counted")), None)


def record_scan(session: Session, order: Order, now: datetime) -> Evidence:
    """Record a seal scan. Repeats are no-ops: at most one counted and one not-counted row."""
    if OrderState(order.state) in _NOT_SHIPPED:
        raise SealError(f"order {order.id} has not shipped yet")
    counted = carrier_handled_by(session, order, now)
    for existing in seal_scans(session, order.id):
        if bool(existing.ai_result.get("counted")) == counted or existing.ai_result.get("counted"):
            return existing
    evidence = Evidence(
        order_id=order.id,
        kind=EvidenceKind.SEAL_SCAN.value,
        ai_result={"counted": counted, "reason": AFTER_HANDOVER if counted else BEFORE_HANDOVER},
        created_at=now,
    )
    session.add(evidence)
    session.flush()
    return evidence
