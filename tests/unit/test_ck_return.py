"""CK-05/CK-01: PayPal sends the buyer back to Doorstep after approval, before capture."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.api.deps import AppDeps, Services
from app.main import create_app
from app.money import Money
from app.orders.enums import ShippingMethod
from app.paypal_client.client import PayPalClient, PayPalConfig
from app.paypal_client.types import ShippingOption
from app.paypal_client.writes.orders import create_order
from app.proof.receipts import ReceiptSigner
from app.security.links import LinkSigner
from tests.fakes import FakeVerifier
from tests.fakes_journey import SAM_NO_FLAT, RecordingPayPal

RULES = Path(__file__).resolve().parents[2] / "rules"


def _paypal(httpserver, clock) -> PayPalClient:
    httpserver.expect_request("/v1/oauth2/token").respond_with_json(
        {"access_token": "T", "expires_in": 32400, "token_type": "Bearer"}
    )
    return PayPalClient(
        PayPalConfig(client_id="id", client_secret="s", base_url=httpserver.url_for("")), clock
    )


def _options() -> list[ShippingOption]:
    from app.checkout.service import shipping_options

    return shipping_options(Money(amount_minor=6000, currency="GBP"))


def test_CK_05_return_url_sends_the_buyer_back_before_capture(httpserver, clock):
    client = _paypal(httpserver, clock)
    httpserver.expect_request("/v2/checkout/orders", method="POST").respond_with_json(
        {
            "id": "PPO-9",
            "status": "PAYER_ACTION_REQUIRED",
            "links": [{"rel": "payer-action", "href": "https://www.sandbox.paypal.com/x"}],
        }
    )
    create_order(
        client,
        amount=Money(amount_minor=6000, currency="GBP"),
        options=_options(),
        selected=ShippingMethod.LOCKER,
        request_id="r1",
        return_url="https://doorstep.test/return",
    )
    body = next(r for r, _ in httpserver.log if r.path == "/v2/checkout/orders").get_json()
    ctx = body["payment_source"]["paypal"]["experience_context"]
    assert ctx["return_url"] == "https://doorstep.test/return"
    assert ctx["user_action"] == "CONTINUE"  # capture waits for the address check


def test_CK_05_no_return_url_keeps_the_original_body(httpserver, clock):
    client = _paypal(httpserver, clock)
    httpserver.expect_request("/v2/checkout/orders", method="POST").respond_with_json(
        {"id": "PPO-9", "links": [{"rel": "approve", "href": "https://www.sandbox.paypal.com/x"}]}
    )
    create_order(
        client,
        amount=Money(amount_minor=6000, currency="GBP"),
        options=_options(),
        selected=ShippingMethod.HOME,
        request_id="r2",
    )
    body = next(r for r, _ in httpserver.log if r.path == "/v2/checkout/orders").get_json()
    assert "payment_source" not in body


def test_CK_01_return_exchanges_the_paypal_order_for_a_buyer_link(session_factory, settings, clock):
    paypal = RecordingPayPal()
    services = Services(
        links=LinkSigner("s" * 32, clock),
        receipts=ReceiptSigner.generate(),
        orders=paypal,
        approved=paypal,
    )
    api = TestClient(
        create_app(
            AppDeps(
                settings=settings,
                session_factory=session_factory,
                paypal_verifier=FakeVerifier(),
                clock=clock,
                rules_dir=RULES,
                services=services,
            )
        )
    )
    seller = api.post("/demo/seed?scratch=1").json()["summary"]["seller_id"]
    started = api.post(
        "/api/checkout/start",
        json={
            "seller_id": seller,
            "amount_minor": 6000,
            "currency": "GBP",
            "buyer_country": "GB",
            "method": "locker",
        },
    ).json()
    paypal.approve(started["paypal_order_id"], SAM_NO_FLAT)
    back = api.post("/api/checkout/return", json={"paypal_order_id": started["paypal_order_id"]})
    body = back.json()
    assert body["address"]["valid"] is False  # prompt for the flat number
    view = api.get(f"/api/buyer/{body['buyer_token']}").json()
    assert view["address_check"] == "invalid"
    assert api.post("/api/checkout/return", json={"paypal_order_id": "nope"}).status_code == 404
