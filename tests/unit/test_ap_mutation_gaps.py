"""AP: what the Autopilot tests did not pin, found by mutation testing.

A seller rule refunds money with nobody tapping, so its tenant scoping, its precedents, its window
edges, its audit trail and its statement are each pinned here. Every test names the mutant
family it kills.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.cases.engine import FailurePoint
from app.money import Currency, Money
from app.orders.enums import (
    ApproverKind,
    CaseState,
    MoneyActionKind,
    MoneyActionStatus,
    RecoveryStatus,
)
from app.orders.models import Approval, AuditLog, Case, Decision, MoneyAction, Recovery, SellerRule
from app.policy.autopilot import (
    DECLINED,
    DISMISSED,
    OFF,
    ON,
    RULE_APPROVER,
    _approvals,
    active_rules,
    dismiss,
    matching_rule,
    statement,
    suggest_rules,
    turn_off,
    turn_on,
)
from app.policy.reasons import Reason
from app.policy.rules import effective_limits
from app.policy.service import PolicyService
from tests.factories import make_case, make_order, make_seller
from tests.helpers import engine_result, facts_for, money
from tests.unit.test_ap_autopilot import LOST, SELLER, approve_over_limit, on_rule

DAMAGED = FailurePoint.DAMAGED_IN_TRANSIT
OVER = Reason.OVER_AUTO_APPROVE_LIMIT.value
LIABLE = Reason.LIABILITY_RULE.value


@pytest.fixture
def policy(db, rulebook, clock):
    return PolicyService(db, rulebook, clock)


@pytest.fixture
def shop(db, fake_paypal):
    """A seller and a factory for that seller's captured orders, each with a fresh case."""

    def build():
        seller = make_seller(db)

        def case(amount: int = 6000, *, currency: str = "GBP"):
            order = make_order(
                db,
                seller,
                amount_minor=amount,
                currency=currency,
                buyer_country="US" if currency == "USD" else "GB",
                payer_id=f"PAYER-{uuid4().hex[:10]}",
            )
            fake_paypal.add_capture(
                order.paypal_order_id, order.capture_id, money(amount, currency)
            )
            c = make_case(db, order)
            db.commit()
            return order, c

        return seller, case

    return build


def _decline(db, policy, case, fp=LOST, *, at=None):
    """The seller declines a case. `at` stamps the decline's audit row: state-machine audit rows
    take the wall clock, not the injected one; in production the two agree."""
    order, c = case(7000)
    policy.decide(c, engine_result(fp), facts_for(order))
    policy.decline(c, SELLER)
    if at is not None:
        [row] = db.scalars(
            select(AuditLog).where(AuditLog.entity_id == c.id, AuditLog.action == DECLINED)
        ).all()
        row.at = at
    db.commit()


# ---------------------------------------------------------------- tenant scoping


def test_AP_03_another_sellers_rule_never_refunds_this_sellers_order(
    db, rulebook, clock, policy, shop
):
    seller_a, case_a = shop()
    rule = on_rule(db, rulebook, clock, policy, case_a, seller_a)
    seller_b, _ = shop()
    assert matching_rule(db, rulebook, seller_a.id, LOST, money(5900)) == rule
    assert matching_rule(db, rulebook, seller_b.id, LOST, money(5900)) is None
    assert active_rules(db, seller_b.id) == []


def test_AP_01_another_sellers_decline_does_not_block_this_seller(
    db, rulebook, clock, policy, shop
):
    seller_a, case_a = shop()
    approve_over_limit(policy, case_a, [5500, 6000, 5800])
    _, case_b = shop()
    _decline(db, policy, case_b)
    assert [s.failure_point for s in suggest_rules(db, rulebook, seller_a, now=clock.now())] == [
        LOST
    ]


def test_AP_05_active_rules_are_this_sellers_rules_that_are_on_in_kind_order(db, clock):
    seller, other = make_seller(db), make_seller(db)

    def rule(owner, fp, cur, status=ON):
        db.add(
            SellerRule(
                seller_id=owner.id,
                failure_point=fp.value,
                currency=cur,
                max_amount_minor=6000,
                status=status,
                updated_at=clock.now(),
            )
        )
        db.flush()

    rule(seller, DAMAGED, "GBP")  # inserted out of kind order on purpose
    rule(seller, LOST, "USD")
    rule(seller, LOST, "GBP")
    rule(seller, DAMAGED, "USD", status=OFF)
    rule(seller, LOST, "EUR", status=DISMISSED)
    rule(other, LOST, "GBP")
    kinds = [(r.failure_point, r.currency) for r in active_rules(db, seller.id)]
    assert kinds == [(LOST.value, "GBP"), (LOST.value, "USD"), (DAMAGED.value, "GBP")]


# ---------------------------------------------------------------- window edges


def test_AP_01_an_approval_exactly_at_the_window_start_counts(db, rulebook, clock, policy, shop):
    seller, case = shop()
    approve_over_limit(policy, case, [5500, 6000, 5800])
    days = rulebook.money_limits.seller_rules.window_days
    at_edge = clock.now() + timedelta(days=days)
    assert len(suggest_rules(db, rulebook, seller, now=at_edge)) == 1


def test_AP_01_a_decline_exactly_at_the_window_start_still_blocks(
    db, rulebook, clock, policy, shop
):
    seller, case = shop()
    start = clock.now()
    _decline(db, policy, case, at=start)
    clock.advance(86_400)
    approve_over_limit(policy, case, [5500, 6000, 5800])
    days = rulebook.money_limits.seller_rules.window_days
    assert suggest_rules(db, rulebook, seller, now=start + timedelta(days=days)) == []


def test_AP_01_a_decline_before_the_window_no_longer_blocks(db, rulebook, clock, policy, shop):
    seller, case = shop()
    start = clock.now()
    _decline(db, policy, case, at=start)
    clock.advance(2 * 86_400)
    approve_over_limit(policy, case, [5500, 6000, 5800])
    days = rulebook.money_limits.seller_rules.window_days
    later = start + timedelta(days=days, seconds=86_400)
    assert len(suggest_rules(db, rulebook, seller, now=later)) == 1


# ---------------------------------------------------------------- which approvals are precedents


def _approved(db, seller, *, case_id, at, route=CaseState.AWAITING_SELLER, codes=(LIABLE, OVER)):
    """A seller approval with an exact decision shape (route and reason codes)."""
    order = make_order(db, seller, amount_minor=6000)
    db.add(
        Case(
            id=case_id,
            order_id=order.id,
            seller_id=seller.id,
            reported_by="buyer",
            reason="not_here",
            state=CaseState.SETTLED.value,
        )
    )
    decision = Decision(
        case_id=case_id,
        failure_point=LOST.value,
        route=route.value,
        refund_amount_minor=6000,
        currency="GBP",
        reason_codes=list(codes),
    )
    db.add(decision)
    db.flush()
    db.add(
        Approval(
            case_id=case_id,
            decision_id=decision.id,
            approver_kind=ApproverKind.SELLER.value,
            approver_id="seller-1",
            at=at,
        )
    )
    db.flush()


def test_AP_01_only_cases_held_just_for_the_limit_are_precedents(db, clock):
    seller, now = make_seller(db), clock.now()
    _approved(db, seller, case_id="blame-buyer", at=now, codes=(LIABLE, "would_blame_buyer"))
    _approved(db, seller, case_id="held", at=now, route=CaseState.HOLD_FOR_REVIEW)
    _approved(db, seller, case_id="no-codes", at=now, codes=())
    # A note before the last code (here the scan audit) does not change why the case waited.
    _approved(db, seller, case_id="noted", at=now, codes=(LIABLE, "scan_contradicted", OVER))
    assert _approvals(db, seller.id, now - timedelta(days=1)) == {
        (LOST.value, "GBP"): [("noted", 6000)]
    }


def test_AP_02_precedents_are_listed_oldest_first_then_by_case(db, clock):
    seller, t0 = make_seller(db), clock.now()
    t1 = t0 + timedelta(hours=1)
    _approved(db, seller, case_id="case-b", at=t1)  # inserted out of order on purpose
    _approved(db, seller, case_id="case-a", at=t1)
    _approved(db, seller, case_id="case-z", at=t0)
    [(_, cases)] = _approvals(db, seller.id, t0 - timedelta(days=1)).items()
    assert [case_id for case_id, _ in cases] == ["case-z", "case-a", "case-b"]


def test_AP_01_a_kind_with_too_few_approvals_does_not_hide_the_next_kind(
    db, rulebook, clock, policy, shop
):
    seller, case = shop()
    approve_over_limit(policy, case, [5500, 6000])  # CARRIER_LOST sorts first: too few
    approve_over_limit(policy, case, [5500, 6000, 5800], fp=DAMAGED)
    assert [s.failure_point for s in suggest_rules(db, rulebook, seller, now=clock.now())] == [
        DAMAGED
    ]


def test_AP_01_a_kind_the_limit_now_covers_does_not_hide_the_next_kind(
    db, rulebook, clock, policy, shop
):
    seller, case = shop()
    approve_over_limit(policy, case, [5500, 6000, 5800])  # GBP sorts before USD
    approve_over_limit(policy, case, [5500, 6000, 5800], currency="USD")
    seller.auto_approve_limits = {"GBP": 7000}  # the seller raised the GBP limit since
    db.commit()
    assert [s.currency for s in suggest_rules(db, rulebook, seller, now=clock.now())] == ["USD"]


# ---------------------------------------------------------------- the audit trail of rule changes


def test_AP_02_every_rule_change_writes_a_full_audit_row(db, rulebook, clock, policy, shop):
    seller, case = shop()
    approve_over_limit(policy, case, [5500, 6000, 5800])
    actor = "seller:" + "x" * 70  # longer than the column: kept to 64 characters
    on_at = clock.now()
    rule = turn_on(db, rulebook, seller, LOST, "GBP", now=on_at, actor=actor)
    assert rule.updated_at == on_at
    clock.advance(60)
    off_at = clock.now()
    turn_off(db, seller, rule.id, now=off_at, actor="seller:test")
    assert rule.updated_at == off_at
    rows = db.scalars(
        select(AuditLog).where(AuditLog.entity_id == rule.id).order_by(AuditLog.at)
    ).all()
    after = {
        "failure_point": LOST.value,
        "currency": "GBP",
        "max_amount_minor": 6000,
        "precedents": rule.precedents,
    }
    assert [(r.actor, r.action, r.entity_type, r.before, r.after, r.at) for r in rows] == [
        (actor[:64], "seller_rule.on", "seller_rule", {}, {"status": ON, **after}, on_at),
        (
            "seller:test",
            "seller_rule.off",
            "seller_rule",
            {"status": ON},
            {"status": OFF, **after},
            off_at,
        ),
    ]
    assert len(rule.precedents) == 3


def test_AP_02_a_dismissal_is_audited_with_nothing_before(db, rulebook, clock, policy, shop):
    seller, case = shop()
    approve_over_limit(policy, case, [5500, 6000, 5800])
    rule = dismiss(db, rulebook, seller, LOST, "GBP", now=clock.now(), actor="seller:test")
    [row] = db.scalars(select(AuditLog).where(AuditLog.entity_id == rule.id)).all()
    assert (row.action, row.before, row.after["status"], row.at) == (
        "seller_rule.dismissed",
        {},
        DISMISSED,
        clock.now(),
    )
    assert rule.updated_at == clock.now()


def test_AP_03_the_reason_names_the_rules_maximum(db, rulebook, clock, policy, shop):
    seller, case = shop()
    on_rule(db, rulebook, clock, policy, case, seller)
    order, c = case(5900)
    decision = policy.decide(c, engine_result(LOST), facts_for(order))
    assert decision.codes[-1] == Reason.SELLER_RULE
    limit = effective_limits(rulebook, seller, Currency.GBP).auto_approve
    assert str(limit) in decision.reasons[-1]
    assert str(Money(amount_minor=6000, currency=Currency.GBP)) in decision.reasons[-1]


# ---------------------------------------------------------------- AP-07 statement


def _rule_refund(
    db,
    seller,
    *,
    completed_at,
    amount=5000,
    currency="GBP",
    ma_id=None,
    kind=MoneyActionKind.REFUND,
    status=MoneyActionStatus.COMPLETED,
    approver_kind=ApproverKind.SYSTEM,
    approver_id=RULE_APPROVER + "rule-1",
    recovery=None,
    on_case=True,
):
    """A refund approved the given way, with an optional carrier claim (status, claim, paid)."""
    order = make_order(db, seller, amount_minor=amount, currency=currency)
    case = make_case(db, order)
    decision = Decision(case_id=case.id, route=CaseState.AUTO_REFUND.value, currency=currency)
    db.add(decision)
    db.flush()
    db.add(
        Approval(
            case_id=case.id,
            decision_id=decision.id,
            approver_kind=approver_kind.value,
            approver_id=approver_id,
            at=completed_at,
        )
    )
    ma = MoneyAction(
        id=ma_id or str(uuid4()),
        order_id=order.id,
        case_id=case.id if on_case else None,
        decision_id=decision.id,
        seller_id=seller.id,
        kind=kind.value,
        amount_minor=amount,
        currency=currency,
        status=status.value,
        completed_at=completed_at,
    )
    db.add(ma)
    if recovery is not None:
        rec_status, claim, paid = recovery
        db.add(
            Recovery(
                case_id=case.id,
                order_id=order.id,
                seller_id=seller.id,
                decision_id=decision.id,
                carrier="Royal Mail",
                carrier_id="royal_mail",
                failure_point=LOST.value,
                claim_minor=claim,
                currency=currency,
                status=rec_status.value,
                deadline_at=completed_at + timedelta(days=30),
                recovered_minor=paid,
            )
        )
    db.flush()
    return ma, order


def test_AP_07_statement_counts_only_completed_rule_refunds_in_the_window(db, clock):
    seller, now = make_seller(db), clock.now()
    edge = now - timedelta(days=7)
    kept, _ = _rule_refund(db, seller, completed_at=edge)  # exactly at the window start
    _rule_refund(db, seller, completed_at=now, status=MoneyActionStatus.FAILED)
    _rule_refund(db, seller, completed_at=now, kind=MoneyActionKind.EVIDENCE)
    _rule_refund(db, seller, completed_at=now, approver_kind=ApproverKind.SELLER)
    _rule_refund(db, seller, completed_at=now, approver_id="seller-1")
    _rule_refund(db, make_seller(db), completed_at=now)  # another seller's
    s = statement(db, seller.id, now=now)
    assert [c.order_id for c in s.cases] == [kept.order_id]


def test_AP_07_statement_orders_newest_first_then_by_id(db, clock):
    seller, now = make_seller(db), clock.now()
    earlier = now - timedelta(hours=1)
    _rule_refund(db, seller, completed_at=earlier, ma_id="ma-b")  # inserted out of order
    _rule_refund(db, seller, completed_at=earlier, ma_id="ma-a")
    _rule_refund(db, seller, completed_at=now, ma_id="ma-c")
    by_order = {ma.order_id: ma.id for ma in db.scalars(select(MoneyAction)).all()}
    assert [by_order[c.order_id] for c in statement(db, seller.id, now=now).cases] == [
        "ma-c",
        "ma-a",
        "ma-b",
    ]


def test_AP_07_statement_totals_per_currency_with_claims_and_recoveries(db, clock):
    seller, now = make_seller(db), clock.now()
    t = now - timedelta(hours=1)
    prepared, submitted, paid = (
        RecoveryStatus.PREPARED,
        RecoveryStatus.SUBMITTED,
        RecoveryStatus.PAID,
    )
    _rule_refund(db, seller, completed_at=t, amount=5000, recovery=(prepared, 4000, None))
    _rule_refund(db, seller, completed_at=t, amount=6000, recovery=(prepared, 4500, None))
    _rule_refund(db, seller, completed_at=t, amount=7000, recovery=(submitted, 3000, None))
    _rule_refund(db, seller, completed_at=t, amount=8000, recovery=(paid, 2000, 1500))
    _rule_refund(db, seller, completed_at=t, amount=9000, recovery=(paid, 2500, 2500))
    _rule_refund(db, seller, completed_at=t, amount=1000, recovery=(paid, 900, None))
    _rule_refund(db, seller, completed_at=t, amount=3000, currency="USD")
    totals = {x.currency: x for x in statement(db, seller.id, now=now).totals}
    assert list(totals) == ["GBP", "USD"]
    gbp = totals["GBP"]
    assert (gbp.refunds, gbp.refunded_minor) == (6, 36_000)
    assert (gbp.claimable_minor, gbp.ready_to_send, gbp.recovered_minor) == (11_500, 2, 4000)
    usd = totals["USD"]
    assert (usd.refunds, usd.refunded_minor, usd.claimable_minor) == (1, 3000, 0)


def test_AP_07_statement_case_lines_keep_their_own_time_and_case(db, clock):
    seller, now = make_seller(db), clock.now()
    when = now - timedelta(hours=3)
    ma, order = _rule_refund(db, seller, completed_at=when, on_case=False)
    [line] = statement(db, seller.id, now=now).cases
    assert (line.case_id, line.refunded_at, line.order_id) == ("", when, order.id)
    assert ma.case_id is None
