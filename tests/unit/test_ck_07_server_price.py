"""CK-07: the server sets the price; the browser only shows it.

Found by the Nov 7 review: the checkout took `amount_minor` from the browser, so a buyer could edit
the request and pay any amount. Each seller now has a product table. The checkout charges the
product's price in the buyer's currency and names the item from the same row; a request whose
amount differs from that price is refused before anything reaches PayPal.
"""

from __future__ import annotations

from sqlalchemy import select

from app.demo.seed import DEMO_SKU, backfill_demo_products
from app.money import Currency, Money
from app.orders.models import Order, Product
from tests.factories import make_seller
from tests.unit.test_api_journey import (  # noqa: F401 - fixtures
    api,
    demo,
    paypal,
    services,
)


def _start(api, seller_id: str, **changes):  # noqa: F811
    body = {
        "seller_id": seller_id,
        "amount_minor": 6000,
        "currency": "GBP",
        "buyer_country": "GB",
        "method": "home",
    } | changes
    return api.post("/api/checkout/start", json=body)


def _seller(demo) -> str:  # noqa: F811
    return demo["summary"]["seller_id"]


def test_CK_07_the_shop_lists_its_products_at_server_prices(api, demo):  # noqa: F811
    response = api.get(f"/api/shop/{_seller(demo)}/products")
    assert response.status_code == 200
    assert response.json() == [
        {"sku": DEMO_SKU, "name": "Black jacket, size M", "prices": {"GBP": 6000, "USD": 6000}}
    ]


def test_CK_07_an_unknown_shop_has_no_products(api):  # noqa: F811
    assert api.get("/api/shop/nope/products").status_code == 404


def test_CK_07_a_price_the_shop_does_not_charge_is_refused(api, demo, paypal):  # noqa: F811
    response = _start(api, _seller(demo), amount_minor=1)
    assert response.status_code == 409
    assert "60.00" in response.json()["detail"]
    assert paypal.created == []  # nothing reached PayPal


def test_CK_07_the_order_takes_the_price_and_name_from_the_server(
    api,  # noqa: F811
    demo,  # noqa: F811
    paypal,  # noqa: F811
    session_factory,
):
    response = _start(api, _seller(demo), sku=DEMO_SKU, item_description="Free gift")
    assert response.status_code == 200
    [created] = paypal.created
    assert created["amount"] == Money(amount_minor=6000, currency=Currency.GBP)
    with session_factory() as s:
        order = s.get(Order, response.json()["order_id"])
        assert order is not None
        assert order.item_description == "Black jacket, size M"
        assert order.amount_minor == 6000


def test_CK_07_a_us_buyer_pays_the_usd_price(api, demo, paypal):  # noqa: F811
    response = _start(api, _seller(demo), currency="USD", buyer_country="US")
    assert response.status_code == 200
    assert paypal.created[0]["amount"] == Money(amount_minor=6000, currency=Currency.USD)


def test_CK_07_an_unknown_item_is_404(api, demo, paypal):  # noqa: F811
    assert _start(api, _seller(demo), sku="nope").status_code == 404
    assert paypal.created == []


def test_CK_07_a_shop_with_nothing_for_sale_refuses_checkout(api, paypal, session_factory):  # noqa: F811
    with session_factory() as s:
        seller = make_seller(s)
        s.commit()
        seller_id = seller.id
    response = _start(api, seller_id)
    assert response.status_code == 404
    assert paypal.created == []


def test_CK_07_an_item_not_sold_in_the_buyers_currency_is_refused(
    api,  # noqa: F811
    demo,  # noqa: F811
    paypal,  # noqa: F811
    session_factory,
):
    with session_factory() as s:
        s.add(Product(seller_id=_seller(demo), sku="scarf", name="Scarf", prices={"GBP": 2000}))
        s.commit()
    response = _start(api, _seller(demo), sku="scarf", currency="USD", amount_minor=2000)
    assert response.status_code == 422
    assert paypal.created == []


def test_CK_07_a_shop_with_several_items_needs_the_sku(api, demo, paypal, session_factory):  # noqa: F811
    with session_factory() as s:
        s.add(Product(seller_id=_seller(demo), sku="scarf", name="Scarf", prices={"GBP": 2000}))
        s.commit()
    assert _start(api, _seller(demo)).status_code == 422
    assert _start(api, _seller(demo), sku="scarf", amount_minor=2000).status_code == 200


def test_CK_07_a_new_demo_shop_sells_the_jacket(db, rulebook, clock, settings):
    from app.demo.seed import seed_scenario
    from app.proof.receipts import ReceiptSigner

    summary = seed_scenario(
        db,
        rulebook=rulebook,
        clock=clock,
        settings=settings,
        signer=ReceiptSigner.generate(),
        tag="ck07",
    )
    skus = db.scalars(select(Product.sku).where(Product.seller_id == summary.seller_id)).all()
    assert skus == [DEMO_SKU]


def test_CK_07_startup_gives_older_demo_shops_the_jacket_once(db):
    old_demo = make_seller(db)
    old_demo.scratch_tag = "seeded-before-ck07"
    real_seller = make_seller(db)
    db.flush()
    assert backfill_demo_products(db) == 1
    assert backfill_demo_products(db) == 0  # a second start adds nothing
    rows = db.scalars(select(Product.seller_id)).all()
    assert rows == [old_demo.id]  # a real seller's catalog is never touched
    assert real_seller.id not in rows
