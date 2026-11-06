"""R7: the pipeline's golden signals, read from Postgres. Counts only, never personal data.

Handling a failure is not enough; someone has to see it. These are the few numbers that show the
pipeline has stopped, or that money is waiting for a person:

- webhooks waiting, and how long the oldest has waited (the inbox);
- jobs due, and how late the oldest is (the outbox);
- webhooks, jobs and PayPal money writes (refunds, dispute evidence) that failed;
- money writes held for a person: blocked or failed, with the case still in HOLD_FOR_REVIEW;
- paid model calls today.

`status` is `degraded` with machine reasons when one crosses a line, so any uptime checker can
alert on the body of `/healthz`. The HTTP status stays 200: restarting the web service cannot fix a
stalled worker.

Two kinds of alert, so the status is neither sticky nor forgetful (design review R7-3, R7-4):

- **State** ("money is waiting for a person") has no time window. It degrades until a person
  settles or closes the case, however old it is.
- **Events** (a failure) degrade for an hour, then stay visible as 24-hour counts. One old failure
  does not keep the status degraded all day.

Every query reads a small table. `audit_log`, the largest, is never scanned, because Render calls
`/healthz` as its health check.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.usage import ModelUsage
from app.orders.enums import CaseState, MoneyActionStatus
from app.orders.models import Case, MoneyAction
from app.worker.models import Job, WebhookInbox

LAG_LIMIT = 300  # seconds: a webhook or a due job waiting longer means the worker is not keeping up
FRESH = timedelta(hours=1)  # a failure degrades the status this long
WINDOW = timedelta(hours=24)  # and is counted this long
_HELD = (MoneyActionStatus.BLOCKED.value, MoneyActionStatus.FAILED.value)


class PipelineSignals(BaseModel):
    status: Literal["ok", "degraded"]
    reasons: list[str]
    inbox_pending: int
    inbox_oldest_pending_s: int
    inbox_failed_24h: int
    jobs_queued: int
    jobs_oldest_overdue_s: int
    jobs_failed_24h: int
    money_blocked_24h: dict[str, int]
    money_failed_24h: int
    money_waiting_for_a_person: int
    paid_model_calls_today: int


def _age_s(now: datetime, since: datetime | None) -> int:
    return int((now - since).total_seconds()) if since is not None else 0


def _count(session: Session, *where: object) -> int:
    return int(session.scalar(select(func.count()).where(*where)) or 0)  # type: ignore[arg-type]


def pipeline_signals(session: Session, now: datetime) -> PipelineSignals:
    day, fresh = now - WINDOW, now - FRESH

    pending = WebhookInbox.status == "pending"
    inbox_pending = _count(session, pending)
    oldest_webhook = session.scalar(select(func.min(WebhookInbox.received_at)).where(pending))
    failed_webhook = WebhookInbox.status == "failed"
    inbox_failed = _count(session, failed_webhook, WebhookInbox.received_at >= day)
    inbox_failed_fresh = _count(session, failed_webhook, WebhookInbox.received_at >= fresh)

    queued = Job.status == "queued"
    jobs_queued = _count(session, queued)
    oldest_due = session.scalar(select(func.min(Job.run_at)).where(queued, Job.run_at <= now))
    # A failed job's run_at is its last attempt, the closest record of when it failed.
    failed_job = Job.status == "failed"
    jobs_failed = _count(session, failed_job, Job.run_at >= day)
    jobs_failed_fresh = _count(session, failed_job, Job.run_at >= fresh)

    # Money writes made in the last day that ended blocked or failed, for diagnosis. A write is
    # blocked or fails within minutes of being made (its job's retries).
    blocked: Counter[str] = Counter()
    money_failed = money_failed_fresh = 0
    for status, response, created_at in session.execute(
        select(MoneyAction.status, MoneyAction.paypal_response, MoneyAction.created_at).where(
            MoneyAction.status.in_(_HELD), MoneyAction.created_at >= day
        )
    ):
        if status == MoneyActionStatus.FAILED.value:
            money_failed += 1
            money_failed_fresh += created_at >= fresh
        else:
            blocked[str((response or {}).get("blocked_code") or "unknown")] += 1
    # The state alert: held money whose case is still waiting for a reviewer, at any age.
    # dispute_opened routes its case to DISPUTE_EVIDENCE by design, so it is not counted here.
    waiting = int(
        session.scalar(
            select(func.count(func.distinct(MoneyAction.case_id)))  # cases, not rows (R23)
            .join(Case, Case.id == MoneyAction.case_id)
            .where(
                MoneyAction.status.in_(_HELD),
                Case.state == CaseState.HOLD_FOR_REVIEW.value,
            )
        )
        or 0
    )
    model_calls = int(
        session.scalar(
            select(func.coalesce(func.sum(ModelUsage.calls), 0)).where(
                ModelUsage.day == now.astimezone(UTC).date()  # the cap counts UTC days
            )
        )
        or 0
    )

    inbox_age, job_age = _age_s(now, oldest_webhook), _age_s(now, oldest_due)
    checks = [
        ("inbox_lagging", inbox_age > LAG_LIMIT),
        ("webhooks_failed", inbox_failed_fresh > 0),
        ("jobs_lagging", job_age > LAG_LIMIT),
        ("jobs_failed", jobs_failed_fresh > 0),
        ("money_writes_failed", money_failed_fresh > 0),
        ("money_waiting_for_a_person", waiting > 0),
    ]
    reasons = [name for name, hit in checks if hit]
    return PipelineSignals(
        status="degraded" if reasons else "ok",
        reasons=reasons,
        inbox_pending=inbox_pending,
        inbox_oldest_pending_s=inbox_age,
        inbox_failed_24h=inbox_failed,
        jobs_queued=jobs_queued,
        jobs_oldest_overdue_s=job_age,
        jobs_failed_24h=jobs_failed,
        money_blocked_24h=dict(sorted(blocked.items())),
        money_failed_24h=money_failed,
        money_waiting_for_a_person=waiting,
        paid_model_calls_today=model_calls,
    )
