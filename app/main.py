from __future__ import annotations

from fastapi import FastAPI

from app.api.deps import AppDeps


def create_app(deps: AppDeps) -> FastAPI:
    """Loads and validates rules/*.yaml first; an invalid rule file stops startup (RL-01)."""
    raise NotImplementedError
