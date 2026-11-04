"""PO-07 refund identity: every refund carries our money_action id in PayPal's custom_id (ADR-022).

Crash recovery reads the refunds on the capture first. A refund with our custom_id and the same
amount is ours: it is recorded as COMPLETED and the case settles, with no resend. A refund we
cannot identify still blocks and goes to HOLD_FOR_REVIEW (ADR-016).
"""

from __future__ import annotations

import json

import pytest
from pytest_httpserver import HTTPServer
from sqlalchemy import select

from app.orders.enums import CaseState, MoneyActionStatus
from app.orders.models import AuditLog, Case, MoneyAction
from app.paypal_client.client import PayPalClient, PayPalConfig
from app.paypal_client.errors import PayPalServerError
from app.policy.service import REFUND_JOB, PolicyService, RefundJobHandler
from tests.factories import make_case, make_order, make_seller, money_actions
from tests.fakes import FakeClock
from tests.helpers import engine_result, facts_for, money, run_jobs


@pytest.fixture
def queued(db, fake_paypal, rulebook, clock):
    """An auto-refund decision whose refund job is queued; returns (order, case, money action)."""

    def build(amount_minor: int = 4000, *, claim_minor: int | None = None):
        order = make_order(db, make_seller(db), amount_minor=amount_minor)
        fake_paypal.add_capture(order.paypal_order_id, order.capture_id, money(amount_minor))
        case = make_case(db, order)
        PolicyService(db, rulebook, clock).decide(
            case, engine_result(), facts_for(order, claim_minor)
        )
        db.commit()
        [ma] = money_actions(db, case_id=case.id)
        return order, case, ma

    return build


@pytest.fixture
def run_refunds(session_factory, fake_paypal, rulebook, clock, settings, db):
    def run(rounds: int = 1, advance_s: float = 0) -> None:
        db.commit()
        run_jobs(
            session_factory,
            {REFUND_JOB: RefundJobHandler(fake_paypal, rulebook, clock)},
            clock,
            settings,
            rounds=rounds,
            advance_s=advance_s,
        )
        db.expire_all()

    return run


def _client(httpserver: HTTPServer) -> PayPalClient:
    httpserver.expect_request("/v1/oauth2/token", method="POST").respond_with_json(
        {"access_token": "T", "token_type": "Bearer", "expires_in": 32400}
    )
    return PayPalClient(
        PayPalConfig(
            client_id="c",
            client_secret="s",
            base_url=httpserver.url_for("").rstrip("/"),
            timeout_s=2,
        ),
        FakeClock(),
    )


# ---------------------------------------------------------------- sending


@pytest.mark.xfail(strict=True, reason="ADR-022: custom_id not sent yet")
def test_PO_07_refund_carries_money_action_id_as_custom_id(db, queued, run_refunds, fake_paypal):
    _, _, ma = queued()
    run_refunds()
    assert fake_paypal.refund_custom_ids == [ma.id]
    assert [rid for _, _, rid in fake_paypal.refund_calls] == [ma.request_id]


@pytest.mark.xfail(strict=True, reason="ADR-022: custom_id not sent yet")
def test_PO_07_retry_reuses_request_id_and_custom_id(
    db, queued, run_refunds, fake_paypal, settings
):
    _, _, ma = queued()
    fake_paypal.fail_next_refunds(PayPalServerError(503, "SERVICE_UNAVAILABLE"))
    run_refunds(rounds=2, advance_s=settings.backoff_base_s)
    assert [rid for _, _, rid in fake_paypal.refund_calls] == [ma.request_id, ma.request_id]
    assert fake_paypal.refund_custom_ids == [ma.id, ma.id]
    assert db.get(MoneyAction, ma.id).status == MoneyActionStatus.COMPLETED


@pytest.mark.xfail(strict=True, reason="ADR-022: custom_id not sent yet")
def test_PP_07_refund_request_body_carries_custom_id(httpserver: HTTPServer):
    from app.paypal_client.writes import money as money_writes

    client = _client(httpserver)
    httpserver.expect_request(
        "/v2/payments/captures/CAP-1/refund", method="POST"
    ).respond_with_json({"id": "R-1", "status": "COMPLETED"}, status=201)
    money_writes.refund_capture(  # type: ignore[call-arg]
        client, "CAP-1", amount=money(1500), request_id="k-1", custom_id="ma-1"
    )
    [sent] = [r for r, _ in httpserver.log if r.path.endswith("/refund")]
    assert json.loads(sent.get_data(as_text=True))["custom_id"] == "ma-1"
    assert sent.headers["PayPal-Request-Id"] == "k-1"


@pytest.mark.xfail(strict=True, reason="ADR-022: refunds not read with custom_id yet")
def test_PP_08_capture_state_lists_refunds_with_custom_id(httpserver: HTTPServer):
    from app.paypal_client import reads

    client = _client(httpserver)
    order = {
        "id": "O-1",
        "status": "COMPLETED",
        "purchase_units": [
            {
                "payments": {
                    "captures": [
                        {
                            "id": "CAP-1",
                            "status": "PARTIALLY_REFUNDED",
                            "amount": {"currency_code": "GBP", "value": "40.00"},
                        }
                    ],
                    "refunds": [
                        {
                            "id": "R-7",
                            "status": "COMPLETED",
                            "amount": {"currency_code": "GBP", "value": "10.00"},
                            "custom_id": "ma-7",
                        }
                    ],
                }
            }
        ],
    }
    httpserver.expect_request("/v2/checkout/orders/O-1", method="GET").respond_with_json(order)
    state = reads.get_capture_state(client, "O-1", "CAP-1")
    [refund] = state.refunds  # type: ignore[attr-defined]
    assert (refund.refund_id, refund.custom_id) == ("R-7", "ma-7")
    assert refund.amount == money(1000)


# ---------------------------------------------------------------- crash recovery


@pytest.mark.xfail(strict=True, reason="ADR-022: crash recovery not built yet")
def test_PO_07_crash_recovery_records_our_landed_full_refund_without_resend(
    db, queued, run_refunds, fake_paypal
):
    order, case, ma = queued()
    landed = fake_paypal.refund_capture(  # PayPal applied it; our commit was lost
        order.capture_id, amount=money(4000), request_id=ma.request_id, custom_id=ma.id
    )
    calls_before = len(fake_paypal.refund_calls)
    run_refunds()
    stored = db.get(MoneyAction, ma.id)
    assert stored.status == MoneyActionStatus.COMPLETED
    assert stored.paypal_refund_id == landed.refund_id
    assert len(fake_paypal.refund_calls) == calls_before  # no resend
    assert fake_paypal.total_refunded(order.capture_id) == 4000
    assert db.get(Case, case.id).state == CaseState.SETTLED
    audit = db.scalars(
        select(AuditLog).where(AuditLog.entity_id == ma.id, AuditLog.action == "refund.completed")
    ).one()
    assert audit.after["paypal_response"]["recovered"] is True


@pytest.mark.xfail(strict=True, reason="ADR-022: crash recovery not built yet")
def test_PO_07_landed_partial_refund_with_our_custom_id_is_recovered(
    db, queued, run_refunds, fake_paypal
):
    order, _case, ma = queued(4000, claim_minor=1000)
    fake_paypal.refund_capture(
        order.capture_id, amount=money(1000), request_id=ma.request_id, custom_id=ma.id
    )
    calls_before = len(fake_paypal.refund_calls)
    run_refunds()
    assert db.get(MoneyAction, ma.id).status == MoneyActionStatus.COMPLETED
    assert len(fake_paypal.refund_calls) == calls_before
    assert fake_paypal.total_refunded(order.capture_id) == 1000


def test_PO_07_landed_refund_with_our_custom_id_but_other_amount_blocks(
    db, queued, run_refunds, fake_paypal
):
    order, case, ma = queued()
    fake_paypal.refund_capture(
        order.capture_id, amount=money(2500), request_id="someone-else", custom_id=ma.id
    )
    run_refunds()
    assert db.get(MoneyAction, ma.id).status == MoneyActionStatus.BLOCKED
    assert db.get(Case, case.id).state == CaseState.HOLD_FOR_REVIEW


def test_PO_07_landed_refund_without_custom_id_blocks_and_holds(
    db, queued, run_refunds, fake_paypal
):
    order, case, ma = queued()
    fake_paypal.refund_capture(order.capture_id, amount=money(4000), request_id=ma.request_id)
    run_refunds()
    assert db.get(MoneyAction, ma.id).status == MoneyActionStatus.BLOCKED
    assert db.get(Case, case.id).state == CaseState.HOLD_FOR_REVIEW


@pytest.mark.xfail(strict=True, reason="ADR-022: unidentified partial refunds must block")
def test_PO_07_landed_partial_refund_without_custom_id_blocks_and_holds(
    db, queued, run_refunds, fake_paypal
):
    order, case, ma = queued(4000, claim_minor=1000)
    fake_paypal.refund_capture(order.capture_id, amount=money(1000), request_id=ma.request_id)
    run_refunds()
    assert db.get(MoneyAction, ma.id).status == MoneyActionStatus.BLOCKED
    assert db.get(Case, case.id).state == CaseState.HOLD_FOR_REVIEW


def test_PO_07_landed_refund_with_someone_elses_custom_id_blocks(
    db, queued, run_refunds, fake_paypal
):
    order, case, ma = queued()
    fake_paypal.refund_capture(
        order.capture_id, amount=money(4000), request_id="other", custom_id="ma-not-ours"
    )
    run_refunds()
    assert db.get(MoneyAction, ma.id).status == MoneyActionStatus.BLOCKED
    assert db.get(Case, case.id).state == CaseState.HOLD_FOR_REVIEW
