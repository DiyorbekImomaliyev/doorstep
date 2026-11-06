"""PP-03, the unhappy side: a capture that does not complete leaves the order CREATED, and both
the buyer and the seller are told why, instead of waiting on "Checking your address" forever.

Found live on Nov 6: the worker used another PayPal app's keys, PayPal answered 404 on capture,
and the buyer page never changed."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.api.views import buyer_view, order_row
from app.orders.enums import OrderState
from app.orders.models import AuditLog, Order
from app.paypal_client.errors import PayPalClientError
from app.paypal_client.types import CaptureResult
from app.worker.handlers import CAPTURE_ORDER, CaptureOrderHandler
from app.worker.models import Job
from app.worker.runner import JobRunner, enqueue
from tests.factories import make_order, make_seller


def _created_order(db):
    return make_order(
        db,
        make_seller(db),
        state=OrderState.CREATED,
        capture_id=None,
        capture_status=None,
        paypal_order_id="PPO-FAIL",
    )


def _run(db, session_factory, clock, settings, fake_paypal, order: Order) -> Order:
    enqueue(db, CAPTURE_ORDER, {"order_id": order.id}, clock=clock)
    db.commit()
    JobRunner(
        session_factory, {CAPTURE_ORDER: CaptureOrderHandler(fake_paypal, clock)}, clock, settings
    ).run_due()
    db.expire_all()
    got = db.get(Order, order.id)
    assert got is not None
    return got


def test_PP_03_10_a_capture_paypal_rejects_is_marked_failed_and_audited(
    db, session_factory, clock, settings, fake_paypal
):
    order = _created_order(db)
    fake_paypal.capture_results.append(
        PayPalClientError(404, "RESOURCE_NOT_FOUND", debug_id="dbg-1")
    )
    order = _run(db, session_factory, clock, settings, fake_paypal, order)
    assert order.state == OrderState.CREATED  # no money moved, no state change
    assert order.capture_status == "FAILED" and order.capture_id is None
    job = db.scalars(select(Job).where(Job.kind == CAPTURE_ORDER)).one()
    assert job.status == "failed" and "RESOURCE_NOT_FOUND" in (job.last_error or "")
    audit = db.scalars(
        select(AuditLog).where(
            AuditLog.entity_id == order.id, AuditLog.action == "checkout.capture_failed"
        )
    ).one()
    assert audit.after == {"capture_status": "FAILED", "error": "PayPalClientError"}


@pytest.mark.parametrize(
    ("capture_status", "buyer", "seller"),
    [
        ("FAILED", "Payment didn't go through", "Payment failed"),
        ("DECLINED", "Payment didn't go through", "Payment declined"),
        ("PENDING", "Payment processing", "Payment pending at PayPal"),
        (None, "Waiting for payment", "Waiting for payment"),
    ],
)
def test_PP_03_11_buyer_and_seller_are_told_why_payment_is_not_complete(
    db, capture_status, buyer, seller
):
    order = _created_order(db)
    order.capture_status = capture_status
    db.flush()
    view = buyer_view(db, order)
    assert view.status == buyer and view.capture_status == capture_status
    assert order_row(db, order).status == seller


def test_PP_03_12_a_completed_capture_is_never_marked_failed(
    db, session_factory, clock, settings, fake_paypal
):
    order = _created_order(db)
    fake_paypal.capture_results.append(CaptureResult(status="COMPLETED", capture_id="CAP-OK"))
    order = _run(db, session_factory, clock, settings, fake_paypal, order)
    handler = CaptureOrderHandler(fake_paypal, clock)
    job = db.scalars(select(Job).where(Job.kind == CAPTURE_ORDER)).one()
    handler.on_failed(db, job, RuntimeError("late redelivery"))
    assert order.capture_status == "COMPLETED" and order.state == OrderState.PAID
