"""R7: the pipeline's golden signals, so a stalled worker or held money is seen, not just handled.

Counts only, never personal data. `/healthz` stays HTTP 200: a restart of the web service cannot
fix a stalled worker, so the status is in the body for an uptime checker to alert on.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.ai.usage import ModelUsage
from app.ops.signals import LAG_LIMIT, pipeline_signals
from app.orders.enums import CaseState, MoneyActionKind, MoneyActionStatus
from app.orders.models import MoneyAction
from app.worker.models import Job, WebhookInbox
from tests.factories import make_case, make_order, make_seller

BLOCKED, FAILED = MoneyActionStatus.BLOCKED.value, MoneyActionStatus.FAILED.value
HOUR = 3600


def _inbox(db, *, age_s: float, status: str = "pending", now) -> None:
    db.add(
        WebhookInbox(
            event_id=f"EV-{age_s}-{status}-{len(db.new)}",
            source="paypal",
            event_type="PAYMENT.CAPTURE.COMPLETED",
            payload={},
            status=status,
            attempts=0,
            received_at=now - timedelta(seconds=age_s),
        )
    )
    db.flush()


def _job(db, *, due_in_s: float, status: str = "queued", now) -> None:
    db.add(
        Job(
            kind="paypal.refund",
            payload={},
            run_at=now + timedelta(seconds=due_in_s),
            status=status,
        )
    )
    db.flush()


def _money(
    db,
    *,
    status: str,
    now,
    code: str | None = "",
    age_s: float = 60,
    kind=MoneyActionKind.REFUND,
    case_state: CaseState | None = None,
) -> None:
    order = make_order(db, make_seller(db))
    case_id = None
    if case_state is not None:
        case = make_case(db, order)
        case.state = case_state.value
        case_id = case.id
    response = {} if code is None else ({"blocked_code": code} if code else {"error": "x"})
    db.add(
        MoneyAction(
            order_id=order.id,
            case_id=case_id,
            seller_id=order.seller_id,
            kind=kind.value,
            amount_minor=1000,
            currency="GBP",
            status=status,
            paypal_response=response,
            created_at=now - timedelta(seconds=age_s),
        )
    )
    db.flush()


def test_R7_a_quiet_pipeline_is_ok(db, clock):
    s = pipeline_signals(db, clock.now())
    assert (s.status, s.reasons) == ("ok", [])
    counts = s.model_dump(exclude={"status", "reasons", "money_blocked_24h"})
    assert set(counts.values()) == {0}
    assert s.money_blocked_24h == {}


# ---------------------------------------------------------------- lag


@pytest.mark.parametrize(("age_s", "status"), [(LAG_LIMIT, "ok"), (LAG_LIMIT + 1, "degraded")])
def test_R7_a_webhook_waiting_past_the_limit_degrades(db, clock, age_s, status):
    now = clock.now()
    _inbox(db, age_s=age_s, now=now)
    _inbox(db, age_s=5, now=now)
    _inbox(db, age_s=900, status="processed", now=now)  # done: not waiting
    s = pipeline_signals(db, now)
    assert (s.inbox_pending, s.inbox_oldest_pending_s) == (2, age_s)
    assert s.status == status
    assert s.reasons == ([] if status == "ok" else ["inbox_lagging"])


@pytest.mark.parametrize(("late_s", "status"), [(LAG_LIMIT, "ok"), (LAG_LIMIT + 1, "degraded")])
def test_R7_a_job_overdue_past_the_limit_degrades(db, clock, late_s, status):
    now = clock.now()
    _job(db, due_in_s=-late_s, now=now)
    _job(db, due_in_s=600, now=now)  # backing off: queued, not overdue
    _job(db, due_in_s=-9_000, status="done", now=now)
    s = pipeline_signals(db, now)
    assert (s.jobs_queued, s.jobs_oldest_overdue_s) == (2, late_s)
    assert s.reasons == ([] if status == "ok" else ["jobs_lagging"])


def test_R7_a_job_due_later_is_not_overdue(db, clock):
    now = clock.now()
    _job(db, due_in_s=600, now=now)
    assert pipeline_signals(db, now).jobs_oldest_overdue_s == 0


# ---------------------------------------------------------------- failures: an hour, then a count


def test_R7_a_failure_in_the_last_hour_degrades(db, clock):
    now = clock.now()
    _job(db, due_in_s=-HOUR, status="failed", now=now)  # exactly an hour ago
    _inbox(db, age_s=HOUR, status="failed", now=now)
    _money(db, status=FAILED, age_s=HOUR, now=now)
    s = pipeline_signals(db, now)
    assert s.reasons == ["webhooks_failed", "jobs_failed", "money_writes_failed"]


def test_R7_an_older_failure_is_counted_but_no_longer_degrades(db, clock):
    now = clock.now()
    _job(db, due_in_s=-HOUR - 1, status="failed", now=now)
    _inbox(db, age_s=HOUR + 1, status="failed", now=now)
    _money(db, status=FAILED, age_s=HOUR + 1, now=now)
    s = pipeline_signals(db, now)
    assert (s.jobs_failed_24h, s.inbox_failed_24h, s.money_failed_24h) == (1, 1, 1)
    assert (s.status, s.reasons) == ("ok", [])


def test_R7_a_failure_older_than_a_day_is_not_counted(db, clock):
    now = clock.now()
    _job(db, due_in_s=-25 * HOUR, status="failed", now=now)
    _inbox(db, age_s=25 * HOUR, status="failed", now=now)
    _money(db, status=FAILED, age_s=25 * HOUR, now=now)
    _money(db, status=MoneyActionStatus.COMPLETED.value, now=now)
    s = pipeline_signals(db, now)
    assert (s.jobs_failed_24h, s.inbox_failed_24h, s.money_failed_24h) == (0, 0, 0)


def test_R7_a_dispute_evidence_write_that_failed_counts_too(db, clock):
    now = clock.now()
    _money(db, status=FAILED, kind=MoneyActionKind.EVIDENCE, now=now)
    assert pipeline_signals(db, now).money_failed_24h == 1


# ---------------------------------------------------------------- held money: state, no window


def test_R7_blocked_money_writes_are_counted_by_reason(db, clock):
    now = clock.now()
    for _ in range(2):
        _money(db, status=BLOCKED, code="dispute_opened", now=now)
    _money(db, status=BLOCKED, code="unmatched_dispute", now=now)
    _money(db, status=BLOCKED, code=None, now=now)  # a block with no code is still counted
    _money(db, status=BLOCKED, code="dispute_opened", age_s=25 * HOUR, now=now)
    s = pipeline_signals(db, now)
    assert s.money_blocked_24h == {"dispute_opened": 2, "unknown": 1, "unmatched_dispute": 1}
    assert s.status == "ok"  # no case waits on a reviewer: diagnosis, not an alert


@pytest.mark.parametrize("status", [BLOCKED, FAILED])
def test_R7_money_held_for_a_reviewer_degrades_at_any_age(db, clock, status):
    now = clock.now()
    _money(db, status=status, age_s=10 * 24 * HOUR, case_state=CaseState.HOLD_FOR_REVIEW, now=now)
    s = pipeline_signals(db, now)
    assert (s.money_waiting_for_a_person, s.reasons) == (1, ["money_waiting_for_a_person"])


@pytest.mark.parametrize("state", [CaseState.SETTLED, CaseState.CLOSED, CaseState.DISPUTE_EVIDENCE])
def test_R7_held_money_stops_alerting_once_its_case_moves_on(db, clock, state):
    now = clock.now()
    _money(db, status=BLOCKED, case_state=state, now=now)
    _money(
        db, status=MoneyActionStatus.COMPLETED.value, case_state=CaseState.HOLD_FOR_REVIEW, now=now
    )
    assert pipeline_signals(db, now).money_waiting_for_a_person == 0


# ---------------------------------------------------------------- model calls


def test_R7_paid_model_calls_are_counted_for_today_only(db, clock):
    now = clock.now()
    today = now.date()
    db.add(ModelUsage(day=today, provider="anthropic", calls=7))
    db.add(ModelUsage(day=today, provider="other", calls=3))
    db.add(ModelUsage(day=today - timedelta(days=1), provider="anthropic", calls=50))
    db.flush()
    assert pipeline_signals(db, now).paid_model_calls_today == 10


# ---------------------------------------------------------------- /healthz


@pytest.fixture
def health_api(monkeypatch, tmp_path) -> TestClient:
    from app.main import create_app
    from app.runtime import build_runtime

    for key in (
        "PAYPAL_CLIENT_ID",
        "PAYPAL_CLIENT_SECRET",
        "SIGNING_SECRET",
        "RECEIPT_SIGNING_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'rt.db'}")
    monkeypatch.setenv("APP_ENV", "development")
    return TestClient(create_app(build_runtime().deps))


def test_R7_healthz_shows_the_pipeline_as_counts(health_api):
    response = health_api.get("/healthz")
    assert response.status_code == 200
    pipeline = response.json()["pipeline"]
    assert (pipeline["status"], pipeline["reasons"]) == ("ok", [])
    numbers = {k: v for k, v in pipeline.items() if k not in ("status", "reasons")}
    assert numbers["money_blocked_24h"] == {}
    assert all(isinstance(v, int) for k, v in numbers.items() if k != "money_blocked_24h")


def test_R7_healthz_stays_up_when_the_signals_fail(health_api, monkeypatch):
    """Design review R7-1: Render restarts a service whose health check fails."""

    def broken(*_: object) -> None:
        raise OperationalError("SELECT ...", {}, Exception("canceling statement due to timeout"))

    monkeypatch.setattr("app.api.journey.pipeline_signals", broken)
    response = health_api.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["pipeline"] is None
    assert body["webhooks"]["paypal"]["received"] == 0  # the rest of the check still answers
