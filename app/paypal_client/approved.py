"""Reads the payer and shipping address of an approved order (CK-01), before capture."""

from __future__ import annotations

from typing import Any

from app.checkout.ports import ApprovedOrder
from app.orders.schemas import Address
from app.paypal_client import reads
from app.paypal_client.client import PayPalClient
from app.paypal_client.errors import PayPalServerError


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def parse_approved_order(body: dict[str, Any]) -> ApprovedOrder:
    units = body.get("purchase_units")
    unit = _dict(units[0]) if isinstance(units, list) and units else {}
    shipping = _dict(unit.get("shipping"))
    address = _dict(shipping.get("address"))
    if not address.get("address_line_1") or not address.get("country_code"):
        raise PayPalServerError(None, f"order {body.get('id')} has no shipping address")
    return ApprovedOrder(
        paypal_order_id=str(body.get("id") or ""),
        payer_id=str(_dict(body.get("payer")).get("payer_id") or ""),
        status=str(body.get("status") or ""),
        address=Address(
            name=str(_dict(shipping.get("name")).get("full_name") or ""),
            line1=str(address.get("address_line_1") or ""),
            line2=str(address.get("address_line_2") or ""),
            city=str(address.get("admin_area_2") or ""),
            postal_code=str(address.get("postal_code") or ""),
            country=str(address.get("country_code") or ""),
        ),
    )


class PayPalApprovedOrderReader:
    def __init__(self, client: PayPalClient) -> None:
        self.client = client

    def get_approved(self, paypal_order_id: str) -> ApprovedOrder:
        return parse_approved_order(reads.get_order(self.client, paypal_order_id))
