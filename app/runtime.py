"""Builds the running system from environment variables (CLAUDE.md "Environment variables").

One place reads the environment, so the web service, the worker and the scripts are wired the
same way. Missing credentials degrade honestly: without PayPal keys, checkout answers 503 and
says why; without a model key, explanations use template text. In production a missing signing
key stops startup, because receipts signed by a throwaway key could never be verified later.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.ai.explainer import Explainer
from app.api.deps import AppDeps, Services, ServiceUnavailable
from app.clock import Clock, SystemClock
from app.config import Settings
from app.db import Base, import_all_models, make_engine, make_session_factory
from app.proof.receipts import ReceiptSigner
from app.security.links import LinkSigner

log = logging.getLogger("doorstep.runtime")

ROOT = Path(__file__).resolve().parents[1]


class ConfigError(RuntimeError):
    """A required setting is missing; the message names it (never its value)."""


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


class NotConfigured:
    """Stands in for an external service whose keys are not set. Every call fails clearly."""

    def __init__(self, what: str, variables: str) -> None:
        self.message = f"{what} is not configured: set {variables}"

    def __getattr__(self, name: str) -> Any:
        def fail(*args: Any, **kwargs: Any) -> Any:
            raise ServiceUnavailable(self.message)

        return fail


@dataclass
class Runtime:
    deps: AppDeps
    services: Services
    session_factory: sessionmaker[Session]
    clock: Clock
    settings: Settings
    paypal: Any  # PayPalClient | None
    shippo: Any  # ShippoClient | None


def _app_base_url() -> str:
    """APP_BASE_URL, else the URL Render gives the service, else local development."""
    url = env("APP_BASE_URL") or env("RENDER_EXTERNAL_URL") or "http://localhost:8000"
    return url.rstrip("/")


def _settings() -> Settings:
    return Settings(
        shippo_webhook_token=env("SHIPPO_WEBHOOK_TOKEN"),
        stale_tracking_hours=int(env("STALE_TRACKING_HOURS", "72")),
    )


def _signer_from_secret(secret: str) -> ReceiptSigner:
    """A base64 Ed25519 seed is used as is. Any other secret (e.g. one a host generated) is
    stretched with SHA-256 into a seed, so every process given the same secret gets one key."""
    try:
        raw = base64.b64decode(secret, validate=True)
    except binascii.Error:
        raw = b""
    if len(raw) == 32:
        return ReceiptSigner.from_seed_b64(secret)
    if len(secret) < 16:
        raise ConfigError("RECEIPT_SIGNING_KEY must be a base64 32-byte seed or 16+ characters")
    seed = hashlib.sha256(b"doorstep-receipt-key:" + secret.encode()).digest()
    return ReceiptSigner.from_seed_b64(base64.b64encode(seed).decode("ascii"))


def _dev_key_file(database_url: str) -> Path | None:
    """A local SQLite file keeps its development receipt key beside it (gitignored).

    Receipts outlive the process; a key that changed on every restart would make yesterday's
    receipts fail verification. In-memory and server databases get a throwaway key instead.
    """
    prefix = "sqlite:///"
    if not database_url.startswith(prefix) or ":memory:" in database_url:
        return None
    return Path(database_url.removeprefix(prefix) + ".receipt-key")


def _receipt_signer(environment: str, database_url: str = "") -> ReceiptSigner:
    seed = env("RECEIPT_SIGNING_KEY")
    if seed:
        return _signer_from_secret(seed)
    if environment == "production":
        raise ConfigError("RECEIPT_SIGNING_KEY is required in production")
    key_file = _dev_key_file(database_url)
    if key_file is not None:
        if key_file.exists():
            return ReceiptSigner.from_seed_b64(key_file.read_text().strip())
        signer = ReceiptSigner.generate()
        key_file.write_text(signer.seed_b64)
        key_file.chmod(0o600)
        log.warning("RECEIPT_SIGNING_KEY not set: development key stored beside the database")
        return signer
    log.warning("RECEIPT_SIGNING_KEY not set: using a throwaway key (development only)")
    return ReceiptSigner.generate()


def _link_secret(environment: str) -> str:
    secret = env("SIGNING_SECRET")
    if len(secret) >= 16:
        return secret
    if environment == "production":
        raise ConfigError("SIGNING_SECRET (16+ characters) is required in production")
    log.warning("SIGNING_SECRET not set: using a development-only secret")
    return "development-only-signing-secret"


def build_runtime(*, create_tables: bool = True) -> Runtime:
    environment = env("APP_ENV", "development")
    clock = SystemClock()
    settings = _settings()
    import_all_models()
    database_url = env("DATABASE_URL", f"sqlite:///{ROOT / 'doorstep.db'}")
    engine = make_engine(database_url)
    if create_tables:
        Base.metadata.create_all(engine)  # ADR-024: no migrations in the hackathon build
    session_factory = make_session_factory(engine)

    paypal = None
    orders: Any = NotConfigured("PayPal", "PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET")
    approved: Any = orders
    verifier: Any = NotConfigured("PayPal webhook verification", "PAYPAL_WEBHOOK_ID")
    if env("PAYPAL_CLIENT_ID") and env("PAYPAL_CLIENT_SECRET"):
        from app.api.webhooks import PayPalWebhookVerifier
        from app.paypal_client.approved import PayPalApprovedOrderReader
        from app.paypal_client.client import PayPalClient, PayPalConfig
        from app.worker.gateway import RestOrdersGateway

        base_url = env("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
        if "sandbox" not in base_url:
            raise ConfigError("PAYPAL_BASE_URL must be the sandbox in this build")
        paypal = PayPalClient(
            PayPalConfig(
                client_id=env("PAYPAL_CLIENT_ID"),
                client_secret=env("PAYPAL_CLIENT_SECRET"),
                base_url=base_url,
                webhook_id=env("PAYPAL_WEBHOOK_ID"),
            ),
            clock,
        )
        app_url = _app_base_url()
        orders = RestOrdersGateway(
            paypal, return_url=f"{app_url}/return", cancel_url=f"{app_url}/return?cancelled=1"
        )
        approved = PayPalApprovedOrderReader(paypal)
        if env("PAYPAL_WEBHOOK_ID"):
            verifier = PayPalWebhookVerifier(paypal)

    shippo = None
    validator = None
    if env("SHIPPO_API_KEY"):
        from app.shipping.shippo import ShippoClient

        shippo = ShippoClient(api_key=env("SHIPPO_API_KEY"))
        validator = shippo

    from app.ai.llm import adapter_from_env

    llm = adapter_from_env(purpose="runtime")
    services = Services(
        links=LinkSigner(_link_secret(environment), clock),
        receipts=_receipt_signer(environment, database_url),
        orders=orders,
        approved=approved,
        address_validator=validator,
        explainer=Explainer(llm),
        environment=environment,
        app_base_url=_app_base_url(),
    )
    deps = AppDeps(
        settings=settings,
        session_factory=session_factory,
        paypal_verifier=verifier,
        clock=clock,
        rules_dir=ROOT / "rules",
        services=services,
    )
    return Runtime(deps, services, session_factory, clock, settings, paypal, shippo)
