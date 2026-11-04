"""Order writes: create, address PATCH, capture, tracking. Importable by app.policy/app.worker."""

from __future__ import annotations

from collections.abc import Sequence

from app.money import Money
from app.orders.enums import ShippingMethod
from app.orders.schemas import Address
from app.paypal_client.client import PayPalClient
from app.paypal_client.types import CaptureResult, CreatedOrder, ShippingOption


def create_order(
    client: PayPalClient,
    *,
    amount: Money,
    options: Sequence[ShippingOption],
    selected: ShippingMethod,
    request_id: str,
) -> CreatedOrder:
    raise NotImplementedError


def update_shipping_address(
    client: PayPalClient, paypal_order_id: str, address: Address, *, request_id: str
) -> None:
    raise NotImplementedError


def capture_order(client: PayPalClient, paypal_order_id: str, *, request_id: str) -> CaptureResult:
    """422 INSTRUMENT_DECLINED returns CaptureResult(status="DECLINED")."""
    raise NotImplementedError


def add_tracking(
    client: PayPalClient,
    *,
    paypal_order_id: str,
    capture_id: str,
    carrier: str,
    tracking_number: str,
    request_id: str,
) -> None:
    raise NotImplementedError
