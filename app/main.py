from __future__ import annotations

from fastapi import FastAPI

from app.api.deps import AppDeps
from app.api.webhooks import build_router
from app.policy.rules import load_rules


def create_app(deps: AppDeps) -> FastAPI:
    """Loads and validates rules/*.yaml first; an invalid rule file stops startup (RL-01)."""
    rulebook = load_rules(deps.rules_dir)  # raises RuleValidationError before anything else
    app = FastAPI(title="Doorstep", version="0.1.0")
    app.state.deps = deps
    app.state.rulebook = rulebook
    app.include_router(build_router(deps))

    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        return {"ok": True, "rules": sorted(rulebook.rule_ids)}

    return app
