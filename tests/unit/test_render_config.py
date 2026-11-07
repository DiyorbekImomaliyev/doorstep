"""On Render, the app refuses to start unless APP_ENV=production.

APP_ENV defaults to development for local work. In development, /demo/reset deletes every table and
signing falls back to a public constant. Render sets RENDER=true in every service, so one missing
environment row must never quietly turn the live app into a development app.
"""

from __future__ import annotations

import pytest

from app.runtime import ConfigError, build_runtime


@pytest.fixture
def on_render(monkeypatch, tmp_path):
    for key in (
        "PAYPAL_CLIENT_ID",
        "PAYPAL_CLIENT_SECRET",
        "SIGNING_SECRET",
        "RECEIPT_SIGNING_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'r.db'}")
    monkeypatch.setenv("RENDER", "true")
    return monkeypatch


@pytest.mark.parametrize("app_env", [None, "development", "staging", ""])
def test_render_refuses_anything_but_production(on_render, app_env):
    if app_env is None:
        on_render.delenv("APP_ENV", raising=False)
    else:
        on_render.setenv("APP_ENV", app_env)
    with pytest.raises(ConfigError, match="APP_ENV=production"):
        build_runtime()


def test_production_on_render_still_needs_its_secrets(on_render):
    on_render.setenv("APP_ENV", "production")
    with pytest.raises(ConfigError, match="SIGNING_SECRET"):
        build_runtime()


def test_local_development_is_unchanged(monkeypatch, tmp_path):
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'l.db'}")
    assert build_runtime().services.environment == "development"
