"""Health shows webhook arrivals (counts and times only) so a deploy can be checked live."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.deps import AppDeps, Services
from app.main import create_app
from app.proof.receipts import ReceiptSigner
from app.runtime import ROOT
from app.security.links import LinkSigner
from app.worker.models import WebhookInbox


def test_healthz_reports_webhook_arrivals_without_payloads(session_factory, settings, clock):
    services = Services(
        links=LinkSigner("a" * 32, clock),
        receipts=ReceiptSigner.generate(),
        orders=None,
        approved=None,
    )
    deps = AppDeps(
        settings=settings,
        session_factory=session_factory,
        paypal_verifier=None,
        clock=clock,
        rules_dir=ROOT / "rules",
        services=services,
    )
    api = TestClient(create_app(deps))
    empty = api.get("/healthz").json()["webhooks"]
    assert empty["paypal"] == {
        "received": 0,
        "last_received_at": None,
        "last_event_type": None,
        "last_status": None,
    }
    with session_factory() as s:
        s.add(
            WebhookInbox(
                event_id="WH-1",
                source="paypal",
                event_type="PAYMENT.CAPTURE.COMPLETED",
                payload={"resource": {"payer": {"email_address": "sam@example.com"}}},
                status="processed",
                attempts=1,
            )
        )
        s.commit()
    body = api.get("/healthz").json()
    paypal = body["webhooks"]["paypal"]
    assert paypal["received"] == 1 and paypal["last_status"] == "processed"
    assert paypal["last_event_type"] == "PAYMENT.CAPTURE.COMPLETED"
    assert "sam@example.com" not in str(body) and "WH-1" not in str(body)
    assert body["webhooks"]["shippo"]["received"] == 0
