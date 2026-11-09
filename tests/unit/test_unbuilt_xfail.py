"""Requirements that wait on an outside input (photos, a licence, a live run), as strict xfails.

Each test fails on purpose until the feature lands; then it is replaced by a real test."""

from __future__ import annotations

import pytest

NEEDS_HUMAN = "needs the human: "


def _unbuilt() -> None:
    raise NotImplementedError


@pytest.mark.xfail(strict=True, reason=NEEDS_HUMAN + "a labelled packing-photo set (40 photos)")
def test_PK_05_mismatch_recall_on_the_labelled_photo_set():
    _unbuilt()


# RG-03 is built and run: evals/rag/test_rg03_ragas.py replays the recorded live run (30 of 30
# questions, faithfulness 0.908 against 0.90); tests/unit/test_rg03_ragas_keyless.py needs no key.


# AS-01..05 are built: tests/unit/test_as01_insights_sources.py (the tables) and
# tests/e2e/test_as_insights_browser.py (the reports, in a browser).


# AS-06 Ask Doorstep is built (tests/unit/test_as06_insights_agent.py and the browser test).
# AS-06-09 is built: the Studio licence is Pro with AI, and the Doorstep agent delegates to
# Studio's built-in widget and page agents (tests/unit/test_as07_studio_agent.py and AS-07 in
# web/e2e/insights.spec.ts).


# SB-01 and SB-02 are done: tests/integration/test_sb_live_sandbox_refund.py re-checks the live
# refund of Nov 6, 2026 against PayPal (RUN_SANDBOX_CHECKS=1).


@pytest.mark.xfail(strict=True, reason=NEEDS_HUMAN + "/paypal:doctor on the final commit")
def test_SB_03_paypal_doctor_clean_on_final_commit():
    _unbuilt()


# BR-01..04 (a Bryntum deadline view) left the scope: claim and dispute deadlines show in the
# case panel and the grid.

# SE-03 is built: tests/unit/test_se03_redteam_keyless.py runs all 64 probes against models that
# obey every attack; evals/redteam/test_se03_redteam.py runs them on the live model.

# E2E-03 is built: tests/integration/test_e2e03_sandbox_smoke.py (RUN_SANDBOX_SMOKE=1).
