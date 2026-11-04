"""POST /webhooks/paypal and POST /webhooks/shippo/{token}: verify, store, return fast."""

from __future__ import annotations

import hmac  # noqa: F401 - WH-04 constant-time token compare uses hmac.compare_digest
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter

from app.api.deps import AppDeps
from app.paypal_client.client import PayPalClient

router = APIRouter()


class PayPalWebhookVerifier:
    """Real verifier: calls PayPal's verify-webhook-signature through app.paypal_client.reads."""

    def __init__(self, client: PayPalClient) -> None:
        self.client = client

    def verify(self, headers: Mapping[str, str], event: dict[str, Any]) -> bool:
        raise NotImplementedError


def build_router(deps: AppDeps) -> APIRouter:
    raise NotImplementedError
