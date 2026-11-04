"""PR-06: on the public site the filmed demo cannot be opened; judges get scratch copies."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.deps import AppDeps, Services
from app.main import create_app
from app.proof.receipts import ReceiptSigner
from app.security.links import LinkSigner


def _client(session_factory, settings, clock, rules_dir, environment) -> TestClient:
    services = Services(
        links=LinkSigner("x" * 32, clock),
        receipts=ReceiptSigner.generate(),
        orders=None,
        approved=None,
        environment=environment,
    )
    deps = AppDeps(
        settings=settings,
        session_factory=session_factory,
        paypal_verifier=None,
        clock=clock,
        rules_dir=rules_dir,
        services=services,
    )
    return TestClient(create_app(deps))


def test_PR_06_filmed_demo_is_not_public_in_production(session_factory, settings, clock):
    from app.runtime import ROOT

    api = _client(session_factory, settings, clock, ROOT / "rules", "production")
    assert api.post("/demo/seed?scratch=0").status_code == 403
    assert api.post("/demo/seed?scratch=1").status_code == 200
