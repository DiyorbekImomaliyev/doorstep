"""Processes webhook_inbox rows (WH-02, WH-03, WH-05, WH-07).

Each row is claimed under a row lock and processed in its own transaction. A raising handler's
writes are rolled back; the attempt and error are then recorded, and after
settings.max_attempts the row is marked "failed" and kept for a human (never deleted).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.clock import Clock
from app.config import Settings
from app.worker.models import WebhookInbox

log = logging.getLogger("doorstep.inbox")

InboxHandler = Callable[[Session, WebhookInbox], None]
PENDING, PROCESSED, IGNORED, FAILED = "pending", "processed", "ignored", "failed"


class InboxProcessor:
    """Handlers keyed by (source, event_type). Unknown types -> status "ignored"."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        handlers: Mapping[tuple[str, str], InboxHandler],
        clock: Clock,
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.handlers = handlers
        self.clock = clock
        self.settings = settings

    def run_pending(self) -> int:
        """Process each pending row once. A raising handler increments attempts; at
        settings.max_attempts the row is marked "failed" (never deleted).

        Returns the rows that reached a final state (processed or ignored). A failed attempt is not
        counted, so the worker loop sleeps before the next try instead of spending every attempt
        within milliseconds on a passing outage."""
        with self.session_factory() as session:
            ids = list(
                session.scalars(
                    select(WebhookInbox.id)
                    .where(WebhookInbox.status == PENDING)
                    .order_by(WebhookInbox.received_at, WebhookInbox.id)
                )
            )
        return sum(1 for row_id in ids if self._process(row_id))

    @staticmethod
    def _claim(session: Session, row_id: str) -> WebhookInbox | None:
        row = session.scalar(
            select(WebhookInbox).where(WebhookInbox.id == row_id).with_for_update(skip_locked=True)
        )
        return row if row is not None and row.status == PENDING else None

    def _process(self, row_id: str) -> bool:
        with self.session_factory() as session:
            row = self._claim(session, row_id)
            if row is None:
                return False
            handler = self.handlers.get((row.source, row.event_type))
            if handler is None:
                row.status = IGNORED
                row.processed_at = self.clock.now()
                session.commit()
                return True
            try:
                handler(session, row)
            except Exception as error:  # every failure is recorded on the row
                session.rollback()
                self._record_failure(row_id, error)
                return False
            row.attempts += 1
            row.status = PROCESSED
            row.processed_at = self.clock.now()
            row.last_error = None
            session.commit()
            return True

    def _record_failure(self, row_id: str, error: Exception) -> None:
        with self.session_factory() as session:
            row = self._claim(session, row_id)
            if row is None:  # pragma: no cover - lost the row between transactions
                return
            row.attempts += 1
            row.last_error = f"{type(error).__name__}: {error}"[:2000]
            if row.attempts >= self.settings.max_attempts:
                row.status = FAILED
                log.error("inbox %s (%s) failed: %s", row.event_id, row.event_type, row.last_error)
            session.commit()
