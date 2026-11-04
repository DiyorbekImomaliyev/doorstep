"""Shipping (TR-01, TR-03, TR-04, TR-05): ship an order, register tracking with PayPal and
Shippo through the job outbox, record the buyer's choice after a failed attempt, and flag
shipments whose tracking went quiet."""

from __future__ import annotations

from datetime import timedelta
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clock import Clock
from app.config import Settings
from app.orders.enums import OrderState, ShippingMethod
from app.orders.models import AuditLog, Order, Shipment, TrackingEvent
from app.orders.state import transition_order
from app.worker.handlers import ADD_TRACKING
from app.worker.models import Job
from app.worker.runner import enqueue

REGISTER_TRACK = "shippo.register_track"  # payload: {"shipment_id": ...}
# TR-03: what the buyer may choose after a failed attempt. Both are simulated in the demo
# (no live carrier rerouting or locker booking; SPEC section 17).
FAILED_ATTEMPT_CHOICES = ("rebook", "locker")
_SHIPPABLE = (OrderState.PAID.value, OrderState.PACKED.value)
_IN_FLIGHT = (OrderState.SHIPPED.value, OrderState.IN_TRANSIT.value)


class ShippingError(Exception):
    pass


class TrackRegistrar(Protocol):
    def register_track(self, carrier: str, tracking_number: str) -> None: ...


class RegisterTrackHandler:
    def __init__(self, registrar: TrackRegistrar) -> None:
        self.registrar = registrar

    def run(self, session: Session, job: Job) -> None:
        shipment = session.get(Shipment, job.payload.get("shipment_id", ""))
        if shipment is None:
            from app.worker.runner import PermanentError

            raise PermanentError(f"job {job.id}: shipment missing")
        self.registrar.register_track(shipment.carrier, shipment.tracking_number)

    def on_failed(self, session: Session, job: Job, error: Exception) -> None:
        return None  # the runner already wrote job.failed; tracking still arrives via PayPal


def offer_failed_attempt_choice(shipment: Shipment) -> None:
    if not shipment.choice_made:
        shipment.choice_offered = list(FAILED_ATTEMPT_CHOICES)


class ShippingService:
    def __init__(self, session: Session, clock: Clock, settings: Settings) -> None:
        self.session = session
        self.clock = clock
        self.settings = settings

    def shipment_for(self, order: Order) -> Shipment | None:
        return self.session.scalars(
            select(Shipment).where(Shipment.order_id == order.id).order_by(Shipment.created_at)
        ).first()

    def ship(
        self,
        order: Order,
        *,
        carrier: str,
        tracking_number: str,
        label_weight_g: int | None = None,
    ) -> Shipment:
        if order.state not in _SHIPPABLE:
            raise ShippingError(f"order {order.id} is {order.state}; only paid orders ship")
        if self.shipment_for(order) is not None:
            raise ShippingError(f"order {order.id} already shipped")
        shipment = Shipment(
            order_id=order.id,
            carrier=carrier.strip().lower(),
            tracking_number=tracking_number.strip(),
            method=order.shipping_method or ShippingMethod.HOME.value,
            label_weight_g=label_weight_g,
            delivery_address=dict(order.shipping_address),
            created_at=self.clock.now(),
        )
        self.session.add(shipment)
        self.session.flush()
        transition_order(self.session, order, OrderState.SHIPPED, actor="seller", reason="shipped")
        for kind in (ADD_TRACKING, REGISTER_TRACK):
            enqueue(
                self.session,
                kind,
                {"shipment_id": shipment.id},
                clock=self.clock,
                dedupe_key=f"{kind}:{shipment.id}",
            )
        return shipment

    def choose(self, order: Order, option: str) -> Shipment:
        shipment = self.shipment_for(order)
        if shipment is None or not shipment.choice_offered:
            raise ShippingError(f"order {order.id}: no choice was offered")
        if option not in shipment.choice_offered:
            raise ShippingError(f"order {order.id}: {option!r} is not an offered choice")
        shipment.choice_made = option
        self._audit(shipment, "shipment.choice", {"choice": option, "simulated": True})
        self.session.flush()
        return shipment

    def sweep_stale(self) -> list[Shipment]:
        """Flag in-flight shipments with no carrier event for longer than the limit (TR-04)."""
        now = self.clock.now()
        cutoff = now - timedelta(hours=self.settings.stale_tracking_hours)
        last_event = (
            select(TrackingEvent.shipment_id, func.max(TrackingEvent.occurred_at).label("last"))
            .group_by(TrackingEvent.shipment_id)
            .subquery()
        )
        rows = self.session.execute(
            select(Shipment, last_event.c.last)
            .join(Order, Order.id == Shipment.order_id)
            .outerjoin(last_event, last_event.c.shipment_id == Shipment.id)
            .where(Order.state.in_(_IN_FLIGHT), Shipment.stale_flagged_at.is_(None))
        ).all()
        flagged = []
        for shipment, last in rows:
            latest = max(t for t in (last, shipment.created_at) if t is not None)
            if latest < cutoff:
                shipment.stale_flagged_at = now
                self._audit(shipment, "shipment.stale", {"last_event_at": latest.isoformat()})
                flagged.append(shipment)
        self.session.flush()
        return flagged

    def _audit(self, shipment: Shipment, action: str, after: dict[str, object]) -> None:
        self.session.add(
            AuditLog(
                actor="shipping",
                action=action,
                entity_type="shipment",
                entity_id=shipment.id,
                before={},
                after=after,
                at=self.clock.now(),
            )
        )
