"""TR-01..05: ship, register tracking (Shippo + PayPal), map carrier events, offer a choice on a
failed attempt, sweep stale shipments."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.orders.enums import OrderState
from app.orders.models import AuditLog, Order, Shipment, TrackingEvent
from app.shipping.events import handle_shippo_track_updated
from app.shipping.service import (
    REGISTER_TRACK,
    RegisterTrackHandler,
    ShippingError,
    ShippingService,
)
from app.shipping.shippo import ShippoClient
from app.worker.handlers import ADD_TRACKING, AddTrackingHandler
from app.worker.models import Job, WebhookInbox
from tests.factories import make_order, make_seller
from tests.fakes_journey import FakeShippo
from tests.helpers import run_jobs


@pytest.fixture
def shipping(db, clock, settings) -> ShippingService:
    return ShippingService(db, clock, settings)


def _paid(db) -> Order:
    order = make_order(db, make_seller(db), state=OrderState.PAID)
    order.shipping_method = "locker"
    db.flush()
    return order


def _event(db, tracking_number: str, status: str, when: str = "2026-11-05T10:00:00Z"):
    row = WebhookInbox(
        event_id=f"{tracking_number}-{status}-{when}",
        source="shippo",
        event_type="track_updated",
        payload={
            "event": "track_updated",
            "data": {
                "tracking_number": tracking_number,
                "tracking_status": {"status": status, "status_date": when},
            },
        },
        status="pending",
        attempts=0,
    )
    handle_shippo_track_updated(db, row)
    db.flush()


# ---------------------------------------------------------------- TR-05 / TR-01 registration


def test_TR_05_shipping_registers_tracking_on_the_paypal_payment(
    db, shipping, session_factory, fake_paypal, clock, settings
):
    order = _paid(db)
    shipment = shipping.ship(order, carrier="shippo", tracking_number="SHIPPO_TRANSIT")
    db.commit()
    run_jobs(
        session_factory, {ADD_TRACKING: AddTrackingHandler(fake_paypal, clock)}, clock, settings
    )
    [call] = fake_paypal.tracking_calls
    assert call["tracking_number"] == "SHIPPO_TRANSIT"
    assert call["capture_id"] == order.capture_id
    assert call["request_id"] == f"shipment:{shipment.id}:add_tracking"


def test_TR_01_test_tracking_number_is_registered_with_shippo(
    db, shipping, session_factory, clock, settings
):
    order = _paid(db)
    shipping.ship(order, carrier="shippo", tracking_number="SHIPPO_TRANSIT")
    db.commit()
    shippo = FakeShippo()
    run_jobs(session_factory, {REGISTER_TRACK: RegisterTrackHandler(shippo)}, clock, settings)
    assert shippo.tracks == [("shippo", "SHIPPO_TRANSIT")]


def test_TR_01_ship_moves_the_order_and_stores_the_shipment(db, shipping):
    order = _paid(db)
    shipment = shipping.ship(
        order, carrier="shippo", tracking_number="SHIPPO_DELIVERED", label_weight_g=850
    )
    assert order.state == OrderState.SHIPPED
    assert (shipment.method, shipment.label_weight_g) == ("locker", 850)
    assert shipment.delivery_address == order.shipping_address
    kinds = sorted(j.kind for j in db.scalars(select(Job)))
    assert kinds == sorted([ADD_TRACKING, REGISTER_TRACK])


def test_TR_01_ship_twice_is_refused(db, shipping):
    order = _paid(db)
    shipping.ship(order, carrier="shippo", tracking_number="SHIPPO_TRANSIT")
    with pytest.raises(ShippingError):
        shipping.ship(order, carrier="shippo", tracking_number="SHIPPO_TRANSIT")


def test_TR_01_unpaid_order_cannot_ship(db, shipping):
    order = make_order(db, make_seller(db), state=OrderState.CREATED)
    with pytest.raises(ShippingError):
        shipping.ship(order, carrier="shippo", tracking_number="SHIPPO_TRANSIT")


def test_TR_01_shippo_client_registers_a_track(httpserver):
    httpserver.expect_request("/tracks/", method="POST").respond_with_json(
        {"tracking_number": "SHIPPO_TRANSIT", "tracking_status": {"status": "TRANSIT"}}
    )
    client = ShippoClient(api_key="shippo_test_x", base_url=httpserver.url_for("").rstrip("/"))
    client.register_track("shippo", "SHIPPO_TRANSIT")
    [(request, _)] = httpserver.log
    assert request.headers["Authorization"] == "ShippoToken shippo_test_x"
    assert request.get_json() == {"carrier": "shippo", "tracking_number": "SHIPPO_TRANSIT"}


def test_CK_01_shippo_client_reports_validation_messages(httpserver):
    httpserver.expect_request("/addresses/", method="POST").respond_with_json(
        {
            "validation_results": {
                "is_valid": False,
                "messages": [{"text": "Address not found", "type": "address_error"}],
            }
        }
    )
    client = ShippoClient(api_key="shippo_test_x", base_url=httpserver.url_for("").rstrip("/"))
    from tests.fakes_journey import SAM_FIXED

    assert client.validate(SAM_FIXED) == ["Address not found"]


# ---------------------------------------------------------------- TR-02 mapping


@pytest.mark.parametrize(
    ("statuses", "final"),
    [
        (["TRANSIT"], OrderState.IN_TRANSIT),
        (["TRANSIT", "DELIVERED"], OrderState.DELIVERED),
        (["TRANSIT", "FAILURE"], OrderState.EXCEPTION),
        (["TRANSIT", "RETURNED"], OrderState.EXCEPTION),
    ],
)
def test_TR_02_carrier_status_maps_to_order_state(db, shipping, statuses, final):
    order = _paid(db)
    shipping.ship(order, carrier="shippo", tracking_number="SHIPPO_X")
    for i, status in enumerate(statuses):
        _event(db, "SHIPPO_X", status, when=f"2026-11-05T1{i}:00:00Z")
    assert db.get(Order, order.id).state == final


# ---------------------------------------------------------------- TR-03 failed attempt


def test_TR_03_failed_attempt_offers_the_buyer_a_choice(db, shipping):
    order = _paid(db)
    shipment = shipping.ship(order, carrier="shippo", tracking_number="SHIPPO_FAILURE")
    _event(db, "SHIPPO_FAILURE", "FAILURE")
    assert shipment.choice_offered == ["rebook", "locker"]


def test_TR_03_buyer_choice_is_recorded_and_audited(db, shipping):
    order = _paid(db)
    shipment = shipping.ship(order, carrier="shippo", tracking_number="SHIPPO_FAILURE")
    _event(db, "SHIPPO_FAILURE", "FAILURE")
    shipping.choose(order, "rebook")
    assert shipment.choice_made == "rebook"
    actions = [
        a.action for a in db.scalars(select(AuditLog).where(AuditLog.entity_id == shipment.id))
    ]
    assert "shipment.choice" in actions


def test_TR_03_choice_not_offered_is_refused(db, shipping):
    order = _paid(db)
    shipping.ship(order, carrier="shippo", tracking_number="SHIPPO_TRANSIT")
    with pytest.raises(ShippingError):
        shipping.choose(order, "rebook")


def test_TR_03_unknown_option_is_refused(db, shipping):
    order = _paid(db)
    shipping.ship(order, carrier="shippo", tracking_number="SHIPPO_FAILURE")
    _event(db, "SHIPPO_FAILURE", "FAILURE")
    with pytest.raises(ShippingError):
        shipping.choose(order, "teleport")


# ---------------------------------------------------------------- TR-04 stale sweep


def test_TR_04_sweep_flags_shipment_with_no_recent_event(db, shipping, clock, settings):
    order = _paid(db)
    shipment = shipping.ship(order, carrier="shippo", tracking_number="SHIPPO_PRE_TRANSIT")
    clock.advance((settings.stale_tracking_hours + 1) * 3600)
    flagged = shipping.sweep_stale()
    assert [s.id for s in flagged] == [shipment.id]
    assert shipment.stale_flagged_at == clock.now()
    assert shipping.sweep_stale() == []  # flagged once


def test_TR_04_recent_event_keeps_shipment_fresh(db, shipping, clock, settings):
    order = _paid(db)
    shipment = shipping.ship(order, carrier="shippo", tracking_number="SHIPPO_TRANSIT")
    clock.advance((settings.stale_tracking_hours + 1) * 3600)
    db.add(
        TrackingEvent(
            shipment_id=shipment.id, status="TRANSIT", occurred_at=clock.now() - timedelta(hours=1)
        )
    )
    db.flush()
    assert shipping.sweep_stale() == []


def test_TR_04_delivered_shipment_is_never_stale(db, shipping, clock, settings):
    order = _paid(db)
    shipping.ship(order, carrier="shippo", tracking_number="SHIPPO_DELIVERED")
    _event(db, "SHIPPO_DELIVERED", "DELIVERED")
    clock.advance((settings.stale_tracking_hours + 1) * 3600)
    assert shipping.sweep_stale() == []


def test_TR_04_sweep_writes_an_audit_row(db, shipping, clock, settings):
    order = _paid(db)
    shipment = shipping.ship(order, carrier="shippo", tracking_number="SHIPPO_PRE_TRANSIT")
    clock.advance((settings.stale_tracking_hours + 1) * 3600)
    shipping.sweep_stale()
    row = db.scalars(
        select(AuditLog).where(
            AuditLog.entity_id == shipment.id, AuditLog.action == "shipment.stale"
        )
    ).one()
    assert row.at == clock.now()
    assert db.get(Shipment, shipment.id).stale_flagged_at is not None
