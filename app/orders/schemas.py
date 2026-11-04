from __future__ import annotations

from pydantic import BaseModel

from app.money import Money
from app.orders.enums import ShippingMethod


class Address(BaseModel):
    name: str = ""
    line1: str
    line2: str = ""
    city: str
    postal_code: str
    country: str


class CheckoutRequest(BaseModel):
    """Body of POST /checkout/orders. PO-10: only GBP and USD are accepted."""

    seller_id: str
    amount: Money
    shipping_method: ShippingMethod
