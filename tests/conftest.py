from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytest_httpserver import HTTPServer
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from werkzeug import Request, Response

from app.config import Settings
from app.db import Base, import_all_models, make_engine, make_session_factory
from tests.fakes import FakeClock, FakePayPal, FakeVerifier

import_all_models()

ROOT = Path(__file__).resolve().parent.parent
RULES_DIR = ROOT / "rules"
RULE_FILES = [
    "uk_possession_v1.yaml",
    "eu_possession_v1.yaml",
    "us_seller_policy_v1.yaml",
    "paypal_sp_v1.yaml",
    "money_limits_v1.yaml",
]


class IsolatedLogHTTPServer(HTTPServer):
    """Session-wide fake server whose request log belongs to one test at a time.

    A request is logged only if it arrived during the current test. A slow handler from an earlier
    test that finishes late (e.g. a client timeout test) never lands in the next test's log.
    """

    generation = 0

    def application(self, request: Request) -> Response:
        started_in = self.generation
        request.get_data()
        response = self.dispatch(request)
        if started_in == self.generation:
            self.log.append((request, response))
        return response


@pytest.fixture(scope="session")
def make_httpserver() -> Iterator[HTTPServer]:
    server = IsolatedLogHTTPServer(host=HTTPServer.DEFAULT_LISTEN_HOST, port=0)
    server.start()
    yield server
    server.clear()
    if server.is_running():
        server.stop()


@pytest.fixture
def httpserver(make_httpserver: IsolatedLogHTTPServer) -> Iterator[HTTPServer]:
    make_httpserver.generation += 1
    make_httpserver.clear()
    yield make_httpserver
    make_httpserver.generation += 1
    make_httpserver.clear()


def sqlite_session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    return sqlite_session_factory()


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = session_factory()
    yield session
    session.close()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        max_attempts=3,
        backoff_base_s=10,
        shippo_webhook_token="tok-123",
        max_webhook_body_bytes=10_000,
        paypal_timeout_s=0.5,
    )


@pytest.fixture
def fake_paypal() -> FakePayPal:
    return FakePayPal()


@pytest.fixture
def verifier() -> FakeVerifier:
    return FakeVerifier(result=True)


@pytest.fixture
def rulebook():
    from app.policy.rules import load_rules

    return load_rules(RULES_DIR)


@pytest.fixture
def rules_copy(tmp_path: Path) -> Path:
    """A writable copy of rules/ for tests that break a rule file on purpose."""
    target = tmp_path / "rules"
    shutil.copytree(RULES_DIR, target)
    return target


def _env_file_value(key: str) -> str:
    """Read one key from .env (only that key; nothing else is loaded)."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return ""
    for line in env_file.read_text().splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def pg_test_url() -> str:
    """TEST_DATABASE_URL, else DATABASE_URL (env or .env) with `_test` appended to the db name.
    Postgres tests drop every table, so they never touch the dev database."""
    explicit = os.environ.get("TEST_DATABASE_URL", "")
    if explicit:
        return explicit
    base = os.environ.get("DATABASE_URL", "") or _env_file_value("DATABASE_URL")
    if not base.startswith("postgresql"):
        return ""
    head, _, dbname = base.rpartition("/")
    dbname = dbname.split("?", 1)[0]
    return base if dbname.endswith("_test") else f"{head}/{dbname}_test"


@pytest.fixture
def pg_session_factory() -> Iterator[sessionmaker[Session]]:
    url = pg_test_url()
    if not url:
        if os.environ.get("REQUIRE_PG") == "1":
            pytest.fail("REQUIRE_PG=1 but no Postgres URL (TEST_DATABASE_URL or DATABASE_URL)")
        pytest.skip("needs Postgres: set DATABASE_URL=postgresql://... in .env")
    dbname = url.rpartition("/")[2].split("?", 1)[0]
    assert dbname.endswith("_test"), f"refusing to drop tables in non-test database {dbname!r}"
    engine = make_engine(url)
    try:
        with engine.connect():
            pass
    except Exception as exc:
        pytest.fail(f"Postgres configured but unreachable at {dbname!r}: {exc}")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    Base.metadata.drop_all(engine)
    engine.dispose()
