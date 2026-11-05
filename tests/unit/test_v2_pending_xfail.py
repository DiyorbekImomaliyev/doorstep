"""v2 acceptance criteria not built yet (CLAUDE.md: unbuilt criteria stay xfail with a reason).

Each entry is deleted when its real test lands. Strict: an xfail that passes fails the suite."""

from __future__ import annotations

import pytest


@pytest.mark.xfail(strict=True, reason="CF-01: counterfactual not built yet")
def test_CF_01_pending():
    raise AssertionError("not built")


@pytest.mark.xfail(strict=True, reason="CF-02: prevention suggestions not built yet")
def test_CF_02_pending():
    raise AssertionError("not built")
