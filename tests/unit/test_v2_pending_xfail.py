"""v2 acceptance criteria not built yet (CLAUDE.md: unbuilt criteria stay xfail with a reason).

Each entry is deleted when its real test lands. Strict: an xfail that passes fails the suite."""

from __future__ import annotations

import pytest


@pytest.mark.xfail(strict=True, reason="RC-01: carrier recovery not built yet")
def test_RC_01_pending():
    raise AssertionError("not built")


@pytest.mark.xfail(strict=True, reason="RC-02: carrier terms file not built yet")
def test_RC_02_pending():
    raise AssertionError("not built")


@pytest.mark.xfail(strict=True, reason="RC-03: claim pack verifier not built yet")
def test_RC_03_pending():
    raise AssertionError("not built")


@pytest.mark.xfail(strict=True, reason="RC-04: recovery transitions not built yet")
def test_RC_04_pending():
    raise AssertionError("not built")


@pytest.mark.xfail(strict=True, reason="RC-05: seller recovery taps not built yet")
def test_RC_05_pending():
    raise AssertionError("not built")


@pytest.mark.xfail(strict=True, reason="RC-06: recovery expiry sweep not built yet")
def test_RC_06_pending():
    raise AssertionError("not built")


@pytest.mark.xfail(strict=True, reason="RC-07: healthz recovery wording not updated yet")
def test_RC_07_pending():
    raise AssertionError("not built")


@pytest.mark.xfail(strict=True, reason="DE-01: dispute evidence job not built yet")
def test_DE_01_pending():
    raise AssertionError("not built")


@pytest.mark.xfail(strict=True, reason="DE-02: dispute evidence notes not built yet")
def test_DE_02_pending():
    raise AssertionError("not built")


@pytest.mark.xfail(strict=True, reason="DE-03: dispute evidence idempotency not built yet")
def test_DE_03_pending():
    raise AssertionError("not built")


@pytest.mark.xfail(strict=True, reason="DE-04: dispute evidence receipt not built yet")
def test_DE_04_pending():
    raise AssertionError("not built")


@pytest.mark.xfail(strict=True, reason="CF-01: counterfactual not built yet")
def test_CF_01_pending():
    raise AssertionError("not built")


@pytest.mark.xfail(strict=True, reason="CF-02: prevention suggestions not built yet")
def test_CF_02_pending():
    raise AssertionError("not built")
