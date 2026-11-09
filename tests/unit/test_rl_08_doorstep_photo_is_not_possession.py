"""RL-08: in the UK and the EU a carrier's doorstep photo is not physical possession.

From the Nov 7 legal review (judge 7, W5). CRA 2015 s.29 keeps the goods at the trader's risk until
they "come into the physical possession" of the consumer or a person they identified; Directive
2011/83/EU Art. 20 says the same. A photo of a parcel left at the door shows where the carrier left
it, not that anyone holds it. Only a place the buyer chose could count, and Doorstep offers no
safe-place delivery, so the photo is never possession proof in these rules. It is still evidence
of where the parcel was left, so the likely cause (stolen after delivery) is unchanged.
"""

from __future__ import annotations

import pytest

from app.cases.counterfactual import counterfactual
from app.orders.enums import ProofKind as P


@pytest.mark.parametrize("country", ["GB", "FR"])
def test_RL_08_a_doorstep_photo_leaves_the_parcel_at_the_sellers_risk(rulebook, country):
    rule = rulebook.select(country)
    assert P.PHOTO_AT_BUYER_ADDRESS not in rule.possession_proof
    outcome = rule.evaluate(frozenset({P.DELIVERED_SCAN, P.PHOTO_AT_BUYER_ADDRESS}))
    assert outcome.possession_proven is False and outcome.liable_party == "seller"
    assert rule.version >= "2026-11-09"


@pytest.mark.parametrize("country", ["GB", "FR"])
def test_RL_08_what_would_have_changed_it_no_longer_offers_a_photo(rulebook, country):
    cf = counterfactual(rulebook.select(country), frozenset({P.DELIVERED_SCAN}))
    assert P.PHOTO_AT_BUYER_ADDRESS not in cf.would_change
    assert "photo" not in cf.text
