"""PO-05, review R1 and R3: a refund never goes out while PayPal holds an open dispute.

The local disputes table is filled by webhooks, and webhooks can be late or arrive out of order.
Right before money moves, the re-check asks PayPal itself (R1). Webhook events can never move a
resolved dispute back to open (R3); only a live read from PayPal can.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.orders.enums import CaseState, MoneyActionStatus
from app.orders.models import AuditLog, Case, Dispute, MoneyAction, Order
from app.paypal_client import reads
from app.paypal_client.errors import PayPalClientError, PayPalServerError
from app.paypal_client.types import DisputeSummary
from app.policy.gateway import RestRefundGateway
from app.policy.ports import DisputeLookup
from app.policy.reasons import Reason
from app.policy.service import (
    DISPUTE_LOOKBACK_MARGIN,
    REFUND_JOB,
    PolicyService,
    RefundJobHandler,
    _record_dispute_found,
)
from app.worker.models import Job, WebhookInbox
from app.worker.paypal_events import paypal_inbox_handlers
from app.worker.simulated import RoutingRefundGateway, SimulatedRefundGateway
from tests.factories import make_case, make_order, make_seller
from tests.fakes import FakePayPal, FakeRefund
from tests.helpers import engine_result, facts_for, money, run_jobs


class DisputingPayPal(FakePayPal):
    """FakePayPal plus live dispute reads (app.policy.ports.DisputeLookup)."""

    def __init__(self) -> None:
        super().__init__()
        self.open_disputes: list[DisputeSummary] = []
        self.dispute_queries: list[tuple[datetime, str]] = []
        self.dispute_failures: list[Exception] = []

    def open_disputes_since(self, since: datetime, *, capture_id: str) -> list[DisputeSummary]:
        self.dispute_queries.append((since, capture_id))
        if self.dispute_failures:
            raise self.dispute_failures.pop(0)
        return list(self.open_disputes)


@pytest.fixture
def paypal() -> DisputingPayPal:
    return DisputingPayPal()


@pytest.fixture
def refund_case(db, rulebook, clock, paypal):
    """A GB order captured at PayPal, decided for an automatic refund: one refund job queued."""

    def build(*, age: timedelta = timedelta(days=3)) -> tuple[Order, Case]:
        order = make_order(db, make_seller(db), amount_minor=3000)
        order.created_at = clock.now() - age
        paypal.add_capture(order.paypal_order_id, order.capture_id, money(3000))
        case = make_case(db, order)
        db.commit()
        decision = PolicyService(db, rulebook, clock).decide(
            case, engine_result(), facts_for(order)
        )
        assert decision.route == CaseState.AUTO_REFUND
        db.commit()
        return order, case

    return build


@pytest.fixture
def run_refund(session_factory, paypal, rulebook, clock, settings, db):
    def run() -> None:
        run_jobs(
            session_factory,
            {REFUND_JOB: RefundJobHandler(paypal, rulebook, clock, disputes=paypal)},
            clock,
            settings,
        )
        db.expire_all()

    return run


def _state(db, case: Case) -> tuple[str, str]:
    [ma] = db.scalars(select(MoneyAction).where(MoneyAction.case_id == case.id)).all()
    reloaded = db.get(Case, case.id)
    assert reloaded is not None
    return ma.status, reloaded.state


def _summary(dispute_id: str, *captures: str, status: str = "OPEN") -> DisputeSummary:
    return DisputeSummary(dispute_id=dispute_id, status=status, capture_ids=list(captures))


# ---------------------------------------------------------------- R1: ask PayPal itself


def test_PO_05_a_dispute_only_paypal_knows_blocks_the_refund(db, refund_case, run_refund, paypal):
    order, case = refund_case()
    paypal.open_disputes = [_summary("PP-D-LATE", str(order.capture_id))]  # webhook not in yet
    run_refund()
    assert paypal.refund_calls == []
    assert _state(db, case) == (MoneyActionStatus.BLOCKED, CaseState.DISPUTE_EVIDENCE)
    [ma] = db.scalars(select(MoneyAction).where(MoneyAction.case_id == case.id)).all()
    assert ma.paypal_response["blocked_code"] == Reason.DISPUTE_OPENED.value


def test_PO_05_the_dispute_found_at_paypal_is_recorded_for_evidence(
    db, refund_case, run_refund, paypal, clock
):
    order, _ = refund_case()
    paypal.open_disputes = [_summary("PP-D-LATE", str(order.capture_id), status="UNDER_REVIEW")]
    run_refund()
    [d] = db.scalars(select(Dispute)).all()
    assert (d.order_id, d.paypal_dispute_id, d.status) == (order.id, "PP-D-LATE", "open")
    [row] = db.scalars(select(AuditLog).where(AuditLog.action == "dispute.found_at_paypal")).all()
    assert (row.actor, row.entity_type, row.entity_id) == ("policy", "dispute", "PP-D-LATE")
    assert row.before == {}
    assert row.after == {"status": "open", "paypal_status": "UNDER_REVIEW", "order_id": order.id}
    assert row.at == clock.now()


def test_PO_05_paypal_is_asked_from_a_day_before_the_order(db, refund_case, run_refund, paypal):
    order, _ = refund_case()
    run_refund()
    assert DISPUTE_LOOKBACK_MARGIN == timedelta(days=1)
    assert paypal.dispute_queries == [(order.created_at - timedelta(days=1), order.capture_id)]
    assert len(paypal.refund_calls) == 1  # no dispute: the refund goes out


def test_PO_05_a_dispute_on_another_capture_does_not_block(db, refund_case, run_refund, paypal):
    _, case = refund_case()
    paypal.open_disputes = [_summary("PP-D-OTHER", "CAP-SOMEONE-ELSE")]
    run_refund()
    assert len(paypal.refund_calls) == 1
    assert _state(db, case)[0] == MoneyActionStatus.COMPLETED
    assert db.scalars(select(Dispute)).all() == []


def test_PO_05_a_dispute_paypal_cannot_tie_to_a_capture_goes_to_a_person(
    db, refund_case, run_refund, paypal
):
    _, case = refund_case()
    paypal.open_disputes = [_summary("PP-D-UNKNOWN")]  # no disputed transaction listed
    run_refund()
    assert paypal.refund_calls == []
    assert _state(db, case) == (MoneyActionStatus.BLOCKED, CaseState.HOLD_FOR_REVIEW)
    assert db.scalars(select(Dispute)).all() == []  # never pinned on an order by guesswork


def test_PO_05_a_dispute_hold_is_visible_on_the_pipeline_signals(
    db, clock, refund_case, run_refund, paypal
):
    """Review F1: an untied dispute holds refunds by design, so someone must be told (R7)."""
    from app.ops.signals import pipeline_signals

    refund_case()
    paypal.open_disputes = [_summary("PP-D-UNKNOWN")]
    run_refund()
    db.expire_all()
    s = pipeline_signals(db, clock.now())
    assert s.money_blocked_24h == {"unmatched_dispute": 1}
    assert s.money_waiting_for_a_person == 1
    assert (s.status, s.reasons) == ("degraded", ["money_waiting_for_a_person"])


def test_PO_05_our_dispute_wins_over_an_unknown_one(db, refund_case, run_refund, paypal):
    order, case = refund_case()
    paypal.open_disputes = [_summary("PP-D-UNKNOWN"), _summary("PP-D-1", str(order.capture_id))]
    run_refund()
    assert _state(db, case) == (MoneyActionStatus.BLOCKED, CaseState.DISPUTE_EVIDENCE)


def test_PO_05_an_order_too_old_for_paypal_to_list_goes_to_a_person(
    db, refund_case, run_refund, paypal
):
    _, case = refund_case(age=reads.DISPUTE_LIST_MAX_AGE)  # minus the margin: past the limit
    run_refund()
    assert paypal.dispute_queries == []
    assert paypal.refund_calls == []
    assert _state(db, case) == (MoneyActionStatus.BLOCKED, CaseState.HOLD_FOR_REVIEW)
    [ma] = db.scalars(select(MoneyAction).where(MoneyAction.case_id == case.id)).all()
    assert ma.paypal_response["blocked_code"] == Reason.DISPUTE_CHECK_UNAVAILABLE.value


def test_PO_05_the_oldest_order_paypal_can_list_is_still_checked(
    db, refund_case, run_refund, paypal
):
    refund_case(age=reads.DISPUTE_LIST_MAX_AGE - DISPUTE_LOOKBACK_MARGIN)  # exactly at the limit
    run_refund()
    assert len(paypal.dispute_queries) == 1
    assert len(paypal.refund_calls) == 1


def test_PO_05_a_failed_dispute_read_sends_nothing_and_retries(db, refund_case, run_refund, paypal):
    _, case = refund_case()
    paypal.dispute_failures = [PayPalServerError(503, "unavailable")]
    run_refund()
    assert paypal.refund_calls == []
    assert _state(db, case)[0] == MoneyActionStatus.QUEUED  # fails closed, retried later
    [job] = db.scalars(select(Job).where(Job.kind == REFUND_JOB)).all()
    assert job.attempts == 1 and "PayPalServerError" in (job.last_error or "")


def test_PO_05_paypal_reopening_a_resolved_dispute_reopens_ours(
    db, refund_case, run_refund, paypal
):
    order, case = refund_case()
    db.add(Dispute(order_id=order.id, paypal_dispute_id="PP-D-1", status="resolved"))
    db.commit()
    paypal.open_disputes = [_summary("PP-D-1", str(order.capture_id))]  # e.g. an appeal
    run_refund()
    [d] = db.scalars(select(Dispute)).all()
    assert d.status == "open"
    [row] = db.scalars(select(AuditLog).where(AuditLog.action == "dispute.found_at_paypal")).all()
    assert row.before == {"status": "resolved"}
    assert _state(db, case) == (MoneyActionStatus.BLOCKED, CaseState.DISPUTE_EVIDENCE)


def test_PO_05_an_already_open_dispute_is_not_recorded_twice(db, refund_case, paypal, clock):
    order, case = refund_case()
    db.add(Dispute(order_id=order.id, paypal_dispute_id="PP-D-1", status="open"))
    db.commit()
    paypal.open_disputes = [_summary("PP-D-1", str(order.capture_id))]
    handler = RefundJobHandler(paypal, None, clock, disputes=paypal)  # type: ignore[arg-type]
    [ma] = db.scalars(select(MoneyAction).where(MoneyAction.case_id == case.id)).all()
    verdict = handler._disputes_at_paypal(db, order, str(order.capture_id))
    assert verdict is not None and verdict.code == Reason.DISPUTE_OPENED
    assert len(db.scalars(select(Dispute)).all()) == 1
    assert (
        db.scalars(select(AuditLog).where(AuditLog.action == "dispute.found_at_paypal")).all() == []
    )
    assert ma.status == MoneyActionStatus.QUEUED


def test_PO_05_a_refund_that_already_landed_is_recorded_not_blocked(
    db, refund_case, run_refund, paypal
):
    order, case = refund_case()
    [ma] = db.scalars(select(MoneyAction).where(MoneyAction.case_id == case.id)).all()
    paypal.captures[str(order.capture_id)].refunds.append(  # sent, but our commit was lost
        FakeRefund(ma.request_id, 3000, "R-LANDED", custom_id=ma.id)
    )
    paypal.open_disputes = [_summary("PP-D-1", str(order.capture_id))]
    run_refund()
    assert _state(db, case)[0] == MoneyActionStatus.COMPLETED  # money already moved: record it
    assert paypal.refund_calls == []


def test_PO_05_paypal_refusing_the_dispute_check_holds_with_its_reason(
    db, refund_case, run_refund, paypal
):
    _, case = refund_case()
    paypal.dispute_failures = [PayPalClientError(403, "NOT_AUTHORIZED")]  # review F2
    run_refund()
    assert paypal.refund_calls == []
    assert _state(db, case) == (MoneyActionStatus.BLOCKED, CaseState.HOLD_FOR_REVIEW)
    [ma] = db.scalars(select(MoneyAction).where(MoneyAction.case_id == case.id)).all()
    assert ma.paypal_response["blocked_code"] == Reason.DISPUTE_CHECK_REFUSED.value
    assert "403" in ma.paypal_response["blocked"]  # the person sees why, not "refund failed"
    [job] = db.scalars(select(Job).where(Job.kind == REFUND_JOB)).all()
    assert job.status == "done"


def test_PO_05_a_rate_limited_dispute_check_waits_and_retries(db, refund_case, run_refund, paypal):
    _, case = refund_case()
    paypal.dispute_failures = [PayPalClientError(429, "RATE_LIMIT_REACHED")]
    run_refund()
    assert paypal.refund_calls == []
    assert _state(db, case)[0] == MoneyActionStatus.QUEUED
    [job] = db.scalars(select(Job).where(Job.kind == REFUND_JOB)).all()
    assert (job.status, job.attempts) == ("queued", 1)


def test_PO_05_a_dispute_a_webhook_records_at_the_same_moment_is_kept_once(
    db, refund_case, monkeypatch, clock
):
    order, _ = refund_case()
    db.add(Dispute(order_id=order.id, paypal_dispute_id="PP-D-RACE", status="open"))
    db.commit()
    real_scalar = db.scalar
    calls = {"n": 0}

    def missed_once(*args, **kwargs):  # our read ran just before the webhook's insert committed
        calls["n"] += 1
        return None if calls["n"] == 1 else real_scalar(*args, **kwargs)

    monkeypatch.setattr(db, "scalar", missed_once)
    _record_dispute_found(db, order, _summary("PP-D-RACE", str(order.capture_id)), at=clock.now())
    monkeypatch.undo()
    db.commit()  # the outer transaction survives the failed insert
    assert len(db.scalars(select(Dispute)).all()) == 1
    assert (
        db.scalars(select(AuditLog).where(AuditLog.action == "dispute.found_at_paypal")).all() == []
    )


def test_PO_05_paypal_is_never_asked_further_back_than_it_allows():
    assert reads.DISPUTE_LIST_MAX_AGE < timedelta(days=180)  # 180+ days: INVALID_START_TIME_RANGE


# ---------------------------------------------------------------- R1: the real gateways


class _Client:
    """Answers app.paypal_client.client.PayPalClient.request from canned pages."""

    def __init__(self, pages: dict[str, dict[str, Any]]) -> None:
        self.pages = pages
        self.paths: list[str] = []

    def request(self, method: str, path: str, **_: Any) -> dict[str, Any]:
        assert method == "GET"
        self.paths.append(path)
        return self.pages[path]


SINCE = datetime(2026, 11, 1, 9, 30, 15, 123456, tzinfo=UTC)
LIST = "/v1/customer/disputes?start_time=2026-11-01T09%3A30%3A15.000Z&page_size=50"


def test_PO_05_paypal_disputes_are_listed_by_time_and_read_for_their_captures():
    client = _Client(
        {
            LIST: {
                "items": [
                    {"dispute_id": "PP-D-1", "status": "WAITING_FOR_SELLER_RESPONSE"},
                    {"dispute_id": "PP-D-2", "status": "RESOLVED"},
                    {"dispute_id": "PP-D-3", "status": "under_review"},
                    {"status": "OPEN"},
                    {"dispute_id": "PP-D-4"},  # no status: counts as open
                    {  # the list already names the capture: no extra read (review F3)
                        "dispute_id": "PP-D-5",
                        "status": "OPEN",
                        "disputed_transactions": [{"seller_transaction_id": "CAP-5"}],
                    },
                ],
                "links": [{"rel": "self", "href": "x"}],
            },
            "/v1/customer/disputes/PP-D-1": {
                "disputed_transactions": [
                    {"seller_transaction_id": "CAP-1"},
                    {"buyer_transaction_id": "B-1"},
                    "junk",
                ]
            },
            "/v1/customer/disputes/PP-D-3": {},
            "/v1/customer/disputes/PP-D-4": {},
        }
    )
    found = reads.list_open_disputes_since(client, SINCE)  # type: ignore[arg-type]
    assert found == [
        DisputeSummary(
            dispute_id="PP-D-1", status="WAITING_FOR_SELLER_RESPONSE", capture_ids=["CAP-1"]
        ),
        DisputeSummary(dispute_id="PP-D-3", status="UNDER_REVIEW", capture_ids=[]),
        DisputeSummary(dispute_id="", status="OPEN", capture_ids=[]),
        DisputeSummary(dispute_id="PP-D-4", status="", capture_ids=[]),
        DisputeSummary(dispute_id="PP-D-5", status="OPEN", capture_ids=["CAP-5"]),
    ]
    assert "/v1/customer/disputes/PP-D-2" not in client.paths  # resolved: no extra read
    assert "/v1/customer/disputes/PP-D-5" not in client.paths


def test_PO_05_paypal_dispute_pages_are_followed():
    page2 = LIST + "&next_page_token=T2"
    client = _Client(
        {
            LIST: {
                "items": [],
                "links": [{"rel": "next", "href": "https://api-m.sandbox.paypal.com" + page2}],
            },
            page2: {"items": [{"dispute_id": "PP-D-9", "status": "OPEN"}]},
            "/v1/customer/disputes/PP-D-9": {
                "disputed_transactions": [{"seller_transaction_id": "CAP-9"}]
            },
        }
    )
    found = reads.list_open_disputes_since(client, SINCE)  # type: ignore[arg-type]
    assert [d.dispute_id for d in found] == ["PP-D-9"]
    assert client.paths[:2] == [LIST, page2]


def test_PO_05_a_next_page_without_a_token_fails_closed():
    client = _Client({LIST: {"items": [], "links": [{"rel": "next", "href": "https://x/y?a=1"}]}})
    with pytest.raises(PayPalServerError):
        reads.list_open_disputes_since(client, SINCE)  # type: ignore[arg-type]


def test_PO_05_endless_dispute_pages_fail_closed():
    class Endless(_Client):
        def request(self, method: str, path: str, **_: Any) -> dict[str, Any]:
            self.paths.append(path)
            return {"items": [], "links": [{"rel": "next", "href": "/p?next_page_token=again"}]}

    client = Endless({})
    with pytest.raises(PayPalServerError):
        reads.list_open_disputes_since(client, SINCE)  # type: ignore[arg-type]
    assert len(client.paths) == 10


def test_PO_05_the_production_refund_gateways_read_disputes_live(session_factory):
    client = _Client(
        {
            LIST: {"items": [{"dispute_id": "PP-D-1", "status": "OPEN"}]},
            "/v1/customer/disputes/PP-D-1": {},
        }
    )
    real = RestRefundGateway(client)  # type: ignore[arg-type]
    routed = RoutingRefundGateway(real, SimulatedRefundGateway(session_factory))
    assert isinstance(real, DisputeLookup) and isinstance(routed, DisputeLookup)
    assert routed.open_disputes_since(SINCE, capture_id="SIM-CAP-1") == []  # never reached PayPal
    assert client.paths == []
    [found] = routed.open_disputes_since(SINCE, capture_id="CAP-1")
    assert found.dispute_id == "PP-D-1"


def test_PO_05_the_worker_refund_handler_asks_paypal_for_disputes(monkeypatch, tmp_path):
    from app.runtime import build_runtime
    from app.worker.main import build_worker

    for key in (
        "PAYPAL_WEBHOOK_ID",
        "SHIPPO_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "SIGNING_SECRET",
        "RECEIPT_SIGNING_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'rt.db'}")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
    _, runner = build_worker(build_runtime())
    refunds = runner.handlers[REFUND_JOB].inner
    assert isinstance(refunds, RefundJobHandler)
    lookup = refunds._disputes  # production never runs the re-check without a live read
    assert isinstance(lookup, RoutingRefundGateway)
    assert isinstance(lookup.real, RestRefundGateway)


def test_PO_05_a_routed_gateway_that_cannot_read_disputes_fails_closed(session_factory):
    routed = RoutingRefundGateway(FakePayPal(), SimulatedRefundGateway(session_factory))
    with pytest.raises(TypeError):  # never "no disputes" just because nobody could ask
        routed.open_disputes_since(SINCE, capture_id="CAP-1")
    assert routed.open_disputes_since(SINCE, capture_id="SIM-CAP-1") == []


# ---------------------------------------------------------------- R3: event order


def _event(db, rulebook, clock, event_type: str, resource: dict[str, Any]) -> None:
    row = WebhookInbox(
        event_id=f"WH-{event_type}-{len(resource)}",
        source="paypal",
        event_type=event_type,
        payload={"id": "WH-1", "event_type": event_type, "resource": resource},
        status="pending",
        attempts=0,
    )
    paypal_inbox_handlers(rulebook, clock)[("paypal", event_type)](db, row)
    db.flush()


def _dispute(order: Order, status: str) -> dict[str, Any]:
    return {
        "dispute_id": "PP-D-1",
        "status": status,
        "disputed_transactions": [{"seller_transaction_id": order.capture_id}],
    }


def test_PO_05_a_late_dispute_update_never_reopens_a_resolved_dispute(db, rulebook, clock):
    order = make_order(db, make_seller(db))
    _event(db, rulebook, clock, "CUSTOMER.DISPUTE.CREATED", _dispute(order, "OPEN"))
    _event(db, rulebook, clock, "CUSTOMER.DISPUTE.RESOLVED", _dispute(order, "RESOLVED"))
    _event(db, rulebook, clock, "CUSTOMER.DISPUTE.UPDATED", _dispute(order, "UNDER_REVIEW"))
    [d] = db.scalars(select(Dispute)).all()
    assert d.status == "resolved"


def test_PO_05_resolved_before_created_stays_resolved(db, rulebook, clock):
    order = make_order(db, make_seller(db))
    _event(db, rulebook, clock, "CUSTOMER.DISPUTE.RESOLVED", _dispute(order, "RESOLVED"))
    _event(db, rulebook, clock, "CUSTOMER.DISPUTE.CREATED", _dispute(order, "OPEN"))
    [d] = db.scalars(select(Dispute)).all()
    assert d.status == "resolved"


def test_PO_05_an_open_dispute_still_resolves(db, rulebook, clock):
    order = make_order(db, make_seller(db))
    _event(db, rulebook, clock, "CUSTOMER.DISPUTE.CREATED", _dispute(order, "OPEN"))
    _event(db, rulebook, clock, "CUSTOMER.DISPUTE.UPDATED", _dispute(order, "UNDER_REVIEW"))
    [d] = db.scalars(select(Dispute)).all()
    assert d.status == "open"
    _event(db, rulebook, clock, "CUSTOMER.DISPUTE.RESOLVED", _dispute(order, "RESOLVED"))
    assert d.status == "resolved"
