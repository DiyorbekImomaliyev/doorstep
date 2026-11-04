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
    strict=True, reason="RAG policy reader not built (SPEC 10; template quotes rules)"
)
def test_RG_01_answers_cite_the_retrieved_passages():
    _unbuilt()


@pytest.mark.xfail(strict=True, reason="RAG policy reader not built")
def test_RG_02_no_passage_quotes_the_rule_file():
    _unbuilt()


@pytest.mark.xfail(strict=True, reason="RAG policy reader and Ragas set not built")
def test_RG_03_ragas_faithfulness():
    _unbuilt()


_AS = NEEDS_HUMAN + "AG Studio licence; Control Room runs on AG Grid Community today"


@pytest.mark.xfail(strict=True, reason=_AS)
def test_AS_01_studio_loads_doorstep_sources():
    _unbuilt()


@pytest.mark.xfail(strict=True, reason=_AS)
def test_AS_02_studio_expressions_and_calendar():
    _unbuilt()


@pytest.mark.xfail(strict=True, reason=_AS)
def test_AS_03_doorstep_studio_theme():
    _unbuilt()


@pytest.mark.xfail(strict=True, reason=_AS)
def test_AS_04_two_custom_studio_widgets():
    _unbuilt()


@pytest.mark.xfail(strict=True, reason=_AS)
def test_AS_05_prebuilt_studio_reports():
    _unbuilt()


@pytest.mark.xfail(strict=True, reason=_AS)
def test_AS_06_doorstep_agent_in_studio():
    _unbuilt()


@pytest.mark.xfail(
    strict=True, reason="AG Grid case queue exists; Studio-hosted queue waits on AS-01"
)
def test_AS_07_studio_case_queue_approve_calls_policy():
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


@pytest.mark.xfail(strict=True, reason=NEEDS_HUMAN + "sandbox keys, Shippo test key and a run")
def test_E2E_03_live_sandbox_smoke_full_loop():
    _unbuilt()
