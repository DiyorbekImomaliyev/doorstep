"""Outbox job runner: retries with backoff, same payload (so same PayPal-Request-Id).

Each job runs in its own transaction. On success the handler's writes and the job's ``done``
status commit together. On failure the handler's writes are rolled back, then the attempt is
recorded in a fresh transaction: either a backoff (retryable) or ``failed`` plus the handler's
``on_failed`` hook and a ``job.failed`` audit row (retries exhausted or permanent error).
A failed job is never deleted (PP-04).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.clock import Clock
from app.config import Settings
from app.orders.models import AuditLog
from app.paypal_client.errors import PayPalClientError
from app.worker.models import Job

log = logging.getLogger("doorstep.worker")

QUEUED, DONE, FAILED = "queued", "done", "failed"


class RetryableError(Exception):
    """Handler asks for a retry. PayPalServerError is also retried."""


class PermanentError(Exception):
    """Handler gives up at once. PayPalClientError is also permanent."""


class JobHandler(Protocol):
    def run(self, session: Session, job: Job) -> None: ...

    def on_failed(self, session: Session, job: Job, error: Exception) -> None:
        """Called once when the job is marked failed (retries exhausted or permanent error)."""
        ...


def is_permanent(error: Exception) -> bool:
    """4xx and explicit PermanentError never succeed on retry; everything else may."""
    return isinstance(error, PermanentError | PayPalClientError)


def enqueue(
    session: Session,
    kind: str,
    payload: dict[str, Any],
    *,
    clock: Clock,
    dedupe_key: str | None = None,
    run_at: datetime | None = None,
) -> Job:
    """Add a job in the caller's transaction. A repeated dedupe_key returns the existing job."""
    if dedupe_key is not None:
        existing = session.scalar(select(Job).where(Job.dedupe_key == dedupe_key))
        if existing is not None:
            return existing
    now = clock.now()
    job = Job(
        kind=kind,
        payload=payload,
        run_at=run_at or now,
        attempts=0,
        status=QUEUED,
        dedupe_key=dedupe_key,
        created_at=now,
    )
    if dedupe_key is None:
        session.add(job)
        session.flush()
        return job
    try:
        with session.begin_nested():
            session.add(job)
    except IntegrityError:  # a concurrent writer claimed the same dedupe_key first
        existing = session.scalar(select(Job).where(Job.dedupe_key == dedupe_key))
        assert existing is not None
        return existing
    return job


class JobRunner:
    """Backoff after attempt n: backoff_base_s * 2**(n-1). Fails after settings.max_attempts."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        handlers: Mapping[str, JobHandler],
        clock: Clock,
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.handlers = handlers
        self.clock = clock
        self.settings = settings

    def run_due(self) -> int:
        """Run every queued job whose run_at <= now, once. Returns the number run."""
        now = self.clock.now()
        with self.session_factory() as session:
            due = list(
                session.scalars(
                    select(Job.id)
                    .where(
                        Job.status == QUEUED,
                        Job.run_at <= now,
                        Job.kind.in_(list(self.handlers)),
                    )
                    .order_by(Job.run_at, Job.created_at)
                )
            )
        return sum(1 for job_id in due if self._run_one(job_id))

    def _claim(self, session: Session, job_id: str) -> Job | None:
        """Lock the job row; skip it if another worker holds it or it is no longer due."""
        job = session.scalar(select(Job).where(Job.id == job_id).with_for_update(skip_locked=True))
        if job is None or job.status != QUEUED or job.run_at > self.clock.now():
            return None
        return job

    def _run_one(self, job_id: str) -> bool:
        with self.session_factory() as session:
            job = self._claim(session, job_id)
            if job is None:
                return False
            handler = self.handlers[job.kind]
            try:
                handler.run(session, job)
            except Exception as error:  # every failure is recorded on the job
                session.rollback()
                self._record_failure(job_id, handler, error)
                return True
            job.attempts += 1
            job.status = DONE
            job.last_error = None
            session.commit()
            return True

    def _record_failure(self, job_id: str, handler: JobHandler, error: Exception) -> None:
        with self.session_factory() as session:
            job = self._claim(session, job_id)
            if job is None:  # pragma: no cover - lost the row between transactions
                return
            job.attempts += 1
            job.last_error = f"{type(error).__name__}: {error}"[:2000]
            exhausted = job.attempts >= self.settings.max_attempts
            if is_permanent(error) or exhausted:
                job.status = FAILED
                handler.on_failed(session, job, error)
                session.add(
                    AuditLog(
                        actor="worker",
                        action="job.failed",
                        entity_type="job",
                        entity_id=job.id,
                        before={"status": QUEUED},
                        after={
                            "status": FAILED,
                            "kind": job.kind,
                            "attempts": job.attempts,
                            "error": job.last_error,
                        },
                        at=self.clock.now(),
                    )
                )
                log.warning("job %s (%s) failed: %s", job.id, job.kind, job.last_error)
            else:
                delay = self.settings.backoff_base_s * 2 ** (job.attempts - 1)
                job.run_at = self.clock.now() + timedelta(seconds=delay)
                log.info("job %s (%s) retry in %ss: %s", job.id, job.kind, delay, job.last_error)
            session.commit()
