"""Outbox (jobs) and inbox (webhook_inbox) tables."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime, new_id


def _now() -> datetime:
    return datetime.now(UTC)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    run_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|done|failed
    last_error: Mapped[str | None] = mapped_column(Text)
    dedupe_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)


class WebhookInbox(Base):
    __tablename__ = "webhook_inbox"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_id: Mapped[str] = mapped_column(String(128), unique=True)
    source: Mapped[str] = mapped_column(String(16))  # paypal | shippo
    event_type: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(
        String(16), default="pending"
    )  # pending|processed|ignored|failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
