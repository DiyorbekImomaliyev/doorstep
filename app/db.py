"""SQLAlchemy base, ids and session factory."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Dialect, Engine, create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    return str(uuid.uuid4())


class UTCDateTime(TypeDecorator[datetime]):
    """Always timezone-aware UTC in Python, on Postgres and SQLite alike. Naive input raises."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> Any:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime; use timezone-aware UTC")
        return value.astimezone(UTC)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def make_engine(url: str) -> Engine:
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return create_engine(url)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def import_all_models() -> None:
    """Import every model module so Base.metadata is complete."""
    import app.ai.usage
    import app.orders.models
    import app.proof.models
    import app.worker.models  # noqa: F401


def ensure_indexes(engine: Engine) -> None:
    """Create every model index the database lacks.

    create_all never alters a table that already exists, so an index added to a model later would
    never reach a database made before it. Tables are small, so each build takes milliseconds. The
    web service and the worker may start together: a lost race to create one index is logged.
    """
    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            try:
                index.create(bind=engine, checkfirst=True)
            except SQLAlchemyError:
                logging.getLogger(__name__).warning("index %s not created", index.name)
