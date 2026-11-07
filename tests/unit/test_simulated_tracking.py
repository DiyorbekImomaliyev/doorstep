"""A simulated order's tracking never reaches PayPal.

Simulated demo orders carry `SIM-` capture ids that PayPal has never seen. Refunds already route
them to the simulator; the tracking job did not, so every simulated shipment sent PayPal a write
it answers with 400 INVALID_REQUEST, and the job ended failed (16 such jobs in one local day).
"""

from __future__ import annotations

from sqlalchemy import select

from app.orders.enums import OrderState
from app.orders.models import AuditLog
from app.worker.handlers import ADD_TRACKING, AddTrackingHandler
from app.worker.models import Job
from app.worker.runner import JobRunner, enqueue
from tests.factories import make_order, make_seller, make_shipment


def _shipped(db, clock, *, capture_id: str):
    order = make_order(
        db,
        make_seller(db),
        state=OrderState.SHIPPED,
        paypal_order_id="SIM-PPO-1" if capture_id.startswith("SIM-") else "PPO-1",
        capture_id=capture_id,
    )
    shipment = make_shipment(db, order, tracking_number="SHIPPO_TRANSIT")
    job = enqueue(db, ADD_TRACKING, {"shipment_id": shipment.id}, clock=clock)
    db.commit()
    return shipment, job


def _run(session_factory, clock, settings, paypal) -> None:
    JobRunner(
        session_factory, {ADD_TRACKING: AddTrackingHandler(paypal, clock)}, clock, settings
    ).run_due()


def test_a_simulated_orders_tracking_is_not_sent_to_paypal(
    db, session_factory, clock, settings, fake_paypal
):
    shipment, job = _shipped(db, clock, capture_id="SIM-CAP-1")
    _run(session_factory, clock, settings, fake_paypal)
    db.expire_all()
    assert fake_paypal.tracking_calls == []
    assert db.get(Job, job.id).status == "done"
    [row] = db.scalars(select(AuditLog).where(AuditLog.action == "tracking.simulated")).all()
    assert (row.entity_type, row.entity_id, row.at) == ("shipment", shipment.id, clock.now())
    assert row.after == {"tracking_number": "SHIPPO_TRANSIT", "paypal": "not called"}


def test_a_real_orders_tracking_still_goes_to_paypal(
    db, session_factory, clock, settings, fake_paypal
):
    _shipped(db, clock, capture_id="CAP-REAL-1")
    _run(session_factory, clock, settings, fake_paypal)
    assert [c["capture_id"] for c in fake_paypal.tracking_calls] == ["CAP-REAL-1"]
    assert db.scalars(select(AuditLog).where(AuditLog.action == "tracking.simulated")).all() == []
