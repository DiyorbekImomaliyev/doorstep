"""AQ-03: the claim pack asks the carrier to pay, never by the seller's filing deadline, and
writes carrier status codes in plain words."""

from __future__ import annotations

from app.ai import recovery


def test_AQ_03_claim_pack_prompt_asks_for_payment_without_a_payment_deadline() -> None:
    text = recovery._SYSTEM
    assert "pay the claim amount by the stated deadline" not in text
    assert "not a date for the carrier to pay by" in text
    assert "plain words" in text
