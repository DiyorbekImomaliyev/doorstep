"""AP — seller autopilot rules, learned from the seller's own approvals (the Learn stage).

Doorstep suggests a rule only from what the seller already decided; the seller turns it on; the
policy applies it only where the auto-approve limit was the one thing stopping a refund.
AT-06 — the agent's PayPal pre-check is reused, so opening a case twice never repeats it.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import AppDeps, Services
from app.cases.engine import Confidence, FailurePoint
from app.main import create_app
from app.orders.enums import ApproverKind, CaseState, Fix, ProofKind
from app.orders.models import Approval, AuditLog, Dispute, SellerRule
from app.policy.autopilot import (
    AutopilotError,
    dismiss,
    matching_rule,
    suggest_rules,
    turn_off,
    turn_on,
)
from app.policy.reasons import Reason
from app.policy.rules import load_rules
from app.policy.service import Approver, PolicyService
from app.proof.receipts import ReceiptSigner
from app.runtime import ROOT
from app.security.links import LinkSigner
from tests.factories import make_case, make_order, make_seller
from tests.helpers import engine_result, facts_for, money

SELLER = Approver(kind=ApproverKind.SELLER, id="seller-1")
LOST = FailurePoint.CARRIER_LOST


@pytest.fixture
def policy(db, rulebook, clock):
    return PolicyService(db, rulebook, clock)


@pytest.fixture
def shop(db, fake_paypal):
    """A seller and a factory for that seller's captured orders with a fresh case each."""

    def build(*, caps: dict[str, int] | None = None):
        seller = make_seller(db, daily_refund_caps=caps)

        def case(amount: int = 6000, *, currency: str = "GBP", payer: str | None = None):
            order = make_order(
                db,
                seller,
                amount_minor=amount,
                currency=currency,
                buyer_country="US" if currency == "USD" else "GB",
                payer_id=payer or f"PAYER-{uuid4().hex[:10]}",
            )
            fake_paypal.add_capture(
                order.paypal_order_id, order.capture_id, money(amount, currency)
            )
            c = make_case(db, order)
            db.commit()
            return order, c

        return seller, case

    return build


def approve_over_limit(policy, case, amounts, *, fp=LOST, currency="GBP"):
    """The seller approves cases that waited for them only because of the auto-approve limit."""
    for amount in amounts:
        order, c = case(amount, currency=currency)
        decision = policy.decide(c, engine_result(fp), facts_for(order))
        assert decision.route == CaseState.AWAITING_SELLER
        assert decision.codes[-1] == Reason.OVER_AUTO_APPROVE_LIMIT
        policy.approve(c, SELLER)


def on_rule(db, rulebook, clock, policy, case, seller, amounts=(5500, 6000, 5800)):
    approve_over_limit(policy, case, amounts)
    rule = turn_on(db, rulebook, seller, LOST, "GBP", now=clock.now(), actor="seller:test")
    db.commit()
    return rule


# ---------------------------------------------------------------- AP-01


def test_AP_01_three_approvals_over_the_limit_suggest_a_rule(db, rulebook, clock, policy, shop):
    seller, case = shop()
    approve_over_limit(policy, case, [5500, 6000, 5800])
    [s] = suggest_rules(db, rulebook, seller, now=clock.now())
    assert (s.failure_point, s.currency, s.approved) == (LOST, "GBP", 3)
    assert s.max_amount_minor == 6000
    assert s.window_days == rulebook.money_limits.seller_rules.window_days
    assert len(s.case_ids) == 3


def test_AP_01_two_approvals_are_not_enough(db, rulebook, clock, policy, shop):
    seller, case = shop()
    approve_over_limit(policy, case, [5500, 6000])
    assert suggest_rules(db, rulebook, seller, now=clock.now()) == []


def test_AP_01_a_decline_of_that_kind_blocks_the_suggestion(db, rulebook, clock, policy, shop):
    seller, case = shop()
    approve_over_limit(policy, case, [5500, 6000, 5800])
    order, c = case(7000)
    policy.decide(c, engine_result(LOST), facts_for(order))
    policy.decline(c, SELLER)
    db.commit()
    assert suggest_rules(db, rulebook, seller, now=clock.now()) == []


def test_AP_01_a_decline_of_another_kind_does_not_block(db, rulebook, clock, policy, shop):
    seller, case = shop()
    approve_over_limit(policy, case, [5500, 6000, 5800])
    order, c = case(7000)
    policy.decide(c, engine_result(FailurePoint.DAMAGED_IN_TRANSIT), facts_for(order))
    policy.decline(c, SELLER)
    db.commit()
    assert [s.failure_point for s in suggest_rules(db, rulebook, seller, now=clock.now())] == [LOST]


def test_AP_01_approvals_outside_the_window_do_not_count(db, rulebook, clock, policy, shop):
    seller, case = shop()
    approve_over_limit(policy, case, [5500, 6000, 5800])
    days = rulebook.money_limits.seller_rules.window_days
    later = clock.now() + timedelta(days=days, seconds=1)
    assert suggest_rules(db, rulebook, seller, now=later) == []


def test_AP_01_held_cases_the_seller_approved_do_not_count(db, rulebook, clock, policy, shop):
    seller, case = shop()
    for amount in (5500, 6000, 5800):
        order, c = case(amount)
        decision = policy.decide(c, engine_result(LOST, Confidence.LOW), facts_for(order))
        assert decision.route == CaseState.HOLD_FOR_REVIEW
        policy.approve(c, SELLER)
    assert suggest_rules(db, rulebook, seller, now=clock.now()) == []


def test_AP_01_reviewer_approvals_do_not_count(db, rulebook, clock, policy, shop):
    seller, case = shop()
    for amount in (5500, 6000, 5800):
        order, c = case(amount)
        policy.decide(c, engine_result(LOST), facts_for(order))
        policy.approve(c, Approver(kind=ApproverKind.REVIEWER, id="r-1"))
    assert suggest_rules(db, rulebook, seller, now=clock.now()) == []


def test_AP_01_failure_points_outside_the_rule_file_are_never_suggested(
    db, rulebook, clock, policy, shop
):
    assert FailurePoint.WRONG_ITEM not in rulebook.money_limits.seller_rules.failure_points
    seller, case = shop()
    approve_over_limit(policy, case, [5500, 6000, 5800], fp=FailurePoint.WRONG_ITEM)
    assert suggest_rules(db, rulebook, seller, now=clock.now()) == []


def test_AP_01_kinds_and_currencies_are_counted_separately(db, rulebook, clock, policy, shop):
    seller, case = shop()
    approve_over_limit(policy, case, [5500, 6000])
    approve_over_limit(policy, case, [5500], fp=FailurePoint.DAMAGED_IN_TRANSIT)
    approve_over_limit(policy, case, [6500], currency="USD")
    assert suggest_rules(db, rulebook, seller, now=clock.now()) == []


def test_AP_01_another_sellers_approvals_do_not_count(db, rulebook, clock, policy, shop):
    _, case = shop()
    approve_over_limit(policy, case, [5500, 6000, 5800])
    other, _ = shop()
    assert suggest_rules(db, rulebook, other, now=clock.now()) == []


# ---------------------------------------------------------------- AP-02


def test_AP_02_maximum_is_the_largest_approval_capped_by_the_ceiling(
    db, rulebook, clock, policy, shop
):
    ceiling = rulebook.money_limits.seller_rules.ceiling(money(1, "GBP").currency).amount_minor
    seller, case = shop()
    approve_over_limit(policy, case, [6000, ceiling + 5000, 7000])
    [s] = suggest_rules(db, rulebook, seller, now=clock.now())
    assert s.max_amount_minor == ceiling


def test_AP_02_turning_on_uses_the_suggested_maximum(db, rulebook, clock, policy, shop):
    seller, case = shop()
    rule = on_rule(db, rulebook, clock, policy, case, seller)
    assert (rule.status, rule.max_amount_minor, rule.failure_point, rule.currency) == (
        "on",
        6000,
        LOST.value,
        "GBP",
    )
    assert len(rule.precedents) == 3
    assert suggest_rules(db, rulebook, seller, now=clock.now()) == []  # already on


def test_AP_02_no_rule_without_a_current_suggestion(db, rulebook, clock, policy, shop):
    seller, case = shop()
    approve_over_limit(policy, case, [5500, 6000])
    with pytest.raises(AutopilotError):
        turn_on(db, rulebook, seller, LOST, "GBP", now=clock.now(), actor="seller:test")
    assert db.scalars(select(SellerRule)).all() == []


# ---------------------------------------------------------------- AP-03


def test_AP_03_an_active_rule_refunds_within_its_maximum(db, rulebook, clock, policy, shop):
    seller, case = shop()
    rule = on_rule(db, rulebook, clock, policy, case, seller)
    order, c = case(6000)
    decision = policy.decide(c, engine_result(LOST), facts_for(order))
    assert decision.route == CaseState.AUTO_REFUND
    assert decision.codes[-1] == Reason.SELLER_RULE
    assert decision.money_action_id is not None
    [approval] = db.scalars(select(Approval).where(Approval.case_id == c.id)).all()
    assert approval.approver_kind == ApproverKind.SYSTEM
    assert approval.approver_id == f"seller-rule:{rule.id}"


def test_AP_03_over_the_rule_maximum_still_goes_to_the_seller(db, rulebook, clock, policy, shop):
    seller, case = shop()
    on_rule(db, rulebook, clock, policy, case, seller)
    order, c = case(6001)
    decision = policy.decide(c, engine_result(LOST), facts_for(order))
    assert decision.route == CaseState.AWAITING_SELLER
    assert decision.codes[-1] == Reason.OVER_AUTO_APPROVE_LIMIT


def test_AP_03_under_the_limit_is_the_ordinary_auto_refund(db, rulebook, clock, policy, shop):
    seller, case = shop()
    on_rule(db, rulebook, clock, policy, case, seller)
    order, c = case(4000)
    assert policy.decide(c, engine_result(LOST), facts_for(order)).codes[-1] == Reason.AUTO_REFUND


@pytest.mark.parametrize(
    ("fp", "currency"), [(FailurePoint.DAMAGED_IN_TRANSIT, "GBP"), (LOST, "USD")]
)
def test_AP_03_a_rule_covers_only_its_kind_and_currency(
    db, rulebook, clock, policy, shop, fp, currency
):
    seller, case = shop()
    on_rule(db, rulebook, clock, policy, case, seller)
    order, c = case(6000, currency=currency)
    assert policy.decide(c, engine_result(fp), facts_for(order)).route == CaseState.AWAITING_SELLER


def _with_rules(rules_copy, edit):
    path = rules_copy / "money_limits_v1.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    edit(data)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return load_rules(rules_copy)


def test_AP_03_a_lower_ceiling_caps_an_existing_rule(db, rulebook, clock, policy, shop, rules_copy):
    seller, case = shop()
    on_rule(db, rulebook, clock, policy, case, seller)
    lower = _with_rules(rules_copy, lambda d: d["seller_rules"]["ceiling_minor"].update(GBP=5500))
    order, c = case(6000)
    decision = PolicyService(db, lower, clock).decide(c, engine_result(LOST), facts_for(order))
    assert decision.route == CaseState.AWAITING_SELLER


def test_AP_03_a_kind_removed_from_the_rule_file_stops_its_rule(
    db, rulebook, clock, policy, shop, rules_copy
):
    seller, case = shop()
    on_rule(db, rulebook, clock, policy, case, seller)
    fewer = _with_rules(
        rules_copy, lambda d: d["seller_rules"].update(failure_points=["DAMAGED_IN_TRANSIT"])
    )
    assert matching_rule(db, fewer, seller.id, LOST, money(6000)) is None


def test_AP_03_no_seller_rules_block_means_autopilot_is_off(
    db, rulebook, clock, policy, shop, rules_copy
):
    seller, case = shop()
    on_rule(db, rulebook, clock, policy, case, seller)
    off = _with_rules(rules_copy, lambda d: d.pop("seller_rules"))
    assert off.money_limits.seller_rules is None
    assert suggest_rules(db, off, seller, now=clock.now()) == []
    assert matching_rule(db, off, seller.id, LOST, money(6000)) is None
    with pytest.raises(AutopilotError):
        turn_on(db, off, seller, LOST, "GBP", now=clock.now(), actor="seller:test")


# ---------------------------------------------------------------- AP-04


def _dispute(db, order):
    db.add(Dispute(order_id=order.id, paypal_dispute_id=f"PP-D-{uuid4().hex[:8]}", status="open"))
    db.commit()


EARLIER_CHECKS = {
    "open dispute": ({}, {}, "dispute", CaseState.DISPUTE_EVIDENCE, Reason.OPEN_DISPUTE),
    "contradiction": (
        {"contradiction": True},
        {},
        None,
        CaseState.HOLD_FOR_REVIEW,
        Reason.CONTRADICTION,
    ),
    "low confidence": (
        {"confidence": Confidence.LOW},
        {},
        None,
        CaseState.HOLD_FOR_REVIEW,
        Reason.LOW_CONFIDENCE,
    ),
    "possession proven": (
        {},
        {"proofs": frozenset({ProofKind.SIGNATURE})},
        None,
        CaseState.AWAITING_SELLER,
        Reason.POSSESSION_PROVEN,
    ),
    "reship": ({}, {"fix": Fix.RESHIP}, None, CaseState.AWAITING_SELLER, Reason.RESHIP),
}


@pytest.mark.parametrize("name", sorted(EARLIER_CHECKS))
def test_AP_04_every_earlier_check_still_wins(db, rulebook, clock, policy, shop, name):
    engine_kw, facts_kw, extra, route, code = EARLIER_CHECKS[name]
    seller, case = shop()
    on_rule(db, rulebook, clock, policy, case, seller)
    order, c = case(6000)
    if extra == "dispute":
        _dispute(db, order)
    decision = policy.decide(c, engine_result(LOST, **engine_kw), facts_for(order, **facts_kw))
    assert decision.route == route
    assert code in decision.codes
    assert Reason.SELLER_RULE not in decision.codes


def test_AP_04_repeat_claimant_still_holds(db, rulebook, clock, policy, shop):
    seller, case = shop()
    on_rule(db, rulebook, clock, policy, case, seller)
    for _ in range(2):
        order, c = case(6000, payer="PAYER-REPEAT")
        policy.decide(c, engine_result(LOST), facts_for(order))
    order, c = case(6000, payer="PAYER-REPEAT")
    decision = policy.decide(c, engine_result(LOST), facts_for(order))
    assert decision.route == CaseState.HOLD_FOR_REVIEW
    assert decision.codes[-1] == Reason.REPEAT_CLAIMANT


def test_AP_04_the_daily_cap_still_wins(db, rulebook, clock, policy, shop):
    seller, case = shop(caps={"GBP": 20_000})  # three approvals already queued 17_300 today
    on_rule(db, rulebook, clock, policy, case, seller)
    order, c = case(6000)
    decision = policy.decide(c, engine_result(LOST), facts_for(order))
    assert decision.route == CaseState.AWAITING_SELLER
    assert decision.codes[-1] == Reason.DAILY_CAP_REACHED


# ---------------------------------------------------------------- AP-05


def _audit_actions(db, entity_id):
    rows = db.scalars(select(AuditLog).where(AuditLog.entity_id == entity_id)).all()
    return [r.action for r in rows]


def test_AP_05_turning_off_stops_the_rule_and_its_suggestion(db, rulebook, clock, policy, shop):
    seller, case = shop()
    rule = on_rule(db, rulebook, clock, policy, case, seller)
    turn_off(db, seller, rule.id, now=clock.now(), actor="seller:test")
    db.commit()
    order, c = case(6000)
    assert (
        policy.decide(c, engine_result(LOST), facts_for(order)).route == CaseState.AWAITING_SELLER
    )
    policy.approve(c, SELLER)
    assert suggest_rules(db, rulebook, seller, now=clock.now()) == []
    assert _audit_actions(db, rule.id) == ["seller_rule.on", "seller_rule.off"]


def test_AP_05_not_now_dismisses_the_suggestion(db, rulebook, clock, policy, shop):
    seller, case = shop()
    approve_over_limit(policy, case, [5500, 6000, 5800])
    rule = dismiss(db, rulebook, seller, LOST, "GBP", now=clock.now(), actor="seller:test")
    db.commit()
    assert rule.status == "dismissed"
    assert suggest_rules(db, rulebook, seller, now=clock.now()) == []
    assert matching_rule(db, rulebook, seller.id, LOST, money(6000)) is None
    assert _audit_actions(db, rule.id) == ["seller_rule.dismissed"]


def test_AP_05_dismissing_needs_a_current_suggestion(db, rulebook, clock, shop):
    seller, _ = shop()
    with pytest.raises(AutopilotError):
        dismiss(db, rulebook, seller, LOST, "GBP", now=clock.now(), actor="seller:test")


def test_AP_05_a_seller_cannot_turn_off_another_sellers_rule(db, rulebook, clock, policy, shop):
    seller, case = shop()
    rule = on_rule(db, rulebook, clock, policy, case, seller)
    other, _ = shop()
    with pytest.raises(AutopilotError):
        turn_off(db, other, rule.id, now=clock.now(), actor="seller:other")
    with pytest.raises(AutopilotError):
        turn_off(db, seller, "no-such-rule", now=clock.now(), actor="seller:test")
    assert db.get(SellerRule, rule.id).status == "on"


# ---------------------------------------------------------------- AP-02/05/06 over HTTP, AT-06


def _api(session_factory, settings, clock) -> TestClient:
    services = Services(
        links=LinkSigner("a" * 32, clock),
        receipts=ReceiptSigner.generate(),
        orders=None,
        approved=None,
    )
    deps = AppDeps(
        settings=settings,
        session_factory=session_factory,
        paypal_verifier=None,
        clock=clock,
        rules_dir=ROOT / "rules",
        services=services,
    )
    return TestClient(create_app(deps))


def test_AP_06_seeded_demo_suggests_after_malika_approves_diyorbek(
    session_factory, settings, clock
):
    api = _api(session_factory, settings, clock)
    seed = api.post("/demo/seed?scratch=1").json()
    token = seed["seller_token"]
    base = f"/api/seller/{token}/autopilot"
    assert api.get(base).json() == {"rules": [], "suggestions": []}

    diyorbek = api.post(
        f"/api/buyer/{seed['buyers']['sam-jacket']}/not-here",
        json={"reason": "not_received", "note": ""},
    )
    assert diyorbek.status_code == 200, diyorbek.text
    case_id = diyorbek.json()["case_id"]
    assert api.post(f"/api/seller/{token}/cases/{case_id}/approve").status_code == 200

    [s] = api.get(base).json()["suggestions"]
    assert (s["failure_point"], s["currency"], s["approved"], s["max_amount_minor"]) == (
        "CARRIER_LOST",
        "GBP",
        3,
        6000,
    )
    on = api.post(base, json={"failure_point": "CARRIER_LOST", "currency": "GBP"})
    assert on.status_code == 200, on.text
    rule = on.json()
    assert (rule["status"], rule["max_amount_minor"]) == ("on", 6000)
    assert api.get(base).json()["suggestions"] == []
    assert (
        api.post(base, json={"failure_point": "CARRIER_LOST", "currency": "GBP"}).status_code == 409
    )

    off = api.post(f"{base}/{rule['id']}/off")
    assert off.status_code == 200 and off.json()["status"] == "off"
    assert api.post(f"{base}/no-such-rule/off").status_code == 404


def test_AP_05_not_now_over_http(session_factory, settings, clock):
    api = _api(session_factory, settings, clock)
    token = api.post("/demo/seed?scratch=1").json()["seller_token"]
    base = f"/api/seller/{token}/autopilot"
    body = {"failure_point": "CARRIER_LOST", "currency": "GBP"}
    assert api.post(f"{base}/dismiss", json=body).status_code == 409  # nothing to dismiss yet
    assert (
        api.post(f"{base}/dismiss", json={"failure_point": "NOPE", "currency": "GBP"}).status_code
        == 422
    )


def test_AP_02_another_seller_cannot_see_or_change_my_autopilot(session_factory, settings, clock):
    api = _api(session_factory, settings, clock)
    api.post("/demo/seed?scratch=1")
    assert api.get("/api/seller/not-a-token/autopilot").status_code in (401, 404)


def test_AT_06_opening_a_case_again_reuses_the_agent_run(session_factory, settings, clock):
    api = _api(session_factory, settings, clock)
    token = api.post("/demo/seed?scratch=1").json()["seller_token"]
    rows = api.get(f"/api/seller/{token}/orders").json()
    denim = next(r for r in rows if r["item"].startswith("Denim"))
    url = f"/api/seller/{token}/agent/cases/{denim['case_id']}"
    page = f"/api/seller/{token}/cases/{denim['case_id']}"

    def agent_rows() -> int:
        return sum(i["kind"] == "agent" for i in api.get(page).json()["timeline"])

    first = api.post(url).json()
    after_first = agent_rows()
    assert api.post(url).json() == first
    assert agent_rows() == after_first  # reused: the timeline does not repeat the run

    from app.api.journey import AGENT_REUSE_S

    clock.advance(AGENT_REUSE_S + 1)
    api.post(url)
    assert agent_rows() > after_first  # stale: the agent checks PayPal again


def test_AP_01_no_suggestion_when_the_ordinary_limit_already_covers_it(
    db, rulebook, clock, policy, shop
):
    seller, case = shop()
    approve_over_limit(policy, case, [5500, 6000, 5800])
    seller.auto_approve_limits = {"GBP": 6000}  # the seller raised their own limit since
    db.commit()
    assert suggest_rules(db, rulebook, seller, now=clock.now()) == []


def test_AP_02_turning_on_one_of_several_suggestions(db, rulebook, clock, policy, shop):
    seller, case = shop()
    approve_over_limit(policy, case, [5500, 6000, 5800])
    approve_over_limit(policy, case, [5200, 5300, 5400], fp=FailurePoint.DAMAGED_IN_TRANSIT)
    kinds = [s.failure_point for s in suggest_rules(db, rulebook, seller, now=clock.now())]
    assert kinds == [LOST, FailurePoint.DAMAGED_IN_TRANSIT]
    rule = turn_on(
        db, rulebook, seller, FailurePoint.DAMAGED_IN_TRANSIT, "GBP", now=clock.now(), actor="s"
    )
    assert (rule.failure_point, rule.max_amount_minor) == ("DAMAGED_IN_TRANSIT", 5400)
    assert [s.failure_point for s in suggest_rules(db, rulebook, seller, now=clock.now())] == [LOST]
