from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.orm import Session, sessionmaker

from app.clock import Clock
from app.config import Settings


class VerifierUnavailable(Exception):
    """PayPal's verify endpoint failed or timed out -> respond 503 (WH-06)."""


class WebhookVerifier(Protocol):
    def verify(self, headers: Mapping[str, str], event: dict[str, Any]) -> bool: ...


@dataclass
class AppDeps:
    settings: Settings
    session_factory: sessionmaker[Session]
    paypal_verifier: WebhookVerifier
    clock: Clock
    rules_dir: Path = Path("rules")
