"""SE-02: redaction removes personal data, never the facts a claim or an explanation cites.

Found by the Nov 7 review: the phone pattern ate ISO dates and long tracking numbers, so the model
never saw "2026-11-06 07:15" and the claim-pack checker then dropped every sentence citing a date.
"""

from __future__ import annotations

import pytest

from app.ai.llm import redact


@pytest.mark.parametrize(
    "text",
    [
        "Delivered 2026-11-06 07:15 UTC at the door.",
        "File by 2026-12-01 under the carrier's terms.",
        "Scanned at 2026-11-03T00:16:00 in BS1.",
        "Scanned at 2026-11-03 00:16:59.",
        "Tracking 9400 1000 0000 0000 0000 00 shows delivered.",
        "Tracking 9400100000000000000000 shows delivered.",
        "Order ORD12345 for £60.00 on 2026-11-06.",
    ],
)
def test_SE_02_dates_times_and_tracking_numbers_survive(text):
    assert redact(text) == text


@pytest.mark.parametrize(
    ("text", "phone"),
    [
        ("Call +44 7700 900123 after six.", "7700"),
        ("Call 07700 900123 after six.", "900123"),
        ("Call 020 7946 0958 please.", "7946"),
        ("Call (555) 123-4567 please.", "123-4567"),
        ("Call +1 555-123-4567 please.", "555-123"),
    ],
)
def test_SE_02_phone_numbers_are_still_removed(text, phone):
    out = redact(text)
    assert phone not in out and "[phone]" in out


def test_SE_02_a_date_next_to_a_phone_keeps_the_date_and_drops_the_phone():
    out = redact("Delivered 2026-11-06 07:15; buyer phone 07700 900123.")
    assert out == "Delivered 2026-11-06 07:15; buyer phone [phone]."
