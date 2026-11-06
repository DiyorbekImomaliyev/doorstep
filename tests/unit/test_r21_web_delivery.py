"""R21: how the built frontend is served.

- Vite names each asset by its content hash, so assets are cached for a year (`immutable`). Before
  this, no Cache-Control was sent and every visit downloaded the scripts again (about 2 MB
  compressed for the Control Room and Insights).
- The page itself is `no-cache`, so a new deploy shows on the next load.
- A mistyped API path is a 404, not the HTML page with 200 (which hid bugs from API clients).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

IMMUTABLE = "public, max-age=31536000, immutable"


@pytest.fixture
def web(monkeypatch, tmp_path) -> TestClient:
    import app.main
    from app.main import create_app
    from app.runtime import build_runtime

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Doorstep</title>")
    (dist / "assets" / "index-AbC123.js").write_text("console.log(1)")
    monkeypatch.setattr(app.main, "WEB_DIST", dist)
    for key in (
        "PAYPAL_CLIENT_ID",
        "PAYPAL_CLIENT_SECRET",
        "SIGNING_SECRET",
        "RECEIPT_SIGNING_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'web.db'}")
    monkeypatch.setenv("APP_ENV", "development")
    return TestClient(create_app(build_runtime().deps))


def test_R21_hashed_assets_are_cached_for_a_year(web):
    response = web.get("/assets/index-AbC123.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == IMMUTABLE


def test_R21_a_missing_asset_is_a_404_and_never_cached(web):
    response = web.get("/assets/index-gone.js")
    assert response.status_code == 404
    assert response.headers.get("cache-control") != IMMUTABLE


@pytest.mark.parametrize("path", ["/", "/seller/token-1", "/t/token-2", "/verify/case-1", "/x"])
def test_R21_the_page_is_always_revalidated(web, path):
    response = web.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize(
    "path",
    [
        "/api/nope",
        "/api/seller/token/nope",
        "/cases/c1/nope",
        "/healthz/x",
        "/webhooks/x",
        "/demo/x",
        "/mcp/x",
    ],
)
def test_R21_a_mistyped_api_path_is_a_404_not_the_page(web, path):
    response = web.get(path)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_R21_real_api_routes_still_answer(web):
    response = web.get("/healthz")
    assert response.status_code == 200
    assert response.json()["ok"] is True
