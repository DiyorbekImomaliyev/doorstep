"""RC-03: Royal Mail Tracked 24/48 cover is £75 since its 6 October 2025 price change.

Royal Mail halved the cover from £150 (Citizens Advice, seller reports). A claim pack that asked
for more than the carrier pays would be cut back, so the claim is capped at the current cover.
"""

from __future__ import annotations

from app.money import Currency


def test_RC_03_royal_mail_tracked_cover_is_75_pounds(rulebook):
    entry = rulebook.carriers.data.carriers["royal_mail"]
    assert entry.standard_cover_minor == {"GBP": 7_500}
    assert "Citizens Advice" in entry.source
    assert rulebook.carriers.rule_version >= "2026-11-09"


def test_RC_03_a_claim_is_capped_at_the_cover(rulebook, clock):
    common = dict(currency=Currency.GBP, damage=False, posted_at=clock.now(), delivered_at=None)
    big = rulebook.carriers.claim(carrier="royal_mail", item_minor=12_000, **common)
    assert big.cover_minor == 7_500 and big.claim_minor == 7_500
    small = rulebook.carriers.claim(carrier="royal_mail", item_minor=4_600, **common)
    assert small.claim_minor == 4_600  # under the cover: the item's value
