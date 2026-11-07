"""RG-04: the rule set's buyer-rights text sits in the policy reader's corpus, next to the
seller's own words, so the law is retrieved when a shop policy says otherwise."""

from __future__ import annotations

import pytest

from app.ai.policy_reader import corpus, retrieve
from app.policy.rules import load_rules
from app.runtime import ROOT

SHOP = (
    "We refund or reship any order that does not reach you. Once tracking shows your parcel as "
    "delivered, it is your responsibility."
)


@pytest.fixture(scope="module")
def rules():
    return load_rules(ROOT / "rules")


@pytest.mark.parametrize("country", ["GB", "DE", "US"])
def test_RG_04_rights_text_is_in_the_corpus_under_the_rule_id(rules, country):
    rule = rules.select(country)
    assert rule is not None and rule.buyer_rights
    texts = " ".join(p.text for p in corpus("", rule) if p.id.startswith(f"{rule.id}:"))
    assert rule.buyer_rights.split(".")[0].strip() in texts


def test_RG_04_a_uk_shop_policy_that_differs_from_the_law_retrieves_the_law(rules):
    uk = rules.select("GB")
    hits = retrieve("Who is responsible once tracking shows delivered?", corpus(SHOP, uk))
    assert "seller:2" in [h.id for h in hits]
    assert any(h.id.startswith(f"{uk.id}:") and "section 29" in h.text for h in hits)


def test_RG_04_ids_stay_unique_when_a_rule_has_policy_and_rights_text(rules):
    us = rules.select("US")
    ids = [p.id for p in corpus(SHOP, us)]
    assert len(ids) == len(set(ids))
    assert us.policy_text and us.buyer_rights
