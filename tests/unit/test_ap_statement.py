"""AP-07 — the autopilot statement: what the seller's rules refunded in the last 7 days, what can
be claimed back from carriers, and which cases, all computed in code from stored records."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import AppDeps, Services
from app.cases.engine import FailurePoint
from app.main import create_app
from app.orders.enums import ApproverKind, CaseState, MoneyActionStatus, RecoveryStatus
from app.orders.models import Approval, MoneyAction, Recovery
from app.policy.autopilot import STATEMENT_DAYS, statement, turn_on
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
    def build():
        seller = make_seller(db)

        def case(amount: int = 6000):
            order = make_order(
                db,
                seller,
                amount_minor=amount,
                currency="GBP",
                payer_id=f"PAYER-{uuid4().hex[:10]}",
            )
            order.item_description = f"Item {amount}"
            fake_paypal.add_capture(order.paypal_order_id, order.capture_id, money(amount, "GBP"))
            c = make_case(db, order)
            db.commit()
            return order, c

        return seller, case

    return build


def _complete(db, clock, money_action_id, *, ago_s: float = 0):
    ma = db.get(MoneyAction, money_action_id)
    ma.status = MoneyActionStatus.COMPLETED.value
    ma.completed_at = clock.now() - timedelta(seconds=ago_s)
    db.commit()
    return ma


def _autopilot_on(db, rulebook, clock, policy, seller, case):
    """Three over-limit approvals by the seller, then the rule they suggest, turned on."""
    for amount in (5500, 6000, 5800):
        order, c = case(amount)
        policy.decide(c, engine_result(LOST), facts_for(order))
        _complete(db, clock, policy.approve(c, SELLER).id)
    turn_on(db, rulebook, seller, LOST, "GBP", now=clock.now(), actor="seller:test")
    db.commit()


def _by_rule(db, clock, policy, case, amount=5900, *, ago_s: float = 0):
    order, c = case(amount)
    d = policy.decide(c, engine_result(LOST), facts_for(order))
    assert d.route == CaseState.AUTO_REFUND and d.money_action_id
    _complete(db, clock, d.money_action_id, ago_s=ago_s)
    return order, c, d


def _recovery(db, clock, order, case, decision_id, status, claim, recovered=None):
    db.add(
        Recovery(
            case_id=case.id,
            order_id=order.id,
            seller_id=order.seller_id,
            decision_id=decision_id,
            carrier="royal_mail",
            carrier_id="royal_mail",
            failure_point=LOST.value,
            claim_minor=claim,
            currency="GBP",
            status=status.value,
            deadline_at=clock.now() + timedelta(days=60),
            recovered_minor=recovered,
        )
    )
    db.commit()


def test_AP_07_counts_only_refunds_a_seller_rule_made(db, rulebook, clock, policy, shop):
    seller, case = shop()
    _autopilot_on(db, rulebook, clock, policy, seller, case)  # three refunds approved by hand
    order, c, _ = _by_rule(db, clock, policy, case, 5900)
    small, small_case = case(4000)  # the ordinary auto-approve limit, not the rule
    d = policy.decide(small_case, engine_result(LOST), facts_for(small))
    assert d.route == CaseState.AUTO_REFUND and d.money_action_id
    _complete(db, clock, d.money_action_id)

    out = statement(db, seller.id, now=clock.now())
    assert out.days == STATEMENT_DAYS == 7
    [total] = out.totals
    assert (total.currency, total.refunds, total.refunded_minor) == ("GBP", 1, 5900)
    [row] = out.cases
    assert (row.case_id, row.order_id, row.item, row.amount_minor, row.currency) == (
        c.id,
        order.id,
        "Item 5900",
        5900,
        "GBP",
    )
    assert row.order_ref == order.id[:8].upper()


def test_AP_07_only_completed_refunds_in_the_window(db, rulebook, clock, policy, shop):
    seller, case = shop()
    _autopilot_on(db, rulebook, clock, policy, seller, case)
    _by_rule(db, clock, policy, case, 5900, ago_s=(STATEMENT_DAYS * 86400) + 60)  # too old
    order, c = case(5950)
    d = policy.decide(c, engine_result(LOST), facts_for(order))
    assert d.route == CaseState.AUTO_REFUND  # queued at PayPal, not completed yet
    out = statement(db, seller.id, now=clock.now())
    assert out.totals == [] and out.cases == []


def test_AP_07_claims_ready_and_recovered_from_carriers(db, rulebook, clock, policy, shop):
    seller, case = shop()
    _autopilot_on(db, rulebook, clock, policy, seller, case)
    o1, c1, d1 = _by_rule(db, clock, policy, case, 5900)
    o2, c2, d2 = _by_rule(db, clock, policy, case, 5600)
    o3, c3, d3 = _by_rule(db, clock, policy, case, 5200)
    _recovery(
        db,
        clock,
        o1,
        c1,
        db.get(MoneyAction, d1.money_action_id).decision_id,
        RecoveryStatus.PREPARED,
        5900,
    )
    _recovery(
        db,
        clock,
        o2,
        c2,
        db.get(MoneyAction, d2.money_action_id).decision_id,
        RecoveryStatus.SUBMITTED,
        5600,
    )
    _recovery(
        db,
        clock,
        o3,
        c3,
        db.get(MoneyAction, d3.money_action_id).decision_id,
        RecoveryStatus.PAID,
        5200,
        recovered=5000,
    )

    [total] = statement(db, seller.id, now=clock.now()).totals
    assert total.refunds == 3
    assert total.refunded_minor == 5900 + 5600 + 5200
    assert total.claimable_minor == 5900 + 5600  # prepared or submitted
    assert total.ready_to_send == 1  # prepared only
    assert total.recovered_minor == 5000


def test_AP_07_another_sellers_refunds_are_not_in_it(db, rulebook, clock, policy, shop):
    seller, case = shop()
    _autopilot_on(db, rulebook, clock, policy, seller, case)
    _by_rule(db, clock, policy, case, 5900)
    other = make_seller(db)
    db.commit()
    assert statement(db, other.id, now=clock.now()).totals == []


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


def test_AP_07_statement_over_http(session_factory, settings, clock):
    api = _api(session_factory, settings, clock)
    seed = api.post("/demo/seed?scratch=1").json()
    out = api.get(f"/api/seller/{seed['seller_token']}/autopilot/statement")
    assert out.status_code == 200, out.text
    assert out.json() == {"days": 7, "totals": [], "cases": []}  # Maria has no rule on yet
    assert api.get("/api/seller/not-a-token/autopilot/statement").status_code == 404


def test_AP_07_seeded_demo_priya_is_refunded_by_marias_rule(session_factory, settings, clock):
    """The demo story: Maria approves Sam, turns the rule on, and Priya's lost beret is refunded
    by it without waiting for her."""
    api = _api(session_factory, settings, clock)
    seed = api.post("/demo/seed?scratch=1").json()
    token = seed["seller_token"]
    sam = api.post(
        f"/api/buyer/{seed['buyers']['sam-jacket']}/not-here",
        json={"reason": "not_received", "note": ""},
    ).json()
    assert api.post(f"/api/seller/{token}/cases/{sam['case_id']}/approve").status_code == 200
    on = api.post(
        f"/api/seller/{token}/autopilot", json={"failure_point": "CARRIER_LOST", "currency": "GBP"}
    )
    assert on.status_code == 200, on.text
    priya = api.post(
        f"/api/buyer/{seed['buyers']['priya-beret']}/not-here",
        json={"reason": "not_received", "note": ""},
    )
    assert priya.status_code == 200, priya.text
    assert priya.json()["status"] == CaseState.AUTO_REFUND.value
    with session_factory() as s:
        [approval] = s.scalars(
            select(Approval).where(Approval.case_id == priya.json()["case_id"])
        ).all()
        assert approval.approver_id.startswith("seller-rule:")
