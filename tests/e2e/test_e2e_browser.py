"""E2E-01 in a real browser: runs the Playwright suite (web/e2e) against a running server.

Skipped unless E2E_BASE_URL is set (`make e2e` sets it). The suite covers the scratch path:
"Not here" -> seller approves -> receipt chain verifies; packing check; agent stops at the
limit. The real-PayPal half (checkout and sandbox refund id) is E2E-03, run by hand."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"


@pytest.mark.skipif(not os.environ.get("E2E_BASE_URL"), reason="set E2E_BASE_URL to a server")
@pytest.mark.skipif(shutil.which("npx") is None, reason="Node.js is not installed")
def test_E2E_01_browser_journey_passes():
    npx = shutil.which("npx")
    assert npx is not None
    result = subprocess.run(
        [npx, "playwright", "test", "--reporter=line"],
        cwd=WEB,
        env={**os.environ},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
