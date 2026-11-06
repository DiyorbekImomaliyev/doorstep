"""Judge review J7: the buyer's message says who acts next and for how much, in plain words."""

from __future__ import annotations

import pytest

from app.ai.explainer import template
from app.orders.enums import CaseState
from tests.helpers import money
from tests.unit.test_ex_explainer import _ctx


@pytest.mark.parametrize(
    ("route", "extra", "says"),
    [
        (CaseState.AUTO_REFUND, {}, "Your refund of £40.00 has been sent through PayPal."),
        (CaseState.AWAITING_SELLER, {}, "The seller has been asked to approve a refund of £40.00."),
        (CaseState.AWAITING_SELLER, {"reship": True, "refund": None}, "confirm a replacement"),
        (
            CaseState.AWAITING_SELLER,
            {"refund": None},
            "The seller has been asked to decide the fix.",
        ),
        (CaseState.DISPUTE_EVIDENCE, {}, "PayPal decides it"),
        (CaseState.HOLD_FOR_REVIEW, {}, "Nothing has been refused."),
    ],
)
def test_J7_the_buyer_is_told_the_next_step(route, extra, says):
    ctx = _ctx(**{"route": route, "refund": money(4000), **extra})
    message = template(ctx).buyer_message
    assert says in message
    assert "order " + ctx.order_ref in message
