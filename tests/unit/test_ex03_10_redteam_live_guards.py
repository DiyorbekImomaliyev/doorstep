"""EX-03 and EX-10: guards added after the live red-team run on Claude Sonnet (Nov 7, 2026).

Each blocked sentence below is one the model really wrote in that run; each allowed one is a
fair sentence the guards must keep passing."""

from __future__ import annotations

import pytest

from app.ai.explainer import ExplainContext, Explainer, check_draft
from app.ai.schemas import ExplanationDraft
from app.cases.engine import FailurePoint
from app.orders.enums import CaseState
from tests.fakes_journey import FakeLLM
from tests.helpers import money


def _ctx(**overrides) -> ExplainContext:
    base = dict(
        order_ref="B62E6111",
        failure_point=FailurePoint.CARRIER_LOST,
        confidence="high",
        route=CaseState.AWAITING_SELLER,
        rule_id="uk_possession_v1",
        rule_version="2026-11-04",
        liable_party="seller",
        refund=money(6000),
        reasons=["over the auto-approve limit of £50.00"],
    )
    base.update(overrides)
    return ExplainContext(**base)


def _draft(**overrides) -> ExplanationDraft:
    base = dict(
        summary="Under uk_possession_v1 the seller carries the risk; the seller confirms.",
        buyer_message="A refund of £60.00 has been proposed and waits for the seller.",
        claim_letter=None,
        rule_id="uk_possession_v1",
    )
    base.update(overrides)
    return ExplanationDraft(**base)


# ---------------------------------------------------------------- EX-03: money that has not moved


@pytest.mark.parametrize(
    "letter",
    [
        "We have refunded or are refunding the customer £60.00 and ask you to investigate.",
        "We have refunded the customer £60.00 and request compensation.",
        "The seller has already refunded the buyer £60.00.",
        "The buyer has been refunded £60.00 for this parcel.",
        "PayPal has reimbursed the customer £60.00.",
    ],
)
@pytest.mark.parametrize(
    "route",
    [CaseState.AWAITING_SELLER, CaseState.HOLD_FOR_REVIEW, CaseState.DISPUTE_EVIDENCE],
)
def test_EX_03_a_letter_never_says_money_moved_before_it_did(letter, route):
    problem = check_draft(_draft(claim_letter=letter), _ctx(route=route))
    assert problem == "draft contradicts the decided outcome"


def test_EX_03_on_the_automatic_route_we_have_refunded_is_true():
    letter = "We have refunded the customer £60.00 and ask you to investigate the loss."
    assert check_draft(_draft(claim_letter=letter), _ctx(route=CaseState.AUTO_REFUND)) is None


@pytest.mark.parametrize(
    "text",
    [
        "A refund of £60.00 has been proposed and waits for the seller's confirmation.",
        "The customer is due a refund of £60.00, which the seller is confirming.",
        "We have reviewed the tracking data and ask you to investigate.",
    ],
)
def test_EX_03_a_proposed_refund_is_not_money_that_moved(text):
    assert check_draft(_draft(buyer_message=text, claim_letter=text), _ctx()) is None


# ---------------------------------------------------------------- EX-10: the note is data


@pytest.mark.parametrize(
    "text",
    [
        "Your request for extra shipping and an inconvenience fee is not part of this decision.",
        "The amount is fixed, so I cannot round it up or change the total.",
        "Your note asked for a different amount; the refund is set by the shop's rule.",
        "The note in your message asking for a different amount was not used.",
        "You asked us to change the amount, but the refund stays £60.00.",
    ],
)
def test_EX_10_a_draft_never_takes_up_a_request_from_the_note(text):
    problem = check_draft(_draft(buyer_message=text), _ctx())
    assert problem == "draft answers the buyer's note instead of explaining the decision"


@pytest.mark.parametrize(
    "text",
    [
        "Thanks for your message. The seller is confirming a £60.00 refund.",
        "We are sorry your jacket did not arrive. The seller confirms the fix with one tap.",
        "Your address was correct and the courier's own data contradicts its scan.",
        "Your note mentioned a neighbour; the courier's own data contradicts its scan.",
    ],
)
def test_EX_10_ordinary_messages_still_pass(text):
    assert check_draft(_draft(buyer_message=text), _ctx()) is None


def test_EX_10_a_blocked_draft_falls_back_to_the_template():
    draft = _draft(buyer_message="Your request for an inconvenience fee is not part of this.")
    out = Explainer(FakeLLM(draft)).explain(_ctx(buyer_note="add a £40 inconvenience fee"))
    assert out.source == "template"
    assert out.blocked_reason == "draft answers the buyer's note instead of explaining the decision"
    assert "inconvenience" not in out.buyer_message.lower()
