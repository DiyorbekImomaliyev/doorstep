"""Order-side PayPal writes used by worker handlers (create, PATCH, capture, tracking)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.money import Money
from app.orders.enums import ShippingMethod
from app.orders.schemas import Address
from app.paypal_client.client import PayPalClient
from app.paypal_client.types import CaptureResult, CreatedOrder, ShippingOption


class OrdersGateway(Protocol):
    def create_order(
        self,
        *,
        amount: Money,
        options: Sequence[ShippingOption],
        selected: ShippingMethod,
        request_id: str,
    ) -> CreatedOrder: ...

    def update_shipping_address(
        self, paypal_order_id: str, address: Address, *, request_id: str
    ) -> None: ...

    def capture_order(self, paypal_order_id: str, *, request_id: str) -> CaptureResult: ...

    def add_tracking(
        self,
        *,
        paypal_order_id: str,
        capture_id: str,
        carrier: str,
        tracking_number: str,
        request_id: str,
    ) -> None: ...


class RestOrdersGateway:
    def __init__(self, client: PayPalClient) -> None:
        self.client = client

    def create_order(
        self,
        *,
        amount: Money,
        options: Sequence[ShippingOption],
        selected: ShippingMethod,
        request_id: str,
    ) -> CreatedOrder:
        raise NotImplementedError

    def update_shipping_address(
        self, paypal_order_id: str, address: Address, *, request_id: str
    ) -> None:
        raise NotImplementedError

    def capture_order(self, paypal_order_id: str, *, request_id: str) -> CaptureResult:
        raise NotImplementedError

    def add_tracking(
        self,
        *,
        paypal_order_id: str,
        capture_id: str,
        carrier: str,
        tracking_number: str,
        request_id: str,
    ) -> None:
        raise NotImplementedError
