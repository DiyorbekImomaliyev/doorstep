"""PP-03 — a declined capture, replayed from a real sandbox recording (phase D records it)."""

from __future__ import annotations

import json

import pytest
from pytest_httpserver import HTTPServer

from app.paypal_client.client import PayPalClient, PayPalConfig
from app.paypal_client.writes import orders as order_writes
from tests.conftest import ROOT
from tests.fakes import FakeClock

RECORDING = ROOT / "tests" / "fixtures" / "paypal" / "capture_declined.json"

pytestmark = pytest.mark.integration


def test_PP_03_recorded_sandbox_declined_capture(httpserver: HTTPServer):
    assert RECORDING.exists(), "record a declined sandbox capture in the live smoke (phase D)"
    recorded = json.loads(RECORDING.read_text())  # {"status": int, "body": {...}}
    httpserver.expect_request("/v1/oauth2/token", method="POST").respond_with_json(
        {"access_token": "T", "expires_in": 32400}
    )
    httpserver.expect_request("/v2/checkout/orders/REC-1/capture", method="POST").respond_with_json(
        recorded["body"], status=recorded["status"]
    )
    config = PayPalConfig(
        client_id="cid", client_secret="secret", base_url=httpserver.url_for("").rstrip("/")
    )
    result = order_writes.capture_order(
        PayPalClient(config, FakeClock()), "REC-1", request_id="order:rec:capture"
    )
    assert result.status == "DECLINED"
