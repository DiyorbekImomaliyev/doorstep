"""POST /webhooks/paypal and POST /webhooks/shippo/{token}: verify, store, return fast.

Nothing is processed here (WH-02): a verified event is written to webhook_inbox (unique
event_id) and the worker processes it. Responses:
- 200: stored, or already stored (duplicate deliveries are harmless, WH-03);
- 400: malformed JSON, oversized body, or a PayPal signature that does not verify (WH-01, WH-07);
- 404: wrong Shippo URL token, compared in constant time (WH-04);
- 503: PayPal's verify endpoint failed or timed out, so PayPal retries (WH-06).
"""

from __future__ import annotations

import hmac
import json
import logging
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import IntegrityError

from app.api.deps import AppDeps, VerifierUnavailable
from app.paypal_client import reads
from app.paypal_client.client import PayPalClient
from app.paypal_client.errors import PayPalAuthError, PayPalClientError, PayPalServerError
from app.shipping.webhooks import shippo_event_id
from app.worker.models import WebhookInbox

log = logging.getLogger("doorstep.webhooks")


class PayPalWebhookVerifier:
    """Real verifier: calls PayPal's verify-webhook-signature through app.paypal_client.reads."""

    def __init__(self, client: PayPalClient) -> None:
        self.client = client

    def verify(self, headers: Mapping[str, str], event: dict[str, Any]) -> bool:
        try:
            return reads.verify_webhook_signature(self.client, headers=headers, event=event)
        except (PayPalServerError, PayPalAuthError) as exc:  # PayPal down or our token broken
            raise VerifierUnavailable(str(exc)) from exc
        except PayPalClientError:  # PayPal refused to verify these headers: not trusted
            return False


async def _read_json(request: Request, limit: int) -> dict[str, Any]:
    """Body as a JSON object, or 400. The body is streamed and cut off past the limit."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(status_code=400, detail="body too large")
        chunks.append(chunk)
    try:
        payload = json.loads(b"".join(chunks))
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="malformed JSON") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="expected a JSON object")
    return payload


def _store(
    deps: AppDeps, *, event_id: str, source: str, event_type: str, payload: dict[str, Any]
) -> bool:
    """Insert into webhook_inbox. False if this event id was already stored (WH-03)."""
    with deps.session_factory() as session:
        session.add(
            WebhookInbox(
                event_id=event_id[:128],
                source=source,
                event_type=event_type[:128],
                payload=payload,
                status="pending",
                attempts=0,
                received_at=deps.clock.now(),
            )
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return False
    return True


def build_router(deps: AppDeps) -> APIRouter:
    router = APIRouter()
    limit = deps.settings.max_webhook_body_bytes

    @router.post("/webhooks/paypal")
    async def paypal_webhook(request: Request) -> dict[str, Any]:
        event = await _read_json(request, limit)
        event_id, event_type = event.get("id"), event.get("event_type")
        if not isinstance(event_id, str) or not event_id or not isinstance(event_type, str):
            raise HTTPException(status_code=400, detail="missing id or event_type")
        try:
            verified = deps.paypal_verifier.verify(dict(request.headers), event)
        except VerifierUnavailable:
            log.warning("PayPal verify endpoint unavailable; event %s not stored", event_id)
            raise HTTPException(status_code=503, detail="verification unavailable") from None
        if not verified:
            log.warning("PayPal webhook %s failed signature verification", event_id)
            raise HTTPException(status_code=400, detail="invalid signature")
        stored = _store(
            deps, event_id=event_id, source="paypal", event_type=event_type, payload=event
        )
        return {"status": "stored" if stored else "duplicate"}

    @router.post("/webhooks/shippo/{token}")
    async def shippo_webhook(token: str, request: Request) -> dict[str, Any]:
        expected = deps.settings.shippo_webhook_token
        token_ok = hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8"))
        if not token_ok or not expected:
            raise HTTPException(status_code=404, detail="not found")
        payload = await _read_json(request, limit)
        raw_id = payload.get("id") or payload.get("event_id")
        event_id = raw_id if isinstance(raw_id, str) and raw_id else shippo_event_id(payload)
        event_type = str(payload.get("event") or "unknown")
        stored = _store(
            deps, event_id=event_id, source="shippo", event_type=event_type, payload=payload
        )
        return {"status": "stored" if stored else "duplicate"}

    return router
