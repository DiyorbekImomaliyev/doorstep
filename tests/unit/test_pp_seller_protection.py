"""PP-11: PayPal's Seller Protection verdict on the capture: stored, shown, never a money input.

The critique (Nov 5) found Doorstep quoted Seller Protection rules but never read PayPal's actual
status for the payment. The capture response (and the PAYMENT.CAPTURE.COMPLETED webhook) carries
``seller_protection.status``; Doorstep appends it to the order's audit trail so the seller sees it,
and keeps it out of the evidence table so no decision input changes.
"""

from __future__ import annotations

from typing import Any

from pytest_httpserver import HTTPServer
from sqlalchemy import select

from app.api.views import order_row, timeline
from app.cases.engine import ClaimReason
from app.cases.signals import assemble_signals
from app.config import Settings
from app.orders.enums import OrderState
from app.orders.models import AuditLog, Evidence, Order
from app.paypal_client.client import PayPalClient, PayPalConfig
from app.paypal_client.types import CaptureResult
from app.paypal_client.writes import orders as order_writes
from app.policy.fingerprint import evidence_fingerprint
from app.proof.seller_protection import ACTION
from app.worker.handlers import CAPTURE_ORDER, CaptureOrderHandler
from app.worker.models import WebhookInbox
from app.worker.paypal_events import paypal_inbox_handlers
from app.worker.runner import JobRunner, enqueue
from tests.factories import make_order, make_seller
from tests.helpers import money


def _protection_rows(db, order_id: str) -> list[AuditLog]:
    db.expire_all()
    return list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.entity_id == order_id, AuditLog.action == ACTION)
            .order_by(AuditLog.at)
        )
    )


# ---------------------------------------------------------------- the client reads the field


def _capture_body(protection: dict[str, Any] | None) -> dict[str, Any]:
    capture: dict[str, Any] = {
        "id": "CAP-1",
        "status": "COMPLETED",
        "amount": {"currency_code": "GBP", "value": "60.00"},
    }
    if protection is not None:
        capture["seller_protection"] = protection
    return {
        "id": "O-1",
        "status": "COMPLETED",
        "purchase_units": [{"payments": {"captures": [capture]}}],
    }


def _client(httpserver: HTTPServer, clock) -> PayPalClient:
    httpserver.expect_request("/v1/oauth2/token", method="POST").respond_with_json(
        {"access_token": "T", "token_type": "Bearer", "expires_in": 32400}
    )
    config = PayPalConfig(
        client_id="cid", client_secret="secret", base_url=httpserver.url_for("").rstrip("/")
    )
    return PayPalClient(config, clock)


def test_PP_11_capture_returns_paypals_seller_protection_verdict(httpserver, clock):
    pp = _client(httpserver, clock)
    httpserver.expect_request("/v2/checkout/orders/O-1/capture", method="POST").respond_with_json(
        _capture_body(
            {
                "status": "ELIGIBLE",
                "dispute_categories": ["ITEM_NOT_RECEIVED", "UNAUTHORIZED_TRANSACTION"],
            }
        ),
        status=201,
    )
    result = order_writes.capture_order(pp, "O-1", request_id="order:o1:capture")
    assert result.seller_protection == "ELIGIBLE"
    assert result.protected_categories == ["ITEM_NOT_RECEIVED", "UNAUTHORIZED_TRANSACTION"]


def test_PP_11_a_capture_without_the_field_reports_nothing(httpserver, clock):
    pp = _client(httpserver, clock)
    httpserver.expect_request("/v2/checkout/orders/O-1/capture", method="POST").respond_with_json(
        _capture_body(None), status=201
    )
    result = order_writes.capture_order(pp, "O-1", request_id="order:o1:capture")
    assert result.seller_protection is None and result.protected_categories == []


# ---------------------------------------------------------------- stored once per order


def _created(db) -> Order:
    return make_order(
        db,
        make_seller(db),
        state=OrderState.CREATED,
        capture_id=None,
        capture_status=None,
        paypal_order_id="PPO-SP",
    )


def test_PP_11_the_capture_job_stores_the_verdict(
    db, session_factory, clock, settings, fake_paypal
):
    order = _created(db)
    fake_paypal.capture_results.append(
        CaptureResult(
            status="COMPLETED",
            capture_id="CAP-SP",
            amount=money(6000),
            seller_protection="PARTIALLY_ELIGIBLE",
            protected_categories=["ITEM_NOT_RECEIVED"],
        )
    )
    enqueue(db, CAPTURE_ORDER, {"order_id": order.id}, clock=clock)
    db.commit()
    JobRunner(
        session_factory, {CAPTURE_ORDER: CaptureOrderHandler(fake_paypal, clock)}, clock, settings
    ).run_due()
    [row] = _protection_rows(db, order.id)
    assert row.actor == "paypal" and row.entity_type == "order"
    assert row.after == {
        "status": "PARTIALLY_ELIGIBLE",
        "dispute_categories": ["ITEM_NOT_RECEIVED"],
    }
    assert db.scalars(select(Evidence).where(Evidence.order_id == order.id)).all() == []


def test_PP_11_a_capture_without_a_verdict_stores_no_row(
    db, session_factory, clock, settings, fake_paypal
):
    order = _created(db)
    fake_paypal.capture_results.append(CaptureResult(status="COMPLETED", capture_id="CAP-N"))
    enqueue(db, CAPTURE_ORDER, {"order_id": order.id}, clock=clock)
    db.commit()
    JobRunner(
        session_factory, {CAPTURE_ORDER: CaptureOrderHandler(fake_paypal, clock)}, clock, settings
    ).run_due()
    assert _protection_rows(db, order.id) == []


def _webhook(db, rulebook, clock, order: Order, status: str, event: str) -> None:
    resource = {
        "id": "CAP-W",
        "status": "COMPLETED",
        "seller_protection": {"status": status, "dispute_categories": ["ITEM_NOT_RECEIVED"]},
        "supplementary_data": {"related_ids": {"order_id": order.paypal_order_id}},
    }
    row = WebhookInbox(
        event_id=event,
        source="paypal",
        event_type="PAYMENT.CAPTURE.COMPLETED",
        payload={"id": event, "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": resource},
        status="pending",
        attempts=0,
    )
    paypal_inbox_handlers(rulebook, clock)[("paypal", "PAYMENT.CAPTURE.COMPLETED")](db, row)
    db.flush()


def test_PP_11_the_webhook_records_it_a_repeat_writes_nothing_a_change_is_appended(
    db, rulebook, clock
):
    order = _created(db)
    _webhook(db, rulebook, clock, order, "ELIGIBLE", "WH-SP-1")
    _webhook(db, rulebook, clock, order, "ELIGIBLE", "WH-SP-1b")  # same verdict again
    [first] = _protection_rows(db, order.id)
    assert first.after["status"] == "ELIGIBLE"
    clock.advance(60)
    _webhook(db, rulebook, clock, order, "NOT_ELIGIBLE", "WH-SP-2")
    rows = _protection_rows(db, order.id)
    assert [r.after["status"] for r in rows] == ["ELIGIBLE", "NOT_ELIGIBLE"]
    assert rows[1].before["status"] == "ELIGIBLE"
    assert order_row(db, db.get(Order, order.id)).seller_protection == "NOT_ELIGIBLE"


# ---------------------------------------------------------------- shown, never a money input


def test_PP_11_the_control_room_row_shows_the_verdict(db, rulebook, clock):
    order = _created(db)
    assert order_row(db, order).seller_protection is None
    _webhook(db, rulebook, clock, order, "ELIGIBLE", "WH-SP-3")
    assert order_row(db, order).seller_protection == "ELIGIBLE"


def test_PP_11_the_verdict_never_changes_signals_or_the_pre_refund_fingerprint(db, rulebook, clock):
    order = make_order(db, make_seller(db), state=OrderState.DELIVERED, paypal_order_id="PPO-SIG")
    signals = assemble_signals(db, order, ClaimReason.NOT_RECEIVED, clock.now(), Settings())
    fingerprint = evidence_fingerprint(db, order.id)
    _webhook(db, rulebook, clock, order, "ELIGIBLE", "WH-SP-4")
    assert assemble_signals(db, order, ClaimReason.NOT_RECEIVED, clock.now(), Settings()) == signals
    assert evidence_fingerprint(db, order.id) == fingerprint


def test_PP_11_the_buyer_timeline_never_shows_it(db, rulebook, clock):
    order = _created(db)
    _webhook(db, rulebook, clock, order, "ELIGIBLE", "WH-SP-5")
    seller = [i.title for i in timeline(db, order, for_buyer=False)]
    buyer = [i.title for i in timeline(db, order, for_buyer=True)]
    assert "PayPal Seller Protection" in seller and "PayPal Seller Protection" not in buyer
