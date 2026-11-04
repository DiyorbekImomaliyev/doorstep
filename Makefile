.PHONY: setup lint test test-integration trace locked evals

setup:
	uv sync

lint:
	uv run ruff check app tests evals scripts tuning
	uv run pyright
	uv run lint-imports

# Unit + property tests (offline, fast).
test:
	uv run pytest tests/unit tests/property -q

# Recorded sandbox replays + Postgres-only tests. Postgres tests skip unless DATABASE_URL points
# at Postgres; CI sets REQUIRE_PG=1 so they cannot be skipped there.
test-integration:
	uv run pytest tests/integration -q

trace:
	uv run python scripts/trace.py

locked:
	scripts/check_tests_locked.sh

evals:
	uv run pytest evals -q -m eval
