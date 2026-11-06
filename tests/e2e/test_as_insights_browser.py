"""AS-02..07 in a real browser: the Insights reports on AG Studio (web/e2e/insights.spec.ts).

Skipped unless E2E_BASE_URL is set (`make e2e` sets it), like E2E-01. Each test runs the
Playwright tests named with its criterion ID against a scratch demo shop.
AS-01 (the tables Studio loads) is tested without a browser in tests/unit."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"

pytestmark = [
    pytest.mark.skipif(not os.environ.get("E2E_BASE_URL"), reason="set E2E_BASE_URL to a server"),
    pytest.mark.skipif(shutil.which("npx") is None, reason="Node.js is not installed"),
]


def _playwright(criterion: str, spec: str = "e2e/insights.spec.ts") -> None:
    npx = shutil.which("npx")
    assert npx is not None
    result = subprocess.run(
        [npx, "playwright", "test", spec, "--grep", criterion, "--reporter=line"],
        cwd=WEB,
        env={**os.environ},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
    assert "passed" in result.stdout, result.stdout[-1000:]


def test_AS_02_measures_and_quarter_filter_in_the_browser():
    _playwright("AS-02")


def test_AS_03_doorstep_theme_in_the_browser():
    _playwright("AS-03")


def test_AS_04_custom_widgets_in_the_browser():
    _playwright("AS-04")


def test_AS_05_reports_from_state_and_drill_through_in_the_browser():
    _playwright("AS-05")


def test_AS_06_13_ask_doorstep_builds_a_matching_studio_widget_in_the_browser():
    _playwright("AS-06")


def test_AS_07_case_queue_approve_and_masked_addresses_in_the_browser():
    _playwright("AS-07", "e2e/caseQueue.spec.ts")
