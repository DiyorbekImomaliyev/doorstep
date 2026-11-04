"""Bootstrap from environment variables, and the worker wiring (inbox + jobs + receipts)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.proof.models import Receipt
from app.runtime import ConfigError, build_runtime

KEYS = (
    "PAYPAL_CLIENT_ID",
    "PAYPAL_CLIENT_SECRET",
    "PAYPAL_WEBHOOK_ID",
    "SHIPPO_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "SIGNING_SECRET",
    "RECEIPT_SIGNING_KEY",
)


@pytest.fixture
def env(monkeypatch, tmp_path):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'rt.db'}")
    monkeypatch.setenv("APP_ENV", "development")
    return monkeypatch


def test_development_runs_without_any_external_keys(env):
    rt = build_runtime()
    api = TestClient(create_app(rt.deps))
    health = api.get("/healthz").json()
    assert health["environment"] == "development" and health["model"] == "template"
    seeded = api.post("/demo/seed?scratch=1").json()
    assert seeded["seller_url"].startswith("http://localhost:8000/seller/")


def test_checkout_without_paypal_keys_says_what_is_missing(env):
    rt = build_runtime()
    api = TestClient(create_app(rt.deps), raise_server_exceptions=False)
    seller = api.post("/demo/seed?scratch=1").json()["summary"]["seller_id"]
    response = api.post(
        "/api/checkout/start",
        json={
            "seller_id": seller,
            "amount_minor": 6000,
            "currency": "GBP",
            "buyer_country": "GB",
            "method": "home",
        },
    )
    assert response.status_code == 503
    assert "PAYPAL_CLIENT_ID" in response.json()["detail"]


def test_production_refuses_to_start_without_a_receipt_key(env):
    env.setenv("APP_ENV", "production")
    env.setenv("SIGNING_SECRET", "x" * 32)
    with pytest.raises(ConfigError, match="RECEIPT_SIGNING_KEY"):
        build_runtime()


def test_production_refuses_a_live_paypal_url(env):
    from app.proof.receipts import ReceiptSigner

    env.setenv("RECEIPT_SIGNING_KEY", ReceiptSigner.generate().seed_b64)
    env.setenv("PAYPAL_CLIENT_ID", "id")
    env.setenv("PAYPAL_CLIENT_SECRET", "secret")
    env.setenv("PAYPAL_BASE_URL", "https://api-m.paypal.com")
    with pytest.raises(ConfigError, match="sandbox"):
        build_runtime()


def test_worker_settles_a_seeded_refund_and_signs_a_refund_receipt(env):
    from app.worker.main import build_worker

    rt = build_runtime()
    api = TestClient(create_app(rt.deps))
    seeded = api.post("/demo/seed?scratch=1").json()
    mug = next(o for o in seeded["summary"]["orders"] if o["key"] == "damaged-mug")
    _, runner = build_worker(rt)
    assert runner.run_due() >= 1
    with rt.session_factory() as s:
        kinds = [
            r.kind for r in s.scalars(select(Receipt).where(Receipt.case_id == mug["case_id"]))
        ]
    assert kinds == ["decision", "refund"]
    page = api.get(f"/api/seller/{seeded['seller_token']}/cases/{mug['case_id']}").json()
    assert page["state"] == "SETTLED" and page["money"][0]["status"] == "simulated"
    assert api.get(f"/cases/{mug['case_id']}/verify").json()["status"] == "ok"
