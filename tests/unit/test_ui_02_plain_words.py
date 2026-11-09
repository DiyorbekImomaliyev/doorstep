"""UI-02: the seller reads the law and the case in plain words, not rule ids or state names.

Found by the Nov 7 review: the drawer showed `uk_possession_v1 · 2026-11-06` in monospace and the
timeline said "Case hold for review".
"""

from __future__ import annotations

import pytest

from tests.unit.test_decide_again import api  # noqa: F401  (fixture)


@pytest.fixture
def demo(api):  # noqa: F811
    return api.post("/demo/seed?scratch=1").json()


def _case_page(api, demo, key):  # noqa: F811
    from tests.unit.test_api_journey import _by_key

    case_id = _by_key(demo, key)["case_id"]
    return api.get(f"/api/seller/{demo['seller_token']}/cases/{case_id}").json()


def test_UI_02_the_decision_names_the_law_it_applied(api, demo):  # noqa: F811
    page = _case_page(api, demo, "empty-box")
    assert page["decision"]["rule_id"] == "uk_possession_v1"
    assert page["decision"]["rule_name"] == "Consumer Rights Act 2015 s.29"


def test_UI_02_the_timeline_never_shows_a_state_name(api, demo):  # noqa: F811
    titles = [item["title"] for item in _case_page(api, demo, "empty-box")["timeline"]]
    assert "Held for a person to check" in titles
    assert not [t for t in titles if t.startswith("Case ") or "_" in t]


@pytest.mark.parametrize(
    ("source", "label"),
    [
        (
            "Consumer Rights Act 2015 s.29 — https://www.legislation.gov.uk/ukpga/2015/15/section/29",
            "Consumer Rights Act 2015 s.29",
        ),
        (
            "Directive 2011/83/EU Art. 20, via ECC-Net — https://www.eccnet.eu/x",
            "Directive 2011/83/EU Art. 20",
        ),
        (
            "Seller written delivery policy (demo seller defaults); the US has no single rule",
            "Seller written delivery policy",
        ),
    ],
)
def test_UI_02_rule_labels_fit_in_a_sentence(source, label):
    from app.api.views import rule_label

    assert rule_label(source) == label
