"""Thin PayPal REST client: OAuth token cache, one retry on 401, logging (PP-01, PP-06)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.clock import Clock

LOGGER_NAME = "doorstep.paypal"


class PayPalConfig(BaseModel):
    client_id: str
    client_secret: str
    base_url: str
    webhook_id: str = ""
    timeout_s: float = 10.0
    token_refresh_margin_s: int = 300


class PayPalClient:
    def __init__(self, config: PayPalConfig, clock: Clock) -> None:
        self.config = config
        self.clock = clock

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        request_id: str | None = None,
        files: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send an authenticated request; map errors to app.paypal_client.errors."""
        raise NotImplementedError
