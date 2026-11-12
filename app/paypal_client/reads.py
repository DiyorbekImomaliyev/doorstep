"""Read-only PayPal calls. Any module may import this."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from app.money import Money
from app.paypal_client.client import PayPalClient
from app.paypal_client.errors import PayPalClientError, PayPalServerError
from app.paypal_client.sdk_models import captures, decode_order, money_from_sdk, refunds, text
from app.paypal_client.types import CaptureState, DisputeDetail, DisputeSummary, RefundInfo

# Refund statuses that hold or have moved money. FAILED/CANCELLED refunds don't count (PP-08).
COUNTED_REFUND_STATUSES = frozenset({"COMPLETED", "PENDING"})

# The disputes list rejects a start_time 180 or more days back (INVALID_START_TIME_RANGE, checked
# in the sandbox on Nov 6, 2026). Ten days of margin; an older order goes to a person.
DISPUTE_LIST_MAX_AGE = timedelta(days=170)
_DISPUTE_PAGE_SIZE = 50
_DISPUTE_MAX_PAGES = 10  # 500 disputes: more than that is not something to page through blind

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


def list_open_disputes_since(client: PayPalClient, since: datetime) -> list[DisputeSummary]:
    """Every unresolved dispute created since `since`, with the captures each one names.

    The list is filtered by time, not by transaction (the transaction filter answers 400 when
    there is nothing to find). An unresolved dispute the list does not tie to a capture is read
    once for it. Any status other than RESOLVED
    counts as open, so a status PayPal adds later fails closed. A page link without a token, or
    more pages than the cap, raises a retryable error: the refund waits rather than guesses.
    """
    query = {
        "start_time": since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "page_size": str(_DISPUTE_PAGE_SIZE),
    }
    found: list[DisputeSummary] = []
    for _ in range(_DISPUTE_MAX_PAGES):
        page = client.request("GET", "/v1/customer/disputes?" + urlencode(query))
        for item in page.get("items") or []:
            status = str(item.get("status") or "").upper()
            if status == "RESOLVED":
                continue
            dispute_id = str(item.get("dispute_id") or "")
            if "disputed_transactions" in item:  # the list already names them: no extra read
                captures_named = _seller_captures(item)
            elif dispute_id:
                captures_named = _seller_captures(
                    client.request("GET", f"/v1/customer/disputes/{dispute_id}")
                )
            else:
                captures_named = []
            found.append(
                DisputeSummary(
                    dispute_id=dispute_id,
                    status=status,
                    capture_ids=captures_named,
                    reason=str(item.get("reason") or ""),
                )
            )
        next_href = next(
            (link.get("href") for link in page.get("links") or [] if link.get("rel") == "next"),
            None,
        )
        if next_href is None:
            return found
        tokens = parse_qs(urlsplit(str(next_href)).query).get("next_page_token")
        if not tokens:
            raise PayPalServerError(None, "disputes list: next page without a token")
        query["next_page_token"] = tokens[0]
    raise PayPalServerError(None, f"disputes list: more than {_DISPUTE_MAX_PAGES} pages")


def get_dispute(client: PayPalClient, dispute_id: str) -> DisputeDetail:
    """GET /v1/customer/disputes/{id}, reduced to what a refund offer needs (PP-09).

    Fails closed: a missing stage, offer list or amount reads as "no offer allowed" or "no
    amount", and any seller entry in the offer history counts as an offer already made."""
    body = client.request("GET", f"/v1/customer/disputes/{dispute_id}")
    offer = body.get("offer") or {}
    allowed = (body.get("allowed_response_options") or {}).get("make_offer") or {}
    amount = body.get("dispute_amount") or {}
    history = [h for h in offer.get("history") or [] if isinstance(h, dict)]
    return DisputeDetail(
        dispute_id=str(body.get("dispute_id") or dispute_id),
        status=str(body.get("status") or "").upper(),
        stage=str(body.get("dispute_life_cycle_stage") or "").upper(),
        capture_ids=_seller_captures(body),
        amount=(
            Money.from_paypal(str(amount["value"]), str(amount["currency_code"]))
            if amount.get("value") and amount.get("currency_code")
            else None
        ),
        offer_types=[str(t).upper() for t in allowed.get("offer_types") or []],
        seller_offer_made=bool(offer.get("seller_offered_amount"))
        or any(str(h.get("actor") or "").upper() == "SELLER" for h in history),
        outcome_code=str((body.get("dispute_outcome") or {}).get("outcome_code") or ""),
    )


def _seller_captures(dispute: Mapping[str, Any]) -> list[str]:
    """disputed_transactions[].seller_transaction_id: the captures a dispute names."""
    return [
        str(tx["seller_transaction_id"])
        for tx in dispute.get("disputed_transactions") or []
        if isinstance(tx, dict) and tx.get("seller_transaction_id")
    ]


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
