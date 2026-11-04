"""Read-only PayPal calls. Any module may import this."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.money import Money
from app.paypal_client.client import PayPalClient
from app.paypal_client.errors import PayPalClientError, PayPalServerError
from app.paypal_client.sdk_models import captures, decode_order, money_from_sdk, refunds, text
from app.paypal_client.types import CaptureState, RefundInfo

# Refund statuses that hold or have moved money. FAILED/CANCELLED refunds don't count (PP-08).
COUNTED_REFUND_STATUSES = frozenset({"COMPLETED", "PENDING"})

# Webhook headers PayPal signs with (lower-case; HTTP header names are case-insensitive).
_SIGNATURE_HEADERS = {
    "auth_algo": "paypal-auth-algo",
    "cert_url": "paypal-cert-url",
    "transmission_id": "paypal-transmission-id",
    "transmission_sig": "paypal-transmission-sig",
    "transmission_time": "paypal-transmission-time",
}


def get_order(client: PayPalClient, paypal_order_id: str) -> dict[str, Any]:
    return client.request("GET", f"/v2/checkout/orders/{paypal_order_id}")


def get_capture_state(client: PayPalClient, paypal_order_id: str, capture_id: str) -> CaptureState:
    """Capture status and total already refunded (PP-08). Failed/cancelled refunds don't count."""
    order = decode_order(get_order(client, paypal_order_id))
    capture = next((c for c in captures(order) if text(c.id) == capture_id), None)
    if capture is None:
        raise PayPalClientError(404, "CAPTURE_NOT_FOUND", {"capture_id": capture_id})
    amount = money_from_sdk(capture.amount)
    status = text(capture.status)
    if amount is None or status is None:  # outcome unknown: never guess a capture state
        raise PayPalServerError(None, f"capture {capture_id} has no amount or status")
    refunded = Money(amount_minor=0, currency=amount.currency)
    listed: list[RefundInfo] = []
    for refund in refunds(order):
        value = money_from_sdk(refund.amount)
        refund_status = text(refund.status)
        if refund_status in COUNTED_REFUND_STATUSES and value is not None:
            refunded = refunded + value  # CurrencyMismatch if PayPal mixed currencies
            listed.append(
                RefundInfo(
                    refund_id=text(refund.id) or "",
                    status=refund_status,
                    amount=value,
                    custom_id=text(refund.custom_id),
                )
            )
    return CaptureState(
        capture_id=capture_id,
        status=status,
        amount=amount,
        refunded_total=refunded,
        refunds=listed,
    )


def verify_webhook_signature(
    client: PayPalClient, *, headers: Mapping[str, str], event: dict[str, Any]
) -> bool:
    """POST /v1/notifications/verify-webhook-signature. 5xx/timeout -> PayPalServerError."""
    lower = {k.lower(): v for k, v in headers.items()}
    fields = {name: lower.get(header, "") for name, header in _SIGNATURE_HEADERS.items()}
    if not all(fields.values()) or not client.config.webhook_id:
        return False  # unsigned or not configured: never trusted
    body = client.request(
        "POST",
        "/v1/notifications/verify-webhook-signature",
        json={**fields, "webhook_id": client.config.webhook_id, "webhook_event": event},
    )
    return body.get("verification_status") == "SUCCESS"
