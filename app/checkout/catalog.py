"""CK-07: the price a checkout charges comes from the seller's product table, never the browser.

The shop page shows the product and sends back the amount it showed. The checkout looks the item
up again and charges the stored price; if the two differ (an edited request, or a price changed
while the page was open) it refuses before anything reaches PayPal, so the buyer never pays an
amount they did not see.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.checkout.service import CheckoutError
from app.money import Currency, Money
from app.orders.models import Product, Seller


class CatalogError(CheckoutError):
    """The request names no item the shop sells at that price. `status` is the HTTP answer."""

    status = 422


class UnknownItem(CatalogError):
    status = 404


class PriceChanged(CatalogError):
    status = 409


def products(session: Session, seller_id: str) -> list[Product]:
    """The seller's products, in a stable order. Unknown seller: UnknownItem."""
    if session.get(Seller, seller_id) is None:
        raise UnknownItem(f"unknown seller {seller_id}")
    return list(
        session.scalars(select(Product).where(Product.seller_id == seller_id).order_by(Product.sku))
    )


def charge_for(
    session: Session, seller_id: str, *, sku: str | None, currency: Currency, shown_minor: int
) -> tuple[Product, Money]:
    """The item and the price to charge. `sku` may be left out only when the shop sells one item."""
    items = products(session, seller_id)
    if not items:
        raise UnknownItem(f"seller {seller_id} has nothing for sale")
    if sku is None:
        if len(items) != 1:
            raise CatalogError("this shop sells several items: say which one (sku)")
        item = items[0]
    else:
        found = [p for p in items if p.sku == sku]
        if not found:
            raise UnknownItem(f"seller {seller_id} does not sell {sku!r}")
        item = found[0]
    amount_minor = item.prices.get(currency.value)
    if amount_minor is None:
        raise CatalogError(f"{item.name} is not sold in {currency.value}")
    price = Money(amount_minor=amount_minor, currency=currency)
    if shown_minor != amount_minor:
        raise PriceChanged(f"the price of {item.name} is {price}; reload the page to see it")
    return item, price
