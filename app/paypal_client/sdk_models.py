"""Glue between Doorstep types and the generated PayPal SDK models.

Request bodies are built from SDK models (validated against PayPal's spec) and serialized with
to_dict(); success bodies are decoded with SDK models. Every model and field name here was looked
up in the SDK's own map (APIMatic), never written from memory. SDK `Optional` means "may be
UNSET", never None.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from paypal.core import UnsetType
from paypal.models import Order as SdkOrder
from paypal.models import OrdersCapture
from paypal.models import Refund as SdkRefund

from app.money import Money


def money_from_sdk(value: Any) -> Money | None:
    """SDK Money (or UNSET) -> Doorstep Money."""
    if value is None or isinstance(value, UnsetType):
        return None
    return Money.from_paypal(value.value, value.currency_code)


def text(value: Any) -> str | None:
    """An SDK string or open enum (or UNSET) -> plain wire string."""
    if value is None or isinstance(value, UnsetType):
        return None
    return str(value.value) if hasattr(value, "value") else str(value)


def decode_order(body: dict[str, Any]) -> SdkOrder:
    return SdkOrder.model_validate(body)


def _items(value: Any) -> list[Any]:
    return [] if value is None or isinstance(value, UnsetType) else list(value)


def texts(value: Any) -> list[str]:
    """An SDK list of strings or open enums (or UNSET) -> plain wire strings."""
    return [s for s in (text(v) for v in _items(value)) if s]


def captures(order: SdkOrder) -> Iterable[OrdersCapture]:
    for unit in _items(order.purchase_units):
        payments = unit.payments
        if not isinstance(payments, UnsetType):
            yield from _items(payments.captures)


def refunds(order: SdkOrder) -> Iterable[SdkRefund]:
    for unit in _items(order.purchase_units):
        payments = unit.payments
        if not isinstance(payments, UnsetType):
            yield from _items(payments.refunds)


def approve_link(order: SdkOrder) -> str | None:
    for link in _items(order.links):
        if link.rel in ("approve", "payer-action"):
            return link.href
    return None
