"""RC-02 mutation gaps in app/policy/carriers.py (mutmut gate, Nov 5).

Each test pins one behaviour a surviving mutant changed: separator trimming, the loss window
versus the damage window, the duplicate-alias error, and the file's disclaimer.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import yaml

from app.money import Currency
from app.policy.carriers import load_carrier_terms, normalise
from app.policy.rules import RuleValidationError
from app.runtime import ROOT

CARRIER_FILE = ("carriers", "carrier_claims_v1.yaml")
POSTED = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("-Royal Mail-", "royal_mail"), ("__usps__", "usps"), (" FedEx/Express ", "fedex_express")],
)
def test_RC_02_normalise_trims_separators_at_both_ends(raw, expected):
    assert normalise(raw) == expected


def test_RC_02_loss_and_damage_use_their_own_windows():
    terms = load_carrier_terms(ROOT / "rules")
    common = {
        "carrier": "fedex",
        "currency": Currency.USD,
        "item_minor": 5000,
        "posted_at": POSTED,
        "delivered_at": None,
    }
    lost = terms.claim(damage=False, **common)
    damaged = terms.claim(damage=True, **common)
    assert (lost.window_days, damaged.window_days) == (270, 60)
    assert lost.deadline_at == POSTED + timedelta(days=270)
    assert damaged.deadline_at == POSTED + timedelta(days=60)


def test_RC_02_an_alias_naming_two_carriers_says_which(rules_copy):
    path = rules_copy.joinpath(*CARRIER_FILE)
    data = yaml.safe_load(path.read_text())
    data["carriers"]["evri"]["aliases"].append("royalmail")
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(RuleValidationError, match="'royalmail' names both royal_mail and evri"):
        load_carrier_terms(rules_copy)


def test_RC_02_the_files_disclaimer_is_kept():
    terms = load_carrier_terms(ROOT / "rules")
    raw = yaml.safe_load(ROOT.joinpath("rules", *CARRIER_FILE).read_text())
    assert terms.disclaimer == raw["disclaimer"] and terms.disclaimer
