"""Shippo track_updated handling: map carrier status, apply to the order via state.py."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.orders.models import Order, Shipment, TrackingEvent
from app.orders.state import CarrierEventOutcome, CarrierStatus, apply_carrier_event
from app.shipping.shippo import ShippoClient
from app.worker.models import WebhookInbox

log = logging.getLogger("doorstep.shipping")


def _parse_time(value: Any) -> datetime:
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)


def _status(value: Any) -> CarrierStatus:
    try:
        return CarrierStatus(str(value).upper())
    except ValueError:
        return CarrierStatus.UNKNOWN


def handle_shippo_track_updated(session: Session, row: WebhookInbox) -> None:
    """Record every carrier event; apply it to the order only if it moves the order forward.

    Out-of-order and repeat events are recorded with applied=False and never fail the inbox
    row (SM-09). An event for a tracking number Doorstep does not know is logged and skipped.
    """
    data = row.payload.get("data")
    data = data if isinstance(data, dict) else {}
    tracking_number = str(data.get("tracking_number") or "")
    shipment = session.scalars(
        select(Shipment)
        .where(Shipment.tracking_number == tracking_number)
        .order_by(Shipment.created_at.desc())  # Shippo test numbers are shared: newest wins
    ).first()
    if shipment is None:
        log.info("shippo event %s for unknown tracking number; skipped", row.event_id)
        return
    apply_track(session, shipment, data, received_at=row.received_at)


def refresh_from_carrier(
    session: Session, order: Order, shippo: ShippoClient, *, now: datetime
) -> None:
    """DI-06: before a dispute is answered, ask Shippo where the parcel is now and apply it to this
    order's shipment as the webhook would have (a server Shippo cannot reach gets no webhook).
    ShippoError propagates; the caller decides what a failed read means."""
    shipment = session.scalars(
        select(Shipment).where(Shipment.order_id == order.id).order_by(Shipment.created_at.desc())
    ).first()
    if shipment is None or not shipment.tracking_number:
        return
    data = shippo.get_track(shipment.carrier, shipment.tracking_number)
    apply_track(session, shipment, data, received_at=now)


def apply_track(
    session: Session, shipment: Shipment, data: dict[str, Any], *, received_at: datetime
) -> None:
    """One Shippo track object applied to one shipment, from the webhook or a carrier read."""
    order = session.get(Order, shipment.order_id)
    if order is None:  # pragma: no cover - FK guarantees the order exists
        return
    status_block = data.get("tracking_status")
    status_block = status_block if isinstance(status_block, dict) else {}
    status = _status(status_block.get("status"))
    event = TrackingEvent(
        shipment_id=shipment.id,
        status=status.value,
        occurred_at=_parse_time(status_block.get("status_date")),
        raw=data,
        applied=False,
    )
    session.add(event)
    outcome = apply_carrier_event(session, order, status, actor="shippo")
    event.applied = outcome == CarrierEventOutcome.APPLIED
    if status == CarrierStatus.DELIVERED:  # AD-01: the parcel may turn up after a report
        from app.cases.late_delivery import record_delivered_scan

        session.flush()
        record_delivered_scan(session, order, event, now=received_at)
    if status == CarrierStatus.FAILURE:  # TR-03: the buyer picks rebook or locker
        from app.shipping.service import offer_failed_attempt_choice

        offer_failed_attempt_choice(shipment)
