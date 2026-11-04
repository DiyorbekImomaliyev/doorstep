"""Processes webhook_inbox rows (WH-02, WH-03, WH-05, WH-07)."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from sqlalchemy.orm import Session, sessionmaker

from app.clock import Clock
from app.config import Settings
from app.worker.models import WebhookInbox

InboxHandler = Callable[[Session, WebhookInbox], None]


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
        settings.max_attempts the row is marked "failed" (never deleted)."""
        raise NotImplementedError
