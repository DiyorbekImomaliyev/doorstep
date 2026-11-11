from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from app.api.deps import AppDeps
from app.api.webhooks import build_router
from app.policy.rules import load_rules

WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
# First path segments that belong to the API. A GET under one of them that no route matched is a
# 404, never the page: an API client must not get HTML with 200 for a mistyped path.
API_PREFIXES = frozenset({".well-known", "api", "cases", "demo", "healthz", "mcp", "webhooks"})
IMMUTABLE = "public, max-age=31536000, immutable"


def _mount_web(app: FastAPI) -> None:
    """Serve the built React app (web/dist) with a single-page fallback. API routes win.

    Caching: Vite names every asset by its content hash, so an asset never changes under its
    name and can be cached for a year. The page is `no-cache`, so a new deploy shows at once.
    """
    index = WEB_DIST / "index.html"
    if not index.exists():
        return
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    from starlette.responses import Response
    from starlette.types import Scope

    class HashedAssets(StaticFiles):
        async def get_response(self, path: str, scope: Scope) -> Response:
            response = await super().get_response(path, scope)
            if response.status_code in (200, 304):
                response.headers["Cache-Control"] = IMMUTABLE
            return response

    app.mount("/assets", HashedAssets(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.split("/", 1)[0] in API_PREFIXES:
            raise HTTPException(404, "not found")
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


def create_app(deps: AppDeps) -> FastAPI:
    """Loads and validates rules/*.yaml first; an invalid rule file stops startup (RL-01)."""
    rulebook = load_rules(deps.rules_dir)  # raises RuleValidationError before anything else
    app = FastAPI(title="Doorstep", version="0.1.0")
    app.state.deps = deps
    app.state.rulebook = rulebook
    app.include_router(build_router(deps))
    if deps.services is not None:
        from fastapi import Request
        from fastapi.responses import JSONResponse

        from app.api.deps import ServiceUnavailable
        from app.api.journey import build_journey_router
        from app.paypal_client.errors import PayPalAuthError, PayPalServerError

        @app.exception_handler(ServiceUnavailable)
        def _unavailable(request: Request, exc: ServiceUnavailable) -> JSONResponse:
            return JSONResponse({"detail": str(exc)}, status_code=503)

        @app.exception_handler(PayPalServerError)
        @app.exception_handler(PayPalAuthError)
        def _paypal_down(request: Request, exc: Exception) -> JSONResponse:
            return JSONResponse(
                {"detail": "PayPal is not reachable right now; nothing was charged. Try again."},
                status_code=503,
            )

        app.include_router(build_journey_router(deps, deps.services, rulebook))
        from app.api.autopilot import build_autopilot_router
        from app.api.ledger import build_ledger_router
        from app.api.policy_check import build_policy_check_router
        from app.api.proof import build_proof_router

        app.include_router(build_ledger_router(deps, deps.services))
        app.include_router(build_autopilot_router(deps, deps.services, rulebook))
        app.include_router(build_policy_check_router(deps, deps.services, rulebook))
        app.include_router(build_proof_router(deps, deps.services))
        _mount_web(app)
    else:

        @app.get("/healthz")
        def healthz() -> dict[str, object]:
            return {"ok": True, "rules": sorted(rulebook.rule_ids)}

    return app
