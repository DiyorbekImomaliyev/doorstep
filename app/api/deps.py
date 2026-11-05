from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.orm import Session, sessionmaker

from app.clock import Clock
from app.config import Settings


class ServiceUnavailable(RuntimeError):
    """An external service is not configured or not reachable -> 503 with the reason."""


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
    services: Services | None = None


# Components that are simulated in the hackathon build. /healthz names every one (PR-05).
SIMULATED_COMPONENTS: dict[str, str] = {
    "lockers": "Locker list and locker booking are simulated; no real locker is reserved.",
    "rerouting": "Rebook and redirect choices are recorded, not sent to a carrier.",
    "carrier_claims": "Carrier claim packs are prepared and tracked by Doorstep and submitted by "
    "the seller on the carrier's site; Doorstep never files on a carrier portal. Carrier event "
    "locations in the seeded demo are simulated.",
    "scratch_refunds": "Refunds on seeded scratch orders (capture ids starting SIM-) are "
    "simulated; refunds on real sandbox checkouts go to PayPal.",
    "risk_model": "Delivery risk comes from a model trained on synthetic data (model card).",
}


@dataclass
class Services:
    """Everything the buyer, seller and demo routes need. Optional on AppDeps so the webhook-only
    app (and its locked tests) is built exactly as before."""

    links: Any  # app.security.links.LinkSigner
    receipts: Any  # app.proof.receipts.ReceiptSigner
    orders: Any  # app.worker.gateway.OrdersGateway
    approved: Any  # app.checkout.ports.ApprovedOrderReader
    address_validator: Any = None  # app.checkout.ports.AddressValidator
    explainer: Any = None  # app.ai.explainer.Explainer; None -> template only
    packing: Any = None  # app.ai.packing.PackingChecker; None -> every photo is "check"
    agent_llm: Any = None  # planner for the case agent; None -> the fixed plan
    agent_tools: Any = None  # PayPal read tools (Agent Toolkit); None -> simulated reads only
    environment: str = "development"  # development | production
    app_base_url: str = "http://localhost:8000"
    reset_phrase: str = "RESET DEMO DATA"
