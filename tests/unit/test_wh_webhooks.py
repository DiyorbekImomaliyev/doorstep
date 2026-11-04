"""WH — Webhooks: verify, store in webhook_inbox, return fast; the worker processes."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pytest_httpserver import HTTPServer
from sqlalchemy import func, select
from werkzeug import Request, Response

import app.api.webhooks as webhooks_module
from app.api.deps import AppDeps
from app.api.webhooks import PayPalWebhookVerifier
from app.main import create_app
from app.paypal_client.client import PayPalClient, PayPalConfig
from app.shipping.webhooks import shippo_event_id
from app.worker.inbox import InboxProcessor
from app.worker.models import WebhookInbox
from tests.conftest import RULES_DIR

PAYPAL_HEADERS = {
    "PAYPAL-TRANSMISSION-ID": "t-1",
    "PAYPAL-TRANSMISSION-TIME": "2026-11-04T12:00:00Z",
    "PAYPAL-TRANSMISSION-SIG": "sig",
    "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/cert",
    "PAYPAL-AUTH-ALGO": "SHA256withRSA",
}


def paypal_event(
    event_id: str = "WH-1A", event_type: str = "PAYMENT.CAPTURE.COMPLETED"
) -> dict[str, Any]:
    return {
        "id": event_id,
        "event_type": event_type,
        "resource": {"id": "CAP-1"},
        "create_time": "2026-11-04T12:00:00Z",
    }


def shippo_event(status: str = "TRANSIT", tracking: str = "SHIPPO_TRANSIT") -> dict[str, Any]:
    return {
        "event": "track_updated",
        "test": True,
        "data": {
            "carrier": "shippo",
            "tracking_number": tracking,
            "tracking_status": {"status": status, "status_date": "2026-11-04T10:00:00Z"},
        },
    }


def inbox_count(session_factory) -> int:
    with session_factory() as s:
        return s.scalar(select(func.count()).select_from(WebhookInbox)) or 0


def inbox_rows(session_factory) -> list[WebhookInbox]:
    with session_factory() as s:
        return list(s.scalars(select(WebhookInbox)))


def make_api(session_factory, settings, clock, verifier) -> TestClient:
    deps = AppDeps(
        settings=settings,
        session_factory=session_factory,
        paypal_verifier=verifier,
        clock=clock,
        rules_dir=RULES_DIR,
    )
    return TestClient(create_app(deps))


@pytest.fixture
def api(session_factory, settings, clock, verifier) -> TestClient:
    return make_api(session_factory, settings, clock, verifier)


def post_paypal(api: TestClient, event: dict[str, Any]):
    return api.post("/webhooks/paypal", content=json.dumps(event), headers=PAYPAL_HEADERS)


class Spy:
    def __init__(self, fail: bool = False) -> None:
        self.rows: list[str] = []
        self.fail = fail

    def __call__(self, session, row: WebhookInbox) -> None:
        self.rows.append(row.event_id)
        if self.fail:
            raise RuntimeError("processor exploded")


def processor(session_factory, clock, settings, handlers) -> InboxProcessor:
    return InboxProcessor(session_factory, handlers, clock, settings)


def real_verifier(httpserver: HTTPServer, clock) -> PayPalWebhookVerifier:
    config = PayPalConfig(
        client_id="cid",
        client_secret="secret",
        webhook_id="WH-ID",
        base_url=httpserver.url_for("").rstrip("/"),
        timeout_s=0.5,
    )
    httpserver.expect_request("/v1/oauth2/token", method="POST").respond_with_json(
        {"access_token": "T", "expires_in": 32400}
    )
    return PayPalWebhookVerifier(PayPalClient(config, clock))


# ---------------------------------------------------------------- WH-01


def test_WH_01_invalid_paypal_signature_returns_400_and_stores_nothing(
    session_factory, settings, clock
):
    from tests.fakes import FakeVerifier

    api = make_api(session_factory, settings, clock, FakeVerifier(result=False))
    assert post_paypal(api, paypal_event()).status_code == 400
    assert inbox_count(session_factory) == 0


def test_WH_01_paypal_verification_status_failure_returns_400(
    session_factory, settings, clock, httpserver
):
    verifier = real_verifier(httpserver, clock)
    httpserver.expect_request(
        "/v1/notifications/verify-webhook-signature", method="POST"
    ).respond_with_json({"verification_status": "FAILURE"})
    api = make_api(session_factory, settings, clock, verifier)
    assert post_paypal(api, paypal_event()).status_code == 400
    assert inbox_count(session_factory) == 0


# ---------------------------------------------------------------- WH-02


def test_WH_02_valid_paypal_webhook_stored_and_returns_200(api, session_factory):
    response = post_paypal(api, paypal_event())
    assert response.status_code == 200
    rows = inbox_rows(session_factory)
    assert len(rows) == 1
    assert rows[0].event_id == "WH-1A"
    assert rows[0].source == "paypal"
    assert rows[0].event_type == "PAYMENT.CAPTURE.COMPLETED"
    assert rows[0].payload["resource"]["id"] == "CAP-1"


def test_WH_02_handler_does_not_process_event(api, session_factory):
    post_paypal(api, paypal_event())
    row = inbox_rows(session_factory)[0]
    assert row.status == "pending"
    assert row.processed_at is None
    assert row.attempts == 0


def test_WH_02_worker_processes_stored_event(api, session_factory, clock, settings):
    post_paypal(api, paypal_event())
    spy = Spy()
    processor(
        session_factory, clock, settings, {("paypal", "PAYMENT.CAPTURE.COMPLETED"): spy}
    ).run_pending()
    assert spy.rows == ["WH-1A"]
    row = inbox_rows(session_factory)[0]
    assert row.status == "processed"
    assert row.processed_at is not None


# ---------------------------------------------------------------- WH-03


def test_WH_03_duplicate_event_id_returns_200_and_stores_once(api, session_factory):
    assert post_paypal(api, paypal_event()).status_code == 200
    assert post_paypal(api, paypal_event()).status_code == 200
    assert inbox_count(session_factory) == 1


def test_WH_03_duplicate_event_processed_once(api, session_factory, clock, settings):
    spy = Spy()
    proc = processor(
        session_factory, clock, settings, {("paypal", "PAYMENT.CAPTURE.COMPLETED"): spy}
    )
    post_paypal(api, paypal_event())
    proc.run_pending()
    post_paypal(api, paypal_event())
    proc.run_pending()
    assert spy.rows == ["WH-1A"]


# ---------------------------------------------------------------- WH-04


def test_WH_04_shippo_wrong_token_returns_404_and_stores_nothing(api, session_factory):
    response = api.post("/webhooks/shippo/wrong-token", json=shippo_event())
    assert response.status_code == 404
    assert inbox_count(session_factory) == 0


def test_WH_04_shippo_token_compared_in_constant_time(api, monkeypatch):
    calls: list[tuple[Any, Any]] = []
    real = webhooks_module.hmac.compare_digest

    def spy(a: Any, b: Any) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(webhooks_module.hmac, "compare_digest", spy)
    api.post("/webhooks/shippo/wrong-token", json=shippo_event())
    assert calls, "token must be compared with hmac.compare_digest"


def test_WH_04_shippo_correct_token_stores_event(api, session_factory):
    response = api.post("/webhooks/shippo/tok-123", json=shippo_event())
    assert response.status_code == 200
    rows = inbox_rows(session_factory)
    assert len(rows) == 1
    assert rows[0].source == "shippo"
    assert rows[0].event_type == "track_updated"
    assert rows[0].event_id == shippo_event_id(shippo_event())


# ---------------------------------------------------------------- WH-05


def test_WH_05_unknown_event_type_stored_and_ignored(api, session_factory, clock, settings):
    assert post_paypal(api, paypal_event("WH-UNK", "BILLING.PLAN.CREATED")).status_code == 200
    processor(session_factory, clock, settings, {}).run_pending()
    row = inbox_rows(session_factory)[0]
    assert row.status == "ignored"
    assert row.last_error is None


# ---------------------------------------------------------------- WH-06


def test_WH_06_verify_endpoint_5xx_returns_503_and_stores_nothing(
    session_factory, settings, clock, httpserver
):
    verifier = real_verifier(httpserver, clock)
    httpserver.expect_request(
        "/v1/notifications/verify-webhook-signature", method="POST"
    ).respond_with_json({"name": "INTERNAL_SERVER_ERROR"}, status=500)
    api = make_api(session_factory, settings, clock, verifier)
    assert post_paypal(api, paypal_event()).status_code == 503
    assert inbox_count(session_factory) == 0


def test_WH_06_verify_endpoint_timeout_returns_503_and_stores_nothing(
    session_factory, settings, clock, httpserver
):
    verifier = real_verifier(httpserver, clock)

    def slow(_: Request) -> Response:
        time.sleep(1.0)
        return Response(
            json.dumps({"verification_status": "SUCCESS"}), content_type="application/json"
        )

    httpserver.expect_request(
        "/v1/notifications/verify-webhook-signature", method="POST"
    ).respond_with_handler(slow)
    api = make_api(session_factory, settings, clock, verifier)
    assert post_paypal(api, paypal_event()).status_code == 503
    assert inbox_count(session_factory) == 0


# ---------------------------------------------------------------- WH-07


def test_WH_07_failing_processor_retried_then_marked_failed_not_lost(
    api, session_factory, clock, settings
):
    post_paypal(api, paypal_event())
    spy = Spy(fail=True)
    proc = processor(
        session_factory, clock, settings, {("paypal", "PAYMENT.CAPTURE.COMPLETED"): spy}
    )
    for attempt in range(1, settings.max_attempts + 1):
        proc.run_pending()
        row = inbox_rows(session_factory)[0]
        assert row.attempts == attempt
        expected = "failed" if attempt == settings.max_attempts else "pending"
        assert row.status == expected
    proc.run_pending()  # a failed row is not retried again
    assert len(spy.rows) == settings.max_attempts
    row = inbox_rows(session_factory)[0]
    assert "processor exploded" in (row.last_error or "")


@pytest.mark.parametrize("path", ["/webhooks/paypal", "/webhooks/shippo/tok-123"])
def test_WH_07_malformed_json_returns_400(api, session_factory, path):
    response = api.post(path, content=b"{not json", headers=PAYPAL_HEADERS)
    assert response.status_code == 400
    assert inbox_count(session_factory) == 0


@pytest.mark.parametrize("path", ["/webhooks/paypal", "/webhooks/shippo/tok-123"])
def test_WH_07_oversized_body_returns_400(api, session_factory, settings, path):
    body = json.dumps(
        {"id": "WH-BIG", "event_type": "X", "pad": "x" * (settings.max_webhook_body_bytes + 1)}
    )
    response = api.post(path, content=body, headers=PAYPAL_HEADERS)
    assert response.status_code == 400
    assert inbox_count(session_factory) == 0


# ---------------------------------------------------------------- WH-08


def test_WH_08_shippo_event_id_derived_from_tracking_status_and_date():
    expected = hashlib.sha256(b"SHIPPO_TRANSIT|TRANSIT|2026-11-04T10:00:00Z").hexdigest()
    assert shippo_event_id(shippo_event()) == expected


def test_WH_08_same_payload_same_id_different_status_different_id():
    assert shippo_event_id(shippo_event()) == shippo_event_id(shippo_event())
    assert shippo_event_id(shippo_event("TRANSIT")) != shippo_event_id(shippo_event("DELIVERED"))
