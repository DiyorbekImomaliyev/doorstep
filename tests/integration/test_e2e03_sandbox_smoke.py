"""E2E-03: the live sandbox smoke runs the full loop against real PayPal and Shippo.

It writes to the PayPal sandbox (an order, a capture, a refund), so it never runs by accident:
set RUN_SANDBOX_SMOKE=1 and the sandbox keys (`RUN_SANDBOX_SMOKE=1 make test-integration`, or
`make smoke` directly). Sandbox and test keys only; it uses its own SQLite file, never the demo.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
KEYS = ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "SHIPPO_API_KEY")

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("RUN_SANDBOX_SMOKE") != "1", reason="set RUN_SANDBOX_SMOKE=1"
    ),
    pytest.mark.skipif(not all(os.environ.get(k) for k in KEYS), reason="needs the sandbox keys"),
    pytest.mark.skipif(
        "sandbox" not in os.environ.get("PAYPAL_BASE_URL", "sandbox"), reason="sandbox only"
    ),
]


def test_E2E_03_01_live_sandbox_smoke_full_loop():
    run = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sandbox_smoke.py")],
        cwd=ROOT,
        env={**os.environ},
        capture_output=True,
        text=True,
        timeout=300,
    )
    out = run.stdout
    assert run.returncode == 0, out[-3000:] + run.stderr[-2000:]
    assert re.search(r"captured: \w+ \(COMPLETED\)", out), out
    assert "order is DELIVERED" in out, out
    assert re.search(r"Refund completed: PayPal refund id [A-Z0-9]{17}", out), out
    assert "Receipt chain: ok" in out and out.rstrip().endswith("SMOKE OK"), out
