"""CK-08: an order the buyer approved in PayPal is captured even if they never come back.

Capture used to wait for the buyer's browser to return from PayPal. Now PayPal's
CHECKOUT.ORDER.APPROVED event schedules a follow-up job after a grace period, and a sweep
schedules the same job for orders whose event never arrived. The job asks PayPal for the order and
goes on only if PayPal shows it APPROVED and the buyer still has not returned: Doorstep checks the
address PayPal holds and captures through the outbox when it is deliverable. An address that
needs the buyer (a missing flat number) is recorded and waits for them; nothing is captured on it.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cases.engine import AddressCheck
from app.checkout.address import AddressChecker
from app.checkout.ports import ApprovedOrderReader
from app.checkout.service import CheckoutService
from app.clock import Clock
from app.config import Settings
from app.orders.enums import OrderState
from app.orders.models import AuditLog, Order
from app.paypal_client.errors import PayPalError
from app.policy.rules import RuleBook
from app.worker.gateway import OrdersGateway
from app.worker.kinds import COMPLETE_APPROVED
from app.worker.models import Job
from app.worker.runner import enqueue

log = logging.getLogger("doorstep.checkout")

APPROVAL_GRACE = timedelta(minutes=10)  # the buyer returning from PayPal goes first
SWEEP_AFTER = timedelta(minutes=15)  # an order this old with no return is worth asking about
SWEEP_LOOKBACK = timedelta(days=3)  # older unpaid orders are left alone
_SWEEP_BATCH = 20


def awaiting_return(order: Order) -> bool:
    """Created, approved or not, and the buyer has not come back to check the address."""
    return (
        order.state == OrderState.CREATED.value
        and order.address_check == AddressCheck.UNKNOWN.value
        and bool(order.paypal_order_id)
    )


def schedule_completion(session: Session, order: Order, *, clock: Clock, sweep: bool) -> bool:
    """Queue the follow-up job once per source (event or sweep). True if it is new."""
    key = f"{COMPLETE_APPROVED}:{order.id}" + (":sweep" if sweep else "")
    if session.scalar(select(Job.id).where(Job.dedupe_key == key)) is not None:
        return False
    run_at = clock.now() if sweep else clock.now() + APPROVAL_GRACE
    enqueue(
        session,
        COMPLETE_APPROVED,
        {"order_id": order.id},
        clock=clock,
        dedupe_key=key,
        run_at=run_at,
    )
    return True


def sweep_unreturned_approvals(session: Session, clock: Clock) -> int:
    """Orders whose buyer never came back and whose event never arrived. Returns jobs queued."""
    now = clock.now()
    rows = session.scalars(
        select(Order)
        .where(
            Order.state == OrderState.CREATED.value,
            Order.address_check == AddressCheck.UNKNOWN.value,
            Order.paypal_order_id.is_not(None),
            Order.created_at <= now - SWEEP_AFTER,
            Order.created_at >= now - SWEEP_LOOKBACK,
        )
        .order_by(Order.created_at)
        .limit(_SWEEP_BATCH)
    ).all()
    return sum(schedule_completion(session, o, clock=clock, sweep=True) for o in rows)


class CompleteApprovedHandler:
    def __init__(
        self,
        orders: OrdersGateway,
        reader: ApprovedOrderReader,
        checker: AddressChecker,
        rulebook: RuleBook,
        clock: Clock,
        settings: Settings,
    ) -> None:
        self.orders = orders
        self.reader = reader
        self.checker = checker
        self.rulebook = rulebook
        self.clock = clock
        self.settings = settings

    def run(self, session: Session, job: Job) -> None:
        order = session.get(Order, str(job.payload.get("order_id", "")))
        if order is None or not awaiting_return(order):
            return  # the buyer came back, or the order moved on: their path decides
        try:
            approved = self.reader.get_approved(str(order.paypal_order_id))
        except PayPalError as error:  # not approved yet (no address), or PayPal unreadable
            log.info("order %s: PayPal order not ready (%s)", order.id, type(error).__name__)
            return
        if approved.status != "APPROVED":
            return  # created, voided or waiting on the payer: nothing to capture
        checkout = CheckoutService(
            session,
            self.orders,
            self.reader,
            self.checker,
            self.rulebook,
            self.clock,
            self.settings,
        )
        result = checkout.on_approved(order, approved)
        session.add(
            AuditLog(
                actor="worker",
                action="checkout.completed_without_buyer",
                entity_type="order",
                entity_id=order.id,
                before={"address_check": AddressCheck.UNKNOWN.value},
                after={"address_check": order.address_check, "capture_queued": result.valid},
                at=self.clock.now(),
            )
        )
