"""EX-06: the daily call count for the paid main provider, kept in the database.

The web service and the worker are separate processes on Render; a count in memory would give each
its own cap. One row per (UTC day, provider), incremented under a row lock, is one shared count.
If the count cannot be read, the paid call is not made: a cost cap fails closed, and every caller
already has a fallback (the free provider, then its template).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime

from sqlalchemy import Date, Integer, String
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db import Base

log = logging.getLogger("doorstep.ai.usage")


class ModelUsage(Base):
    __tablename__ = "model_usage"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, default=0)


def _utc_today() -> date:
    return datetime.now(UTC).date()


class DbCallBudget:
    """`take(provider)`: True and counted while today's count is under the limit."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        limit: int,
        *,
        today: Callable[[], date] = _utc_today,
    ) -> None:
        self.session_factory = session_factory
        self.limit = limit
        self.today = today

    def take(self, provider: str) -> bool:
        if self.limit <= 0:
            return False
        day = self.today()
        for _ in range(2):  # a second try when another process wrote today's first row
            try:
                with self.session_factory() as s, s.begin():
                    row = s.get(ModelUsage, (day, provider), with_for_update=True)
                    if row is None:
                        s.add(ModelUsage(day=day, provider=provider, calls=1))
                        return True
                    if row.calls >= self.limit:
                        return False
                    row.calls += 1
                    return True
            except IntegrityError:
                continue
            except SQLAlchemyError as exc:
                log.warning("model call count unreadable (%s); no paid call", type(exc).__name__)
                return False
        return False
