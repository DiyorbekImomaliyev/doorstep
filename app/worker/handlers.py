"""Worker job handlers for order-side PayPal writes. Refunds are handled by app.policy."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.clock import Clock
from app.worker.gateway import OrdersGateway
from app.worker.models import Job

CAPTURE_ORDER = "paypal.capture_order"  # payload: {"order_id": <Doorstep order id>}
ADD_TRACKING = "paypal.add_tracking"  # payload: {"shipment_id": <Doorstep shipment id>}


class CaptureOrderHandler:
    """Stores capture_id + status; moves the order to PAID only on COMPLETED (PP-03)."""

    def __init__(self, gateway: OrdersGateway, clock: Clock) -> None:
        self.gateway = gateway
        self.clock = clock

    def run(self, session: Session, job: Job) -> None:
        raise NotImplementedError

    def on_failed(self, session: Session, job: Job, error: Exception) -> None:
        raise NotImplementedError


class AddTrackingHandler:
    def __init__(self, gateway: OrdersGateway, clock: Clock) -> None:
        self.gateway = gateway
        self.clock = clock

    def run(self, session: Session, job: Job) -> None:
        raise NotImplementedError

    def on_failed(self, session: Session, job: Job, error: Exception) -> None:
        raise NotImplementedError
