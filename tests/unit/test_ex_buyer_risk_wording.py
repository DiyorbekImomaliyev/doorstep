"""EX-02 EX-03: when the rule puts the risk on the buyer, the template does not recommend a refund.

Found in the v2 UI review (Nov 5): a US case with proven possession read "The buyer is responsible
under this rule. Recommended fix: refund $89.00." Those two sentences contradict each other. The
rule sets no fix, and the seller may still choose a goodwill refund. The wording names where the
risk sits and does not blame the buyer (hard rule 7).
"""

from __future__ import annotations

from app.ai.explainer import ExplainContext, template
from app.cases.engine import FailurePoint
from app.money import Currency, Money
from app.orders.enums import CaseState


def _ctx(**overrides) -> ExplainContext:
    base = dict(
        order_ref="ORD12345",
        failure_point=FailurePoint.STOLEN_AFTER_DELIVERY,
        confidence="high",
        route=CaseState.DISPUTE_EVIDENCE,
        rule_id="us_seller_policy_v1",
        rule_version="2026-11-06",
        liable_party="buyer",
        refund=Money(amount_minor=8900, currency=Currency.USD),
        reasons=["proof of possession (seal scan)"],
    )
    base.update(overrides)
    return ExplainContext(**base)


def test_EX_03_buyer_risk_sets_no_fix_and_leaves_the_refund_to_the_seller():
    text = template(_ctx()).summary
    assert "Recommended fix: refund" not in text
    assert "Under this rule the risk sits with the buyer." in text
    assert "Recommended fix: none set by the rule; the seller may still refund $89.00." in text


def test_EX_02_buyer_risk_wording_never_blames_the_buyer():
    text = template(_ctx()).summary.lower()
    assert "buyer is responsible" not in text
    assert "buyer is liable" not in text


def test_EX_03_seller_risk_keeps_the_refund_fix():
    text = template(_ctx(liable_party="seller", route=CaseState.AWAITING_SELLER)).summary
    assert "Under this rule the risk sits with the seller." in text
    assert "Recommended fix: refund $89.00." in text


def test_EX_03_buyer_risk_with_a_reship_still_names_the_reship():
    text = template(_ctx(reship=True, refund=None)).summary
    assert "Recommended fix: reship the order." in text


def test_EX_03_buyer_risk_without_an_amount_leaves_the_fix_to_the_seller():
    text = template(_ctx(refund=None)).summary
    assert "Recommended fix: none set by the rule; the seller decides." in text
