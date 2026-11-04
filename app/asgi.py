"""ASGI entry point: `uvicorn app.asgi:app`. Serves the API and, when built, the web app.

EMBEDDED_WORKER=1 also runs the worker loop in a background thread of this process, so a
one-service deploy (e.g. a free Render instance) still processes webhooks and jobs. With a
separate worker service, leave it unset.
"""

from __future__ import annotations

import logging
import threading

from app.main import create_app
from app.runtime import build_runtime, env

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

runtime = build_runtime()
app = create_app(runtime.deps)

if env("EMBEDDED_WORKER") == "1":  # pragma: no cover - process wiring
    from app.worker.main import run_loop

    _stop = threading.Event()
    threading.Thread(
        target=run_loop, args=(runtime, _stop.is_set), name="doorstep-worker", daemon=True
    ).start()
