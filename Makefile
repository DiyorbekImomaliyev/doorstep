.PHONY: setup lint test test-explore mutmut mutmut-gate test-integration trace locked evals web-build dev demo e2e fuzz smoke

setup:
	uv sync

lint:
	uv run ruff check app tests evals scripts tuning
	uv run pyright
	uv run lint-imports

# The official gate: unit + property tests, offline and deterministic. A fixed Hypothesis seed
# means the same examples every run, so the gate never depends on luck (ADR-020).
HYPOTHESIS_SEED ?= 20261104

test:
	uv run pytest tests/unit tests/property -q --hypothesis-seed=$(HYPOTHESIS_SEED)

# Exploration, not a gate: the property suite with fresh random seeds, EXPLORE_RUNS times
# (each run uses the test's own max_examples). A failure prints the seed to replay with
# `make test HYPOTHESIS_SEED=<seed>`; then pin it as an @example in a reviewed test.
EXPLORE_RUNS ?= 20

test-explore:
	@for i in $$(seq 1 $(EXPLORE_RUNS)); do \
		seed=$$(od -An -N4 -tu4 /dev/urandom | tr -d ' '); \
		echo "explore run $$i/$(EXPLORE_RUNS): --hypothesis-seed=$$seed"; \
		uv run pytest tests/property -q --hypothesis-seed=$$seed || { echo "FAILED with seed $$seed"; exit 1; }; \
	done

# Mutation testing on app/policy (config in pyproject [tool.mutmut]).
# OBJC_DISABLE_INITIALIZE_FORK_SAFETY: macOS aborts forked workers that touch the ObjC runtime.
mutmut:
	OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES uv run mutmut run
	uv run mutmut results

# Gate (ADR-023): zero surviving logic mutants in the money path; text-only survivors allowed.
# Starts clean: mutmut's cached test map misses tests added since the last run.
mutmut-gate:
	rm -rf mutants
	OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES uv run mutmut run
	uv run python scripts/mutmut_gate.py

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

# ---------------------------------------------------------------- app

# Local run: API (serves web/dist when built) + worker + Vite dev server with hot reload.
# Reads .env if present; without keys, checkout says 503 and explanations use templates.
ENV_FILE := $(if $(wildcard .env),--env-file .env,)

web-build:
	cd web && npm ci && npm run build

dev:
	@trap 'kill 0' INT TERM EXIT; \
	uv run uvicorn app.asgi:app --reload --port 8000 $(ENV_FILE) & \
	uv run $(if $(wildcard .env),--env-file .env,) python -m app.worker.main & \
	(cd web && npm run dev) & \
	wait

# E2E-02: seed the filmed Sam and Maria scenario and print its private links.
demo:
	PYTHONPATH=. uv run $(if $(wildcard .env),--env-file .env,) python scripts/seed_demo.py

# E2E-01 in a real browser against a running server (default http://localhost:8000).
E2E_BASE_URL ?= http://localhost:8000
e2e:
	cd web && E2E_BASE_URL=$(E2E_BASE_URL) npx playwright test

# SE-04: Schemathesis against the in-process OpenAPI schema (no server needed).
fuzz:
	uv run pytest tests/fuzz -q -p no:randomly --hypothesis-seed=$(HYPOTHESIS_SEED)

# E2E-03: real sandbox loop (capture -> tracking -> "Not here" -> policy refund -> receipts).
# If card payments are not enabled on the sandbox app, it prints a PayPal link to approve as
# the sandbox buyer, then finishes on its own.
smoke:
	uv run $(if $(wildcard .env),--env-file .env,) python scripts/sandbox_smoke.py
