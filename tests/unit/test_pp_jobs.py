"""PP-03 / PP-04 at the worker level: capture and tracking jobs against the fake gateway."""

from __future__ import annotations

from sqlalchemy import select

from app.orders.enums import OrderState
from app.orders.models import Order
from app.paypal_client.errors import PayPalClientError, PayPalServerError
from app.paypal_client.types import CaptureResult
from app.worker.handlers import ADD_TRACKING, CAPTURE_ORDER, AddTrackingHandler, CaptureOrderHandler
from app.worker.models import Job
from app.worker.runner import JobRunner, enqueue
from tests.factories import audit_rows, make_order, make_seller, make_shipment
from tests.helpers import money


def _created_order(db):
    seller = make_seller(db)
    return make_order(
        db,
        seller,
        state=OrderState.CREATED,
        capture_id=None,
        capture_status=None,
        paypal_order_id="PPO-CAP",
    )


def _run_capture(db, session_factory, clock, settings, fake_paypal, order: Order) -> None:
    enqueue(db, CAPTURE_ORDER, {"order_id": order.id}, clock=clock)
    db.commit()
    JobRunner(
        session_factory, {CAPTURE_ORDER: CaptureOrderHandler(fake_paypal, clock)}, clock, settings
    ).run_due()
    db.expire_all()


def test_PP_03_completed_capture_stores_capture_id_and_moves_order_to_paid(
    db, session_factory, clock, settings, fake_paypal
):
    order = _created_order(db)
    fake_paypal.capture_results.append(
        CaptureResult(status="COMPLETED", capture_id="CAP-9", amount=money(6000))
    )
    _run_capture(db, session_factory, clock, settings, fake_paypal, order)
    order = db.get(Order, order.id)
    assert order.capture_id == "CAP-9"
    assert order.capture_status == "COMPLETED"
    assert order.state == OrderState.PAID
    assert any(r.after.get("state") == OrderState.PAID for r in audit_rows(db, order.id))


def test_PP_03_pending_capture_stores_status_and_stays_created(
    db, session_factory, clock, settings, fake_paypal
):
    order = _created_order(db)
    fake_paypal.capture_results.append(CaptureResult(status="PENDING", capture_id="CAP-P"))
    _run_capture(db, session_factory, clock, settings, fake_paypal, order)
    order = db.get(Order, order.id)
    assert order.capture_status == "PENDING"
    assert order.state == OrderState.CREATED


def test_PP_03_declined_capture_stores_status_and_stays_created(
    db, session_factory, clock, settings, fake_paypal
):
    order = _created_order(db)
    fake_paypal.capture_results.append(CaptureResult(status="DECLINED"))
    _run_capture(db, session_factory, clock, settings, fake_paypal, order)
    order = db.get(Order, order.id)
    assert order.capture_status == "DECLINED"
    assert order.state == OrderState.CREATED


def test_PP_03_capture_uses_order_capture_request_id(
    db, session_factory, clock, settings, fake_paypal
):
    from app.paypal_client.idempotency import request_id_for

    order = _created_order(db)
    fake_paypal.capture_results.append(CaptureResult(status="COMPLETED", capture_id="CAP-9"))
    _run_capture(db, session_factory, clock, settings, fake_paypal, order)
    assert fake_paypal.capture_calls == [("PPO-CAP", request_id_for("order", order.id, "capture"))]


def _tracking_setup(db, clock):
    seller = make_seller(db)
    order = make_order(
        db, seller, state=OrderState.SHIPPED, paypal_order_id="PPO-T", capture_id="CAP-T"
    )
    shipment = make_shipment(db, order, tracking_number="SHIPPO_TRANSIT")
    job = enqueue(db, ADD_TRACKING, {"shipment_id": shipment.id}, clock=clock)
    db.commit()
    return shipment, job


def test_PP_04_tracking_job_sends_carrier_and_tracking_number(
    db, session_factory, clock, settings, fake_paypal
):
    _shipment, _ = _tracking_setup(db, clock)
    JobRunner(
        session_factory, {ADD_TRACKING: AddTrackingHandler(fake_paypal, clock)}, clock, settings
    ).run_due()
    assert len(fake_paypal.tracking_calls) == 1
    call = fake_paypal.tracking_calls[0]
    assert call["paypal_order_id"] == "PPO-T"
    assert call["capture_id"] == "CAP-T"
    assert call["tracking_number"] == "SHIPPO_TRANSIT"
    assert call["carrier"]


def test_PP_04_failed_tracking_job_retries_with_backoff(
    db, session_factory, clock, settings, fake_paypal
):
    _, job = _tracking_setup(db, clock)
    fake_paypal.tracking_failures.extend([PayPalServerError(503), PayPalServerError(503)])
    runner = JobRunner(
        session_factory, {ADD_TRACKING: AddTrackingHandler(fake_paypal, clock)}, clock, settings
    )
    start = clock.now()

    runner.run_due()
    db.expire_all()
    first = db.get(Job, job.id)
    assert first.attempts == 1 and first.status == "queued"
    assert (first.run_at - start).total_seconds() == settings.backoff_base_s

    assert runner.run_due() == 0  # not due yet
    clock.advance(settings.backoff_base_s)
    runner.run_due()
    db.expire_all()
    second = db.get(Job, job.id)
    assert second.attempts == 2
    assert (second.run_at - clock.now()).total_seconds() == settings.backoff_base_s * 2

    clock.advance(settings.backoff_base_s * 2)
    runner.run_due()
    db.expire_all()
    assert db.get(Job, job.id).status == "done"
    assert len(fake_paypal.tracking_calls) == 3


def test_PP_04_tracking_job_marked_failed_with_audit_after_max_attempts(
    db, session_factory, clock, settings, fake_paypal
):
    _, job = _tracking_setup(db, clock)
    fake_paypal.tracking_failures.extend([PayPalServerError(503)] * 10)
    runner = JobRunner(
        session_factory, {ADD_TRACKING: AddTrackingHandler(fake_paypal, clock)}, clock, settings
    )
    for _ in range(settings.max_attempts + 2):
        runner.run_due()
        clock.advance(10_000)
    db.expire_all()
    failed = db.get(Job, job.id)
    assert failed is not None, "a failed job must never be deleted"
    assert failed.status == "failed"
    assert failed.attempts == settings.max_attempts
    assert failed.last_error
    assert len(fake_paypal.tracking_calls) == settings.max_attempts
    assert any(r.action == "job.failed" for r in audit_rows(db, job.id))


def test_PP_04_tracking_4xx_fails_at_once(db, session_factory, clock, settings, fake_paypal):
    _, job = _tracking_setup(db, clock)
    fake_paypal.tracking_failures.append(PayPalClientError(422, "UNPROCESSABLE_ENTITY"))
    JobRunner(
        session_factory, {ADD_TRACKING: AddTrackingHandler(fake_paypal, clock)}, clock, settings
    ).run_due()
    db.expire_all()
    assert db.scalars(select(Job).where(Job.id == job.id)).one().status == "failed"
    assert len(fake_paypal.tracking_calls) == 1
