"""ASGI entry point: `uvicorn app.asgi:app`. Serves the API and, when built, the web app."""

from __future__ import annotations

import logging

from app.main import create_app
from app.runtime import build_runtime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

runtime = build_runtime()
app = create_app(runtime.deps)
