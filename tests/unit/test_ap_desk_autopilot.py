"""AP-10..AP-12: the Autopilot page's read-only desk.

AP-10 each kind a rule may cover, with its status and limits from rules/money_limits_v1.yaml.
AP-11 what the rules did: every refund a seller rule approved.
AP-12 what if: past cases replayed through the policy's own `matching_rule`, with the daily cap.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.automate_desk import autopilot_desk
from app.api.deps import AppDeps, Services
from app.cases.engine import Confidence, FailurePoint
from app.main import create_app
from app.money import Currency
from app.orders.enums import ApproverKind
from app.orders.models import SellerRule
from app.policy.autopilot import matching_rule, turn_on
from app.policy.reasons import Reason
from app.policy.rules import RuleBook, effective_limits
from app.policy.service import Approver, PolicyService
from app.proof.receipts import ReceiptSigner
from app.runtime import ROOT
from app.security.links import LinkSigner
from tests.factories import make_case, make_order, make_seller
from tests.helpers import engine_result, facts_for, money

SECRET = "desk-test-secret-0123456789abcdef"
SELLER = Approver(kind=ApproverKind.SELLER, id="seller-1")
LOST, DAMAGED = FailurePoint.CARRIER_LOST, FailurePoint.DAMAGED_IN_TRANSIT
BUYER_NAME = "Buyer Name Secret"


@pytest.fixture
def policy(db, rulebook, clock):
    return PolicyService(db, rulebook, clock)


@pytest.fixture
def shop(db, fake_paypal):
    """A seller and a factory for that seller's captured orders with a fresh case each."""

    def build(*, caps: dict[str, int] | None = None):
        seller = make_seller(db, daily_refund_caps=caps)

        def case(amount: int = 6000, *, currency: str = "GBP", item: str = "Wool scarf"):
            order = make_order(
                db,
                seller,
                amount_minor=amount,
                currency=currency,
                buyer_country="US" if currency == "USD" else "GB",
                payer_id=f"PAYER-{uuid4().hex[:10]}",
            )
            order.item_description = item
            order.shipping_address = {"name": BUYER_NAME, "line1": "1 Secret Road"}
            fake_paypal.add_capture(
                order.paypal_order_id, order.capture_id, money(amount, currency)
            )
            c = make_case(db, order)
            db.commit()
            return order, c

        return seller, case

    return build


def decide(policy, case, amount, *, fp=LOST, currency="GBP", **kw):
    order, c = case(amount, currency=currency)
    d = policy.decide(c, engine_result(fp, **kw), facts_for(order))
    return c, d


def approve_over_limit(policy, case, amounts, *, fp=LOST):
    for amount in amounts:
        c, d = decide(policy, case, amount, fp=fp)
        assert d.codes[-1] == Reason.OVER_AUTO_APPROVE_LIMIT
        policy.approve(c, SELLER)


def card(desk, fp=LOST, currency="GBP"):
    return next(c for c in desk.cards if (c.failure_point, c.currency) == (fp.value, currency))


# ---------------------------------------------------------------- AP-10


def test_AP_10_cards_list_every_kind_with_limits_from_the_rule_file(
    db, rulebook, clock, policy, shop
):
    seller, case = shop()
    approve_over_limit(policy, case, [5500, 5800])
    desk = autopilot_desk(db, rulebook, seller, now=clock.now())
    cfg = rulebook.money_limits.seller_rules
    assert [c.failure_point for c in desk.cards] == [fp.value for fp in cfg.failure_points]
    lost = card(desk)
    # Two approvals of three: Doorstep is still learning; the preview is the suggestion's maximum.
    assert (lost.status, lost.approvals, lost.needed) == ("learning", 2, cfg.min_precedents)
    assert lost.max_amount_minor == 5800 and lost.rule_id is None
    assert card(desk, DAMAGED).status == "idle" and card(desk, DAMAGED).replay is None
    [gbp] = desk.limits
    eff = effective_limits(rulebook, seller, Currency.GBP)
    assert gbp.auto_approve_minor == eff.auto_approve.amount_minor
    assert gbp.daily_cap_minor == eff.daily_cap.amount_minor
    assert gbp.ceiling_minor == cfg.ceiling(Currency.GBP).amount_minor
    assert gbp.today_minor == 5500 + 5800  # the two approved refunds, queued today
    assert desk.money_limits.id == rulebook.money_limits.id
    assert desk.money_limits.version == rulebook.money_limits.version
    assert desk.window_days == cfg.window_days


def test_AP_10_offered_then_on_card_counts_what_its_rule_refunded(
    db, rulebook, clock, policy, shop
):
    seller, case = shop()
    approve_over_limit(policy, case, [5500, 6000, 5800])
    assert card(autopilot_desk(db, rulebook, seller, now=clock.now())).status == "offered"
    rule = turn_on(db, rulebook, seller, LOST, "GBP", now=clock.now(), actor="seller:test")
    db.commit()
    c, d = decide(policy, case, 5200)
    assert d.codes[-1] == Reason.SELLER_RULE
    db.commit()
    desk = autopilot_desk(db, rulebook, seller, now=clock.now())
    lost = card(desk)
    assert (lost.status, lost.rule_id, lost.max_amount_minor) == ("on", rule.id, 6000)
    assert (lost.handled, lost.handled_minor, lost.today_minor) == (1, 5200, 5200)
    assert lost.last_run is not None
    # AP-11: the run, with the rule that approved it.
    [run] = desk.runs
    assert (run.case_id, run.rule_id, run.amount_minor, run.currency) == (
        c.id,
        rule.id,
        5200,
        "GBP",
    )
    assert run.status == "queued"


def test_AP_10_without_a_seller_rules_block_autopilot_is_off(db, rulebook, clock, shop):
    seller, _ = shop()
    limits = rulebook.money_limits.model_copy(update={"seller_rules": None})
    book = RuleBook(rulebook.liability, limits, rulebook.seller_protection, rulebook.carriers)
    desk = autopilot_desk(db, book, seller, now=clock.now())
    assert desk.enabled is False and desk.cards == []


# ---------------------------------------------------------------- AP-12


def test_AP_12_replay_asks_the_policys_own_matching_rule(db, rulebook, clock, policy, shop):
    seller, case = shop()
    approve_over_limit(policy, case, [5500, 6000, 5800])
    turn_on(db, rulebook, seller, LOST, "GBP", now=clock.now(), actor="seller:test")
    db.commit()
    over_rule = 6500  # over the rule's 6000 maximum, under the ceiling
    decide(policy, case, over_rule)
    db.commit()
    lost = card(autopilot_desk(db, rulebook, seller, now=clock.now()))
    assert lost.replay is not None
    for x in lost.replay.cases:
        assert x.amount_minor is not None
        real = matching_rule(db, rulebook, seller.id, LOST, money(x.amount_minor))
        assert (x.outcome == "rule") == (real is not None)
    by_amount = {x.amount_minor: (x.outcome, x.reason) for x in lost.replay.cases}
    assert by_amount[over_rule] == ("you", "over_max")
    assert by_amount[5500] == ("rule", "rule")


def test_AP_12_a_rule_that_is_off_replays_as_if_it_were_on(db, rulebook, clock, policy, shop):
    seller, case = shop()
    approve_over_limit(policy, case, [5500, 6000, 5800])
    rule = turn_on(db, rulebook, seller, LOST, "GBP", now=clock.now(), actor="seller:test")
    rule.status = "off"
    db.commit()
    lost = card(autopilot_desk(db, rulebook, seller, now=clock.now()))
    assert lost.status == "off" and lost.replay is not None
    assert [x.outcome for x in lost.replay.cases] == ["rule", "rule", "rule"]
    assert db.get(SellerRule, rule.id).status == "off"  # the replay changed nothing


def test_AP_12_holds_disputes_other_kinds_and_small_refunds_keep_their_route(
    db, rulebook, clock, policy, shop
):
    seller, case = shop()
    approve_over_limit(policy, case, [5500, 6000, 5800])
    decide(policy, case, 7000, confidence=Confidence.LOW)  # held for review
    decide(policy, case, 6000, fp=DAMAGED)  # over the limit, another kind
    decide(policy, case, 3000)  # under the auto-approve limit: automatic anyway
    decide(policy, case, 6000, currency="USD")  # another currency
    db.commit()
    lost = card(autopilot_desk(db, rulebook, seller, now=clock.now()))
    assert lost.replay is not None
    got = {(x.amount_minor, x.currency, x.outcome, x.reason) for x in lost.replay.cases}
    assert got == {
        (6000, "USD", "you", "other_kind"),
        (3000, "GBP", "auto", "auto"),
        (6000, "GBP", "you", "other_kind"),
        (7000, "GBP", "you", "held"),
        (5500, "GBP", "rule", "rule"),
        (6000, "GBP", "rule", "rule"),
        (5800, "GBP", "rule", "rule"),
    }


def test_AP_12_the_daily_cap_counts_what_the_rule_would_have_refunded(
    db, rulebook, clock, policy, shop
):
    """Two cases decided the same day, neither approved yet: with the rule on, the first is
    refunded, and the second would then pass the seller's daily cap, so it stays with them."""
    limit = rulebook.money_limits.auto_approve_limit(Currency.GBP).amount_minor
    first, second = limit + 500, limit + 800
    seller, case = shop()
    approve_over_limit(policy, case, [5500, 6000, 5800])
    seller.daily_refund_caps = {"GBP": first + second - 1}  # the seller's own, lower cap
    clock.advance(24 * 3600)  # a new UTC day: those approved refunds no longer count
    decide(policy, case, first)
    clock.advance(60)
    decide(policy, case, second)
    db.commit()
    lost = card(autopilot_desk(db, rulebook, seller, now=clock.now()))
    assert lost.replay is not None
    today = {x.amount_minor: (x.outcome, x.reason) for x in lost.replay.cases[:2]}
    assert today == {first: ("rule", "rule"), second: ("you", "daily_cap")}


# ---------------------------------------------------------------- over HTTP


@pytest.fixture
def api(session_factory, settings, clock) -> TestClient:
    services = Services(
        links=LinkSigner(SECRET, clock),
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


def _token(owner_id, clock, scope="seller"):
    return LinkSigner(SECRET, clock).issue(owner_id, scope=scope)


def test_AP_10_the_desk_is_seller_scoped_and_carries_no_buyer_data(db, clock, policy, shop, api):
    seller, case = shop()
    approve_over_limit(policy, case, [5500, 6000, 5800])
    db.commit()
    url = f"/api/seller/{_token(seller.id, clock)}/automate/autopilot"
    body = api.get(url).json()
    assert body["cards"][0]["status"] == "offered"
    assert len(body["cards"][0]["replay"]["cases"]) == 3
    text = json.dumps(body)
    for secret in (BUYER_NAME, "Secret Road", "PAYER-", "@"):
        assert secret not in text

    stranger = make_seller(db)
    order = make_order(db, stranger)
    db.commit()
    other = api.get(f"/api/seller/{_token(stranger.id, clock)}/automate/autopilot").json()
    assert other["runs"] == [] and all(c["replay"] is None for c in other["cards"])
    assert api.get("/api/seller/not-a-token/automate/autopilot").status_code == 404
    buyer = _token(order.id, clock, scope="buyer")
    assert api.get(f"/api/seller/{buyer}/automate/autopilot").status_code == 404


def test_AP_10_the_desk_never_writes(db, clock, policy, shop, api, session_factory):
    seller, case = shop()
    approve_over_limit(policy, case, [5500, 6000, 5800])
    db.commit()
    before = db.query(SellerRule).count()
    api.get(f"/api/seller/{_token(seller.id, clock)}/automate/autopilot")
    with session_factory() as s:
        assert s.query(SellerRule).count() == before == 0
