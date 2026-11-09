"""Order-side PayPal writes used by worker handlers (create, PATCH, capture, tracking)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from app.money import Money
from app.orders.enums import ShippingMethod
from app.orders.schemas import Address
from app.paypal_client.client import PayPalClient
from app.paypal_client.types import CaptureResult, CreatedOrder, OrderItem, ShippingOption
from app.paypal_client.writes import orders as order_writes


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


@runtime_checkable
class ItemisedOrders(Protocol):
    """CK-10: a gateway that lists the item and Doorstep's invoice and order ids on the PayPal
    order. The PayPal gateway does; a gateway without it gets the plain create_order."""

    def create_itemised_order(
        self,
        *,
        amount: Money,
        options: Sequence[ShippingOption],
        selected: ShippingMethod,
        request_id: str,
        item: OrderItem | None,
        invoice_id: str,
        custom_id: str,
    ) -> CreatedOrder: ...


class RestOrdersGateway:
    """OrdersGateway over app.paypal_client.writes.orders (never writes.money, PO-01)."""

    def __init__(
        self, client: PayPalClient, *, return_url: str | None = None, cancel_url: str | None = None
    ) -> None:
        self.client = client
        self.return_url = return_url
        self.cancel_url = cancel_url

    def create_order(
        self,
        *,
        amount: Money,
        options: Sequence[ShippingOption],
        selected: ShippingMethod,
        request_id: str,
    ) -> CreatedOrder:
        return order_writes.create_order(
            self.client,
            amount=amount,
            options=options,
            selected=selected,
            request_id=request_id,
            return_url=self.return_url,
            cancel_url=self.cancel_url,
        )

    def create_itemised_order(
        self,
        *,
        amount: Money,
        options: Sequence[ShippingOption],
        selected: ShippingMethod,
        request_id: str,
        item: OrderItem | None,
        invoice_id: str,
        custom_id: str,
    ) -> CreatedOrder:
        return order_writes.create_order(
            self.client,
            amount=amount,
            options=options,
            selected=selected,
            request_id=request_id,
            return_url=self.return_url,
            cancel_url=self.cancel_url,
            item=item,
            invoice_id=invoice_id,
            custom_id=custom_id,
        )

    def update_shipping_address(
        self, paypal_order_id: str, address: Address, *, request_id: str
    ) -> None:
        order_writes.update_shipping_address(
            self.client, paypal_order_id, address, request_id=request_id
        )

    def capture_order(self, paypal_order_id: str, *, request_id: str) -> CaptureResult:
        return order_writes.capture_order(self.client, paypal_order_id, request_id=request_id)

    def add_tracking(
        self,
        *,
        paypal_order_id: str,
        capture_id: str,
        carrier: str,
        tracking_number: str,
        request_id: str,
    ) -> None:
        order_writes.add_tracking(
            self.client,
            paypal_order_id=paypal_order_id,
            capture_id=capture_id,
            carrier=carrier,
            tracking_number=tracking_number,
            request_id=request_id,
        )
