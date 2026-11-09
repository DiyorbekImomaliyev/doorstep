"""CK-09: after PayPal declines the buyer's payment method, they can pay another way.

Found by the Nov 7 review: a declined capture left the buyer on "Payment didn't go through" with
nowhere to go. PayPal's guidance for INSTRUMENT_DECLINED is to restart the payment so the buyer
can choose a different payment option, never to retry the one that was declined. The buyer page
now offers "Pay another way": a new PayPal order for the same item, at the price the shop charges
(CK-07), with its own PayPal-Request-Id. Only a DECLINED capture qualifies, because PayPal said no
money moved; a capture that failed for another reason is not restarted.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.demo.seed import DEMO_SKU
from app.orders.models import AuditLog, Order, Product
from app.worker.handlers import CAPTURE_ORDER, CaptureOrderHandler
from tests.fakes_journey import SAM_FIXED
from tests.helpers import run_jobs
from tests.unit.test_api_journey import (  # noqa: F401 - fixtures
    api,
    demo,
    paypal,
    services,
)


def _declined(api, demo, paypal, session_factory, clock, settings) -> dict:  # noqa: F811
    """A buyer who approved, whose payment PayPal then declined at capture."""
    started = api.post(
        "/api/checkout/start",
        json={
            "seller_id": demo["summary"]["seller_id"],
            "amount_minor": 6000,
            "currency": "GBP",
            "buyer_country": "GB",
            "method": "locker",
            "sku": DEMO_SKU,
        },
    ).json()
    paypal.approve(started["paypal_order_id"], SAM_FIXED)
    assert api.post(f"/api/buyer/{started['buyer_token']}/approved").json()["valid"] is True
    paypal.will_capture("DECLINED", capture_id="")
    run_jobs(session_factory, {CAPTURE_ORDER: CaptureOrderHandler(paypal, clock)}, clock, settings)
    return started


def _pay_again(api, token: str):  # noqa: F811
    return api.post(f"/api/buyer/{token}/pay-again")


def test_CK_09_the_buyer_page_offers_another_way_only_after_a_decline(
    api,  # noqa: F811
    demo,  # noqa: F811
    paypal,  # noqa: F811
    session_factory,
    clock,
    settings,
):
    started = _declined(api, demo, paypal, session_factory, clock, settings)
    view = api.get(f"/api/buyer/{started['buyer_token']}").json()
    assert view["capture_status"] == "DECLINED"
    assert view["can_pay_again"] is True
    paid = demo["buyers"]["sam-jacket"]
    assert api.get(f"/api/buyer/{paid}").json()["can_pay_again"] is False


def test_CK_09_a_declined_payment_restarts_as_a_new_paypal_order_for_the_same_item(
    api,  # noqa: F811
    demo,  # noqa: F811
    paypal,  # noqa: F811
    session_factory,
    clock,
    settings,
):
    started = _declined(api, demo, paypal, session_factory, clock, settings)
    response = _pay_again(api, started["buyer_token"])
    assert response.status_code == 200
    body = response.json()
    assert body["approve_url"].startswith("https://")
    first, again = paypal.created
    assert again["paypal_order_id"] != first["paypal_order_id"]
    assert again["request_id"] != first["request_id"]  # never the declined request replayed
    assert again["amount"] == first["amount"]
    assert again["selected"] == first["selected"]
    with session_factory() as s:
        old = s.get_one(Order, started["order_id"])
        new = s.get_one(
            Order,
            s.scalars(
                select(Order.id).where(Order.paypal_order_id == again["paypal_order_id"])
            ).one(),
        )
        assert (new.item_description, new.buyer_country, new.state) == (
            old.item_description,
            old.buyer_country,
            "CREATED",
        )
        assert new.capture_status is None
        link = s.scalars(
            select(AuditLog).where(
                AuditLog.entity_id == old.id, AuditLog.action == "checkout.paid_another_way"
            )
        ).one()
        assert link.after["order_id"] == new.id
    # the new buyer link opens the new order
    assert api.get(f"/api/buyer/{body['buyer_token']}").json()["state"] == "CREATED"


def test_CK_09_a_second_tap_returns_the_same_new_order(
    api,  # noqa: F811
    demo,  # noqa: F811
    paypal,  # noqa: F811
    session_factory,
    clock,
    settings,
):
    started = _declined(api, demo, paypal, session_factory, clock, settings)
    one = _pay_again(api, started["buyer_token"]).json()
    two = _pay_again(api, started["buyer_token"]).json()
    assert one["approve_url"] == two["approve_url"]
    assert len(paypal.created) == 2  # the checkout and one restart, not two


def test_CK_09_once_the_new_order_is_approved_the_buyer_goes_to_its_page_not_paypal(
    api,  # noqa: F811
    demo,  # noqa: F811
    paypal,  # noqa: F811
    session_factory,
    clock,
    settings,
):
    started = _declined(api, demo, paypal, session_factory, clock, settings)
    first = _pay_again(api, started["buyer_token"]).json()
    paypal.approve(paypal.created[-1]["paypal_order_id"], SAM_FIXED)
    api.post(f"/api/buyer/{first['buyer_token']}/approved")
    again = _pay_again(api, started["buyer_token"]).json()
    assert again["approve_url"] is None
    assert api.get(f"/api/buyer/{again['buyer_token']}").json()["address_check"] == "valid"


@pytest.mark.parametrize("status", ["FAILED", "PENDING", None])
def test_CK_09_only_a_declined_payment_is_restarted(
    api,  # noqa: F811
    demo,  # noqa: F811
    paypal,  # noqa: F811
    session_factory,
    clock,
    settings,
    status,
):
    started = _declined(api, demo, paypal, session_factory, clock, settings)
    with session_factory() as s:
        s.get_one(Order, started["order_id"]).capture_status = status
        s.commit()
    assert _pay_again(api, started["buyer_token"]).status_code == 409
    assert len(paypal.created) == 1  # nothing new reached PayPal


def test_CK_09_a_paid_order_is_never_restarted(api, demo, paypal):  # noqa: F811
    assert _pay_again(api, demo["buyers"]["sam-jacket"]).status_code == 409
    assert paypal.created == []


def test_CK_09_a_price_changed_since_is_not_charged_silently(
    api,  # noqa: F811
    demo,  # noqa: F811
    paypal,  # noqa: F811
    session_factory,
    clock,
    settings,
):
    started = _declined(api, demo, paypal, session_factory, clock, settings)
    with session_factory() as s:
        product = s.scalars(
            select(Product).where(Product.seller_id == demo["summary"]["seller_id"])
        ).one()
        product.prices = {"GBP": 7000, "USD": 7000}
        s.commit()
    response = _pay_again(api, started["buyer_token"])
    assert response.status_code == 409
    assert "70.00" in response.json()["detail"]
    assert len(paypal.created) == 1


def test_CK_09_an_item_the_shop_no_longer_sells_is_not_restarted(
    api,  # noqa: F811
    demo,  # noqa: F811
    paypal,  # noqa: F811
    session_factory,
    clock,
    settings,
):
    started = _declined(api, demo, paypal, session_factory, clock, settings)
    with session_factory() as s:
        s.get_one(Order, started["order_id"]).item_description = "Something else"
        s.commit()
    response = _pay_again(api, started["buyer_token"])
    assert response.status_code == 404
    assert len(paypal.created) == 1


def test_CK_09_a_bad_link_is_refused(api):  # noqa: F811
    assert _pay_again(api, "not-a-token").status_code in (401, 403, 404)
