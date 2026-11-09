"""Ports the checkout needs from PayPal and the address validator (faked in tests)."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from app.orders.schemas import Address


class ApprovedOrder(BaseModel):
    """What PayPal returns for an approved order: the payer and the shipping address."""

    paypal_order_id: str
    payer_id: str
    address: Address
    # CK-08: PayPal's order status. A capture follows only on APPROVED, which is what the buyer's
    # return from PayPal implies, hence the default.
    status: str = "APPROVED"


class ApprovedOrderReader(Protocol):
    def get_approved(self, paypal_order_id: str) -> ApprovedOrder: ...


class AddressValidator(Protocol):
    """External validation (Shippo). Returns human-readable problems; empty means valid."""

    def validate(self, address: Address) -> list[str]: ...
