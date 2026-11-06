"""SB-01 / SB-02: the real sandbox refund made through the live app on Nov 6, 2026.

The ids are recorded in tests/fixtures/sandbox/live_refund_2026-11-06.json. These tests re-check
them against PayPal (read-only GETs), so the evidence can be verified at any time; the buyer's and
seller's sandbox Activity pages and the dashboard's Webhook Events were checked by hand (listed
in the fixture). Opt-in: RUN_SANDBOX_CHECKS=1 with the sandbox keys. PayPal keeps sandbox webhook
event history for a limited time, so SB-02 is expected to hold for weeks, not forever.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

EVIDENCE: dict[str, Any] = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "sandbox" / "live_refund_2026-11-06.json").read_text()
)
BASE = os.environ.get("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("RUN_SANDBOX_CHECKS") != "1", reason="set RUN_SANDBOX_CHECKS=1"
    ),
    pytest.mark.skipif(
        not (os.environ.get("PAYPAL_CLIENT_ID") and os.environ.get("PAYPAL_CLIENT_SECRET")),
        reason="needs the sandbox keys",
    ),
    pytest.mark.skipif("sandbox" not in BASE, reason="sandbox only"),
]


@pytest.fixture(scope="module")
def paypal() -> httpx.Client:
    token = httpx.post(
        f"{BASE}/v1/oauth2/token",
        auth=(os.environ["PAYPAL_CLIENT_ID"], os.environ["PAYPAL_CLIENT_SECRET"]),
        data={"grant_type": "client_credentials"},
        timeout=20,
    ).json()["access_token"]
    return httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {token}"}, timeout=20)


def test_SB_01_01_the_live_refund_is_completed_on_the_merchant_capture(paypal: httpx.Client):
    refund = paypal.get(f"/v2/payments/refunds/{EVIDENCE['refund_id']}").json()
    assert refund["status"] == "COMPLETED"
    assert refund["amount"] == EVIDENCE["amount"]
    capture = paypal.get(f"/v2/payments/captures/{EVIDENCE['capture_id']}").json()
    assert capture["status"] == "REFUNDED"
    assert capture["amount"] == EVIDENCE["amount"]
    up = next(link["href"] for link in refund["links"] if link["rel"] == "up")
    assert up.endswith(f"/captures/{EVIDENCE['capture_id']}")


def test_SB_01_02_the_order_was_a_real_buyer_checkout_with_tracking(paypal: httpx.Client):
    order = paypal.get(f"/v2/checkout/orders/{EVIDENCE['paypal_order_id']}").json()
    assert order["status"] == "COMPLETED"
    assert order["payer"]["address"]["country_code"] == EVIDENCE["buyer_country"]
    unit = order["purchase_units"][0]
    assert unit["payee"]["email_address"] == EVIDENCE["seller_email"]
    assert [c["id"] for c in unit["payments"]["captures"]] == [EVIDENCE["capture_id"]]
    trackers = paypal.get(
        "/v1/shipping/trackers", params={"transaction_id": EVIDENCE["capture_id"]}
    ).json()
    assert EVIDENCE["tracking_number"] in {t["tracking_number"] for t in trackers["trackers"]}


def test_SB_02_01_paypal_sent_every_webhook_for_the_live_order(paypal: httpx.Client):
    hooks = paypal.get("/v1/notifications/webhooks").json()["webhooks"]
    assert EVIDENCE["webhook_url"] in {h["url"] for h in hooks}
    start, end = EVIDENCE["events_window_utc"]
    events = paypal.get(
        "/v1/notifications/webhooks-events",
        params={"start_time": start, "end_time": end, "page_size": 50},
    ).json()["events"]
    sent = {(e["event_type"], e["resource"]["id"]) for e in events}
    assert ("CHECKOUT.ORDER.APPROVED", EVIDENCE["paypal_order_id"]) in sent
    assert ("PAYMENT.CAPTURE.COMPLETED", EVIDENCE["capture_id"]) in sent
    assert ("PAYMENT.CAPTURE.REFUNDED", EVIDENCE["refund_id"]) in sent
