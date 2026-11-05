"""PR-02 (ADR-040): concurrent receipt writers wait for each other instead of erroring.

The unique `seq` alone stopped a fork but made the second writer fail; Playwright seeding several
demo shops at once hit it on Postgres. Appends now take one transaction-scoped advisory lock.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.proof.receipts import CHAIN_LOCK_KEY, _lock_chain


class _Session:
    def __init__(self, dialect: str) -> None:
        self.dialect = dialect
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get_bind(self) -> Any:
        return SimpleNamespace(dialect=SimpleNamespace(name=self.dialect))

    def execute(self, statement: Any, params: dict[str, Any]) -> None:
        self.calls.append((str(statement), params))


def test_PR_02_postgres_appends_take_the_chain_lock():
    session = _Session("postgresql")
    _lock_chain(session)  # type: ignore[arg-type]
    assert session.calls == [("SELECT pg_advisory_xact_lock(:key)", {"key": CHAIN_LOCK_KEY})]


def test_PR_02_sqlite_needs_no_lock():
    session = _Session("sqlite")
    _lock_chain(session)  # type: ignore[arg-type]
    assert session.calls == []
