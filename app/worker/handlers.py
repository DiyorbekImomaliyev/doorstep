"""Worker job handlers for order-side PayPal writes. Refunds are handled by app.policy."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.clock import Clock
from app.orders.enums import OrderState
from app.orders.models import Order, Shipment
from app.orders.state import transition_order
from app.paypal_client.idempotency import request_id_for
from app.worker.gateway import OrdersGateway
from app.worker.models import Job
from app.worker.runner import PermanentError

log = logging.getLogger("doorstep.worker")

CAPTURE_ORDER = "paypal.capture_order"  # payload: {"order_id": <Doorstep order id>}
ADD_TRACKING = "paypal.add_tracking"  # payload: {"shipment_id": <Doorstep shipment id>}

# Doorstep/Shippo carrier token -> PayPal ShipmentCarrier member (paypal-plan.md).
# Anything else (e.g. Shippo's test carrier "shippo") is sent as OTHER.
PAYPAL_CARRIER = {
    "usps": "USPS",
    "ups": "UPS",
    "fedex": "FEDEX",
    "dhl": "DHL",
    "royal_mail": "ROYAL_MAIL",
    "parcelforce": "PARCELFORCE",
    "dpd_uk": "DPD_UK",
    "evri": "HERMES",
    "hermes": "HERMES",
}


def paypal_carrier(carrier: str) -> str:
    return PAYPAL_CARRIER.get(carrier.strip().lower(), "OTHER")


class CaptureOrderHandler:
    """Stores capture_id + status; moves the order to PAID only on COMPLETED (PP-03)."""

    def __init__(self, gateway: OrdersGateway, clock: Clock) -> None:
        self.gateway = gateway
        self.clock = clock

    def run(self, session: Session, job: Job) -> None:
        order = session.get(Order, job.payload["order_id"])
        if order is None or not order.paypal_order_id:
            raise PermanentError(f"job {job.id}: order or PayPal order id missing")
        if order.capture_status == "COMPLETED":
            return  # already captured: a redelivered job does nothing
        result = self.gateway.capture_order(
            order.paypal_order_id, request_id=request_id_for("order", order.id, "capture")
        )
        order.capture_status = result.status
        if result.capture_id:
            order.capture_id = result.capture_id
        if result.amount is not None and result.amount.amount_minor != order.amount_minor:
            log.warning(
                "order %s captured %s, expected %s", order.id, result.amount, order.amount_minor
            )
        if result.status == "COMPLETED" and order.state == OrderState.CREATED:
            transition_order(session, order, OrderState.PAID, actor="worker", reason="captured")

    def on_failed(self, session: Session, job: Job, error: Exception) -> None:
        log.error("capture job %s failed for order %s: %s", job.id, job.payload, error)


class AddTrackingHandler:
    """Registers the shipment's tracking on the PayPal order (PP-04, proof of shipment)."""

    def __init__(self, gateway: OrdersGateway, clock: Clock) -> None:
        self.gateway = gateway
        self.clock = clock

    def run(self, session: Session, job: Job) -> None:
        shipment = session.get(Shipment, job.payload["shipment_id"])
        order = session.get(Order, shipment.order_id) if shipment else None
        if shipment is None or order is None:
            raise PermanentError(f"job {job.id}: shipment or order missing")
        if not order.paypal_order_id or not order.capture_id:
            raise PermanentError(f"job {job.id}: order {order.id} has no PayPal capture")
        self.gateway.add_tracking(
            paypal_order_id=order.paypal_order_id,
            capture_id=order.capture_id,
            carrier=paypal_carrier(shipment.carrier),
            tracking_number=shipment.tracking_number,
            request_id=request_id_for("shipment", shipment.id, "add_tracking"),
        )

    def on_failed(self, session: Session, job: Job, error: Exception) -> None:
        log.error("tracking job %s failed for %s: %s", job.id, job.payload, error)
