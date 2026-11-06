"""AP-03/AP-04 — an autopilot rule only ever turns "over the auto-approve limit" into an automatic
refund within the rule's maximum and the rule file's ceiling. For any amount, rule maximum and
case signals, every other route is exactly what it would be without the rule."""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.cases.engine import Confidence, FailurePoint
from app.money import Currency
from app.orders.enums import CaseState
from app.orders.models import SellerRule
from app.policy.reasons import Reason
from app.policy.rules import load_rules
from app.policy.service import PolicyService
from tests.conftest import RULES_DIR, sqlite_session_factory
from tests.factories import make_case, make_order, make_seller
from tests.fakes import FakeClock
from tests.helpers import engine_result, facts_for

BOOK = load_rules(RULES_DIR)
LIMITS = BOOK.money_limits
assert LIMITS.seller_rules is not None
CEILING = LIMITS.seller_rules.ceiling(Currency.GBP).amount_minor
AUTO = LIMITS.auto_approve_limit(Currency.GBP).amount_minor


def _route(amount: int, rule_max: int | None, fp: FailurePoint, confidence: Confidence):
    factory = sqlite_session_factory()
    clock = FakeClock()
    with factory() as s:
        seller = make_seller(s)
        order = make_order(s, seller, amount_minor=amount, payer_id="PAYER-P")
        case = make_case(s, order)
        if rule_max is not None:
            s.add(
                SellerRule(
                    seller_id=seller.id,
                    failure_point=fp.value,
                    currency="GBP",
                    max_amount_minor=rule_max,
                    status="on",
                    precedents=[],
                )
            )
        s.flush()
        d = PolicyService(s, BOOK, clock).decide(
            case, engine_result(fp, confidence), facts_for(order)
        )
        return d.route, d.codes, d.refund_amount


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    amount=st.integers(1, 40_000),
    rule_max=st.integers(1, 40_000),
    fp=st.sampled_from(list(FailurePoint)),
    confidence=st.sampled_from(list(Confidence)),
)
def test_AP_04_a_rule_changes_only_the_over_limit_route(amount, rule_max, fp, confidence):
    without = _route(amount, None, fp, confidence)
    with_rule = _route(amount, rule_max, fp, confidence)
    if with_rule[0] == without[0]:
        assert with_rule[1] == without[1]
        return
    # The only change a rule may make: over the limit -> automatic refund, within every bound.
    assert without[1][-1] == Reason.OVER_AUTO_APPROVE_LIMIT
    assert with_rule[0] == CaseState.AUTO_REFUND
    assert with_rule[1][-1] == Reason.SELLER_RULE
    assert fp in LIMITS.seller_rules.failure_points
    refund = with_rule[2]
    assert refund is not None and AUTO < refund.amount_minor <= min(rule_max, CEILING)
