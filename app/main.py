from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from app.api.deps import AppDeps
from app.api.webhooks import build_router
from app.policy.rules import load_rules

WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"


def _mount_web(app: FastAPI) -> None:
    """Serve the built React app (web/dist) with a single-page fallback. API routes win."""
    index = WEB_DIST / "index.html"
    if not index.exists():
        return
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        return FileResponse(index)


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
        _mount_web(app)
    else:

        @app.get("/healthz")
        def healthz() -> dict[str, object]:
            return {"ok": True, "rules": sorted(rulebook.rule_ids)}

    return app
