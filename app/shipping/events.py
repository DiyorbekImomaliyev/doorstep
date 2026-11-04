"""Shippo track_updated handling: map carrier status, apply to the order via state.py."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.orders.models import Order, Shipment, TrackingEvent
from app.orders.state import CarrierEventOutcome, CarrierStatus, apply_carrier_event
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
    status_block = data.get("tracking_status")
    status_block = status_block if isinstance(status_block, dict) else {}
    shipment = session.scalars(
        select(Shipment).where(Shipment.tracking_number == tracking_number)
    ).first()
    if shipment is None:
        log.info("shippo event %s for unknown tracking number; skipped", row.event_id)
        return
    order = session.get(Order, shipment.order_id)
    if order is None:  # pragma: no cover - FK guarantees the order exists
        return
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
