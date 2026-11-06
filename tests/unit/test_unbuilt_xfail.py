"""Acceptance criteria not built yet (CLAUDE.md: unbuilt criteria stay as xfail with a reason).

Each test fails on purpose until the feature lands; then it is replaced by a real test."""

from __future__ import annotations

import pytest

NEEDS_HUMAN = "needs the human: "


def _unbuilt() -> None:
    raise NotImplementedError


@pytest.mark.xfail(strict=True, reason=NEEDS_HUMAN + "a labelled packing-photo set (40 photos)")
def test_PK_05_mismatch_recall_on_the_labelled_photo_set():
    _unbuilt()


@pytest.mark.xfail(
    strict=True, reason=NEEDS_HUMAN + "a Ragas run (30 questions) on the eval model key"
)
def test_RG_03_ragas_faithfulness():
    _unbuilt()


# AS-01..05 are built: tests/unit/test_as01_insights_sources.py (the tables) and
# tests/e2e/test_as_insights_browser.py (the reports, in a browser).


# AS-06 Ask Doorstep is built (tests/unit/test_as06_insights_agent.py and the browser test).
# Studio's own AI panel and built-in agents need the "Pro with AI" licence; the key is Pro.
@pytest.mark.xfail(
    strict=True, reason=NEEDS_HUMAN + "an AG Studio Pro-with-AI licence for the built-in agents"
)
def test_AS_06_09_works_beside_studio_built_in_agents():
    _unbuilt()


_SB = NEEDS_HUMAN + "a live sandbox run with PAYPAL_WEBHOOK_ID and a public URL"


@pytest.mark.xfail(strict=True, reason=_SB)
def test_SB_01_refund_visible_in_merchant_and_buyer_sandbox():
    _unbuilt()


@pytest.mark.xfail(strict=True, reason=_SB)
def test_SB_02_webhook_delivered_in_sandbox_event_logs():
    _unbuilt()


@pytest.mark.xfail(strict=True, reason=NEEDS_HUMAN + "/paypal:doctor on the final commit")
def test_SB_03_paypal_doctor_clean_on_final_commit():
    _unbuilt()


_BR = "Bryntum deadline radar is first to cut (SPEC 16); not built"


@pytest.mark.xfail(strict=True, reason=_BR)
def test_BR_01_rows_journey_bars_and_milestones():
    _unbuilt()


@pytest.mark.xfail(strict=True, reason=_BR)
def test_BR_02_deadlines_only_from_rules_or_disputes():
    _unbuilt()


@pytest.mark.xfail(strict=True, reason=_BR)
def test_BR_03_event_click_opens_the_order():
    _unbuilt()


@pytest.mark.xfail(strict=True, reason=_BR)
def test_BR_04_drag_redelivery_calls_api_and_audits():
    _unbuilt()


@pytest.mark.xfail(strict=True, reason="Promptfoo red-team suite not built (needs model keys)")
def test_SE_03_promptfoo_money_attacks_all_blocked():
    _unbuilt()


# E2E-03 is built: tests/integration/test_e2e03_sandbox_smoke.py (RUN_SANDBOX_SMOKE=1).
