"""RK-02 in the Control Room: each parcel not yet delivered shows its live risk and reasons."""

from __future__ import annotations

from app.api.views import order_row
from app.orders.enums import OrderState
from tests.factories import make_order, make_seller


def test_RK_02_in_flight_order_shows_risk_for_its_method(db):
    seller = make_seller(db)
    home = make_order(db, seller, state=OrderState.IN_TRANSIT)
    home.shipping_method = "home"
    locker = make_order(db, seller, state=OrderState.IN_TRANSIT)
    locker.shipping_method = "locker"
    db.flush()
    r_home, r_locker = order_row(db, home), order_row(db, locker)
    assert r_home.delivery_risk is not None and r_locker.delivery_risk is not None
    assert r_locker.delivery_risk < r_home.delivery_risk
    assert any("Locker pickup" in t for t in r_locker.risk_reasons)
    assert all("_" not in t for t in r_home.risk_reasons)


def test_RK_02_overridden_address_raises_the_shown_risk(db):
    seller = make_seller(db)
    ok = make_order(db, seller, state=OrderState.SHIPPED)
    ok.address_check = "valid"
    kept = make_order(db, seller, state=OrderState.SHIPPED)
    kept.address_check = "overridden"
    db.flush()
    assert order_row(db, kept).delivery_risk > order_row(db, ok).delivery_risk  # type: ignore[operator]


def test_RK_02_no_risk_once_delivered(db):
    order = make_order(db, make_seller(db), state=OrderState.DELIVERED)
    row = order_row(db, order)
    assert row.delivery_risk is None and row.risk_reasons == []
