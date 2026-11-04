"""Operational settings (timeouts, retries, sizes). Business rules live in rules/*.yaml."""

from __future__ import annotations

from pydantic import BaseModel


class Settings(BaseModel):
    token_refresh_margin_s: int = 300
    paypal_timeout_s: float = 10.0
    max_attempts: int = 5
    backoff_base_s: float = 30.0
    max_webhook_body_bytes: int = 256_000
    shippo_webhook_token: str = ""
    stale_tracking_hours: int = 72
