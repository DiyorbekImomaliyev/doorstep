"""Outbox job runner: retries with backoff, same payload (so same PayPal-Request-Id)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy.orm import Session, sessionmaker

from app.clock import Clock
from app.config import Settings
from app.worker.models import Job


class RetryableError(Exception):
    """Handler asks for a retry. PayPalServerError is also retried."""


class PermanentError(Exception):
    """Handler gives up at once. PayPalClientError is also permanent."""


class JobHandler(Protocol):
    def run(self, session: Session, job: Job) -> None: ...

    def on_failed(self, session: Session, job: Job, error: Exception) -> None:
        """Called once when the job is marked failed (retries exhausted or permanent error)."""
        ...


def enqueue(
    session: Session,
    kind: str,
    payload: dict[str, Any],
    *,
    clock: Clock,
    dedupe_key: str | None = None,
    run_at: datetime | None = None,
) -> Job:
    raise NotImplementedError


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
        raise NotImplementedError
