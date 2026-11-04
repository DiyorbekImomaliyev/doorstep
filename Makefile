.PHONY: setup lint test test-explore mutmut mutmut-gate test-integration trace locked evals

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
mutmut-gate:
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
