"""Read-only PayPal calls. Any module may import this."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.paypal_client.client import PayPalClient
from app.paypal_client.types import CaptureState


def get_order(client: PayPalClient, paypal_order_id: str) -> dict[str, Any]:
    raise NotImplementedError


def get_capture_state(client: PayPalClient, paypal_order_id: str, capture_id: str) -> CaptureState:
    """Capture status and total already refunded (PP-08). Failed/cancelled refunds don't count."""
    raise NotImplementedError


def verify_webhook_signature(
    client: PayPalClient, *, headers: Mapping[str, str], event: dict[str, Any]
) -> bool:
    """POST /v1/notifications/verify-webhook-signature. 5xx/timeout -> PayPalServerError."""
    raise NotImplementedError
