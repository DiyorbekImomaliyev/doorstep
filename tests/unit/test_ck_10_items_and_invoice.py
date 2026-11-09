"""CK-10: the PayPal order names what was bought and carries Doorstep's invoice number.

From the Nov 7 review (B4): the purchase unit had only an amount, so the buyer's PayPal receipt,
the seller's PayPal history and any later dispute showed no item. Now each order sends:

- the item (name, SKU, quantity, unit price, PHYSICAL_GOODS) with an amount breakdown whose
  item total equals the order amount, so PayPal never sees a mismatch;
- `invoice_id` = "DS-" + the Doorstep order id. PayPal shows it in the buyer's history and emails
  and requires it to be unique per merchant account, so it is built from the full order id
  (a checkout restarted after a decline is a new order with a new invoice number);
- `custom_id` = the Doorstep order id, which PayPal returns on the capture for reconciliation.

An item whose price does not add up to the order amount is refused before anything is sent.
"""

from __future__ import annotations

import pytest
from pytest_httpserver import HTTPServer

from app.money import Currency, Money
from app.orders.enums import ShippingMethod
from app.orders.models import Order
from app.paypal_client.types import CreatedOrder, OrderItem, invoice_id_for
from app.paypal_client.writes import orders as order_writes
from app.worker.gateway import ItemisedOrders, RestOrdersGateway
from tests.fakes_journey import RecordingPayPal
from tests.unit.test_api_journey import (  # noqa: F401 - fixtures
    api,
    demo,
    services,
)
from tests.unit.test_pp_paypal_client import (  # noqa: F401 - fixtures
    GBP60,
    OPTIONS,
    ORDER_CREATED,
    api_requests,
    body_of,
    pp,
    serve_token,
)

JACKET = OrderItem(name="Black jacket, size M", sku="jacket-black-m", unit=GBP60)


class ItemisedPayPal(RecordingPayPal):
    """The journey fake, plus the itemised create the PayPal gateway offers."""

    def create_itemised_order(self, *, item, invoice_id, custom_id, **order) -> CreatedOrder:
        created = self.create_order(**order)
        self.created[-1] |= {"item": item, "invoice_id": invoice_id, "custom_id": custom_id}
        return created


@pytest.fixture
def paypal() -> ItemisedPayPal:
    return ItemisedPayPal()


def test_CK_10_the_paypal_gateway_offers_the_itemised_create():
    assert issubclass(RestOrdersGateway, ItemisedOrders)
    assert not isinstance(RecordingPayPal(), ItemisedOrders)  # it gets the plain create


def _sent(pp, httpserver: HTTPServer, **extra) -> dict:  # noqa: F811
    serve_token(httpserver)
    httpserver.expect_request("/v2/checkout/orders", method="POST").respond_with_json(
        ORDER_CREATED, status=201
    )
    order_writes.create_order(
        pp,
        amount=GBP60,
        options=OPTIONS,
        selected=ShippingMethod.HOME,
        request_id="order:o1:create",
        **extra,
    )
    return body_of(api_requests(httpserver, "/v2/checkout/orders")[-1])["purchase_units"][0]


def test_CK_10_the_order_names_the_item_with_a_matching_breakdown(pp, httpserver):  # noqa: F811
    unit = _sent(pp, httpserver, item=JACKET, invoice_id="DS-ABC", custom_id="abc")
    assert unit["items"] == [
        {
            "name": "Black jacket, size M",
            "sku": "jacket-black-m",
            "quantity": "1",
            "unit_amount": {"currency_code": "GBP", "value": "60.00"},
            "category": "PHYSICAL_GOODS",
        }
    ]
    assert unit["amount"] == {
        "currency_code": "GBP",
        "value": "60.00",
        "breakdown": {
            "item_total": {"currency_code": "GBP", "value": "60.00"},
            "shipping": {"currency_code": "GBP", "value": "0.00"},  # every option is free
        },
    }
    assert unit["invoice_id"] == "DS-ABC"
    assert unit["custom_id"] == "abc"


def test_CK_10_without_an_item_the_order_is_sent_as_before(pp, httpserver):  # noqa: F811
    unit = _sent(pp, httpserver)
    assert "items" not in unit and "invoice_id" not in unit and "custom_id" not in unit
    assert "breakdown" not in unit["amount"]


@pytest.mark.parametrize(
    "item",
    [
        OrderItem(name="Jacket", sku="j", unit=Money(amount_minor=5999, currency=Currency.GBP)),
        OrderItem(name="Jacket", sku="j", unit=Money(amount_minor=6000, currency=Currency.USD)),
        OrderItem(
            name="Jacket", sku="j", unit=Money(amount_minor=3000, currency=Currency.GBP), quantity=3
        ),
    ],
)
def test_CK_10_an_item_that_does_not_add_up_is_refused_before_paypal(
    pp,  # noqa: F811
    httpserver,
    item,
):
    serve_token(httpserver)
    with pytest.raises(ValueError, match="item total"):
        order_writes.create_order(
            pp,
            amount=GBP60,
            options=OPTIONS,
            selected=ShippingMethod.HOME,
            request_id="order:o1:create",
            item=item,
        )
    assert api_requests(httpserver, "/v2/checkout/orders") == []


def test_CK_10_long_text_is_cut_to_paypals_limits(pp, httpserver):  # noqa: F811
    long = OrderItem(name="N" * 200, sku="S" * 200, unit=GBP60)
    unit = _sent(pp, httpserver, item=long, invoice_id="I" * 200, custom_id="C" * 200)
    assert len(unit["items"][0]["name"]) == 127 and len(unit["items"][0]["sku"]) == 127
    assert len(unit["invoice_id"]) == 127 and len(unit["custom_id"]) == 127


def test_CK_10_two_quantities_add_up(pp, httpserver):  # noqa: F811
    pair = OrderItem(
        name="Socks", sku="s", unit=Money(amount_minor=3000, currency=Currency.GBP), quantity=2
    )
    unit = _sent(pp, httpserver, item=pair)
    assert unit["items"][0]["quantity"] == "2"
    assert unit["amount"]["breakdown"]["item_total"]["value"] == "60.00"


def test_CK_10_the_invoice_number_is_unique_per_order():
    a, b = (
        invoice_id_for("1a2b3c4d-0000-4000-8000-000000000001"),
        invoice_id_for("1a2b3c4d-0000-4000-8000-000000000002"),
    )
    assert a == "DS-1A2B3C4D-0000-4000-8000-000000000001"
    assert a != b  # never a shortened id: a duplicate invoice_id fails the checkout at PayPal


def test_CK_10_checkout_sends_the_shop_item_and_the_invoice_number(
    api,  # noqa: F811
    demo,  # noqa: F811
    paypal,
    session_factory,
):
    started = api.post(
        "/api/checkout/start",
        json={
            "seller_id": demo["summary"]["seller_id"],
            "amount_minor": 6000,
            "currency": "GBP",
            "buyer_country": "GB",
            "method": "home",
            "sku": "jacket-black-m",
        },
    ).json()
    [created] = paypal.created
    assert created["item"] == OrderItem(
        name="Black jacket, size M",
        sku="jacket-black-m",
        unit=Money(amount_minor=6000, currency=Currency.GBP),
    )
    assert created["invoice_id"] == invoice_id_for(started["order_id"])
    assert created["custom_id"] == started["order_id"]
    with session_factory() as s:
        assert s.get_one(Order, started["order_id"]).item_description == "Black jacket, size M"
