"""PO — Policy service: the only path to refunds (hard rule 1, SPEC section 7)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.cases.engine import Confidence, FailurePoint
from app.orders.enums import ApproverKind, CaseState, Fix, MoneyActionStatus, OrderState
from app.orders.models import Approval, AuditLog, Case, Dispute, Evidence, MoneyAction, Order
from app.paypal_client.errors import PayPalClientError, PayPalServerError, PayPalTimeout
from app.paypal_client.idempotency import request_id_for
from app.policy.service import REFUND_JOB, Approver, PolicyService, RefundJobHandler
from app.worker.models import Job
from tests.factories import (
    add_decision,
    add_money_action,
    make_case,
    make_order,
    make_seller,
    money_actions,
)
from tests.fakes import FakeClock
from tests.helpers import engine_result, facts_for, money, run_jobs

SELLER = Approver(kind=ApproverKind.SELLER, id="seller-1")
REVIEWER = Approver(kind=ApproverKind.REVIEWER, id="reviewer-1")


@pytest.fixture
def scenario(db, fake_paypal):
    """Factory: a GB order (captured in the fake PayPal) with a case in GATHERING_EVIDENCE."""

    def build(
        amount_minor: int = 4000,
        *,
        currency: str = "GBP",
        country: str = "GB",
        limits: dict[str, int] | None = None,
        caps: dict[str, int] | None = None,
        payer: str = "PAYER-1",
        seller=None,
    ):
        seller = seller or make_seller(
            db,
            region=country if country in ("GB", "US") else "GB",
            auto_approve_limits=limits,
            daily_refund_caps=caps,
        )
        order = make_order(
            db,
            seller,
            amount_minor=amount_minor,
            currency=currency,
            buyer_country=country,
            payer_id=payer,
        )
        fake_paypal.add_capture(
            order.paypal_order_id, order.capture_id, money(amount_minor, currency)
        )
        case = make_case(db, order)
        db.commit()
        return seller, order, case

    return build


@pytest.fixture
def policy(db, rulebook, clock):
    return PolicyService(db, rulebook, clock)


@pytest.fixture
def run_refunds(session_factory, fake_paypal, rulebook, clock, settings, db):
    def run(rounds: int = 1, advance_s: float = 0) -> None:
        db.commit()
        run_jobs(
            session_factory,
            {REFUND_JOB: RefundJobHandler(fake_paypal, rulebook, clock)},
            clock,
            settings,
            rounds=rounds,
            advance_s=advance_s,
        )
        db.expire_all()

    return run


def refund_jobs(db) -> list[Job]:
    return list(db.scalars(select(Job).where(Job.kind == REFUND_JOB)))


def reload(db, case: Case) -> Case:
    db.expire_all()
    found = db.get(Case, case.id)
    assert found is not None
    return found


# ---------------------------------------------------------------- PO-02


def test_PO_02_liable_under_limit_creates_refund_job(db, policy, scenario):
    _, order, case = scenario(4000)
    decision = policy.decide(case, engine_result(), facts_for(order))
    assert decision.route == CaseState.AUTO_REFUND
    assert case.state == CaseState.AUTO_REFUND
    [ma] = money_actions(db, case_id=case.id)
    assert ma.status == MoneyActionStatus.QUEUED
    assert (ma.amount_minor, ma.currency) == (4000, "GBP")
    assert ma.request_id == request_id_for("case", case.id, ma.kind, ma.id)
    [job] = refund_jobs(db)
    assert job.payload == {"money_action_id": ma.id}
    approval = db.scalars(select(Approval).where(Approval.case_id == case.id)).one()
    assert approval.approver_kind == ApproverKind.SYSTEM
    assert approval.rule_id == "uk_possession_v1"


def test_PO_02_amount_equal_to_limit_auto_refunds(policy, scenario):
    _, order, case = scenario(5000)  # default GBP limit is 5000 (ADR-004: at or under)
    assert policy.decide(case, engine_result(), facts_for(order)).route == CaseState.AUTO_REFUND


def test_PO_02_refund_job_executes_and_settles_case(db, policy, scenario, run_refunds, fake_paypal):
    _, order, case = scenario(4000)
    policy.decide(case, engine_result(), facts_for(order))
    run_refunds()
    [ma] = money_actions(db, case_id=case.id)
    assert ma.status == MoneyActionStatus.COMPLETED
    assert ma.paypal_refund_id
    assert fake_paypal.total_refunded(order.capture_id) == 4000
    assert reload(db, case).state == CaseState.SETTLED
    assert db.get(Order, order.id).state == OrderState.RESOLVED


# ---------------------------------------------------------------- PO-03


def test_PO_03_over_limit_awaits_seller(db, policy, scenario):
    _, order, case = scenario(5001)
    assert policy.decide(case, engine_result(), facts_for(order)).route == CaseState.AWAITING_SELLER
    assert money_actions(db, case_id=case.id) == []
    assert refund_jobs(db) == []


def test_PO_03_seller_approval_creates_refund_job(db, policy, scenario):
    _, order, case = scenario(8000)
    policy.decide(case, engine_result(), facts_for(order))
    ma = policy.approve(case, SELLER)
    assert ma is not None and ma.status == MoneyActionStatus.QUEUED
    assert len(refund_jobs(db)) == 1
    kinds = {
        a.approver_kind for a in db.scalars(select(Approval).where(Approval.case_id == case.id))
    }
    assert kinds == {ApproverKind.SELLER}


def test_PO_03_seller_decline_closes_case_without_refund(db, policy, scenario):
    _, order, case = scenario(8000)
    policy.decide(case, engine_result(), facts_for(order))
    policy.decline(case, SELLER)
    assert case.state == CaseState.CLOSED
    assert db.get(Order, order.id).state == OrderState.RESOLVED
    assert money_actions(db, case_id=case.id) == []


# ---------------------------------------------------------------- PO-04


def _prior_case(db, seller, *, payer: str, days_ago: int, clock: FakeClock) -> None:
    order = make_order(db, seller, payer_id=payer, state=OrderState.RESOLVED)
    make_case(db, order, state=CaseState.CLOSED, opened_at=clock.now() - timedelta(days=days_ago))


def test_PO_04_third_claim_in_90_days_holds_for_review(db, policy, scenario, clock):
    seller, order, case = scenario(3000, payer="PAYER-R")
    _prior_case(db, seller, payer="PAYER-R", days_ago=10, clock=clock)
    _prior_case(db, seller, payer="PAYER-R", days_ago=89, clock=clock)
    db.commit()
    decision = policy.decide(case, engine_result(), facts_for(order))
    assert decision.route == CaseState.HOLD_FOR_REVIEW
    assert case.state == CaseState.HOLD_FOR_REVIEW  # review, never CLOSED (auto-deny)
    assert money_actions(db, case_id=case.id) == []


def test_PO_04_claims_older_than_90_days_or_other_sellers_not_counted(db, policy, scenario, clock):
    seller, order, case = scenario(3000, payer="PAYER-R")
    _prior_case(db, seller, payer="PAYER-R", days_ago=91, clock=clock)
    _prior_case(db, make_seller(db), payer="PAYER-R", days_ago=5, clock=clock)
    _prior_case(db, seller, payer="PAYER-OTHER", days_ago=5, clock=clock)
    _prior_case(db, seller, payer="PAYER-R", days_ago=5, clock=clock)
    db.commit()
    assert policy.decide(case, engine_result(), facts_for(order)).route == CaseState.AUTO_REFUND


# ---------------------------------------------------------------- PO-05


def test_PO_05_open_dispute_routes_to_dispute_evidence_without_refund(db, policy, scenario):
    _, order, case = scenario(3000)
    db.add(Dispute(order_id=order.id, paypal_dispute_id="PP-D-1", status="open"))
    db.commit()
    decision = policy.decide(case, engine_result(), facts_for(order))
    assert decision.route == CaseState.DISPUTE_EVIDENCE
    assert money_actions(db, case_id=case.id) == []
    assert refund_jobs(db) == []


def test_PO_05_dispute_opened_after_decision_blocks_refund(
    db, policy, scenario, run_refunds, fake_paypal
):
    _, order, case = scenario(3000)
    policy.decide(case, engine_result(), facts_for(order))
    db.add(Dispute(order_id=order.id, paypal_dispute_id="PP-D-2", status="open"))
    run_refunds()
    assert fake_paypal.refund_calls == []
    [ma] = money_actions(db, case_id=case.id)
    assert ma.status == MoneyActionStatus.BLOCKED
    assert reload(db, case).state == CaseState.DISPUTE_EVIDENCE


# ---------------------------------------------------------------- PO-06 (property: tests/property)


def test_PO_06_external_refund_counts_toward_total(db, policy, scenario, run_refunds, fake_paypal):
    _, order, case = scenario(6000, limits={"GBP": 10_000})
    ext = fake_paypal.external_refund(order.capture_id, 4000)
    policy.record_external_refund(order, money(4000), paypal_refund_id=ext)
    db.commit()
    policy.decide(case, engine_result(), facts_for(order, 6000))
    if case.state == CaseState.HOLD_FOR_REVIEW:
        policy.approve(case, REVIEWER)
    run_refunds()
    assert fake_paypal.total_refunded(order.capture_id) <= 6000


# ---------------------------------------------------------------- PO-07


def test_PO_07_approving_twice_creates_one_refund(db, policy, scenario):
    _, order, case = scenario(8000)
    policy.decide(case, engine_result(), facts_for(order))
    first = policy.approve(case, SELLER)
    second = policy.approve(case, SELLER)
    assert first is not None and second is not None and first.id == second.id
    assert len(money_actions(db, case_id=case.id)) == 1
    assert len(refund_jobs(db)) == 1


def test_PO_07_rerunning_refund_job_sends_one_refund(
    db, policy, scenario, run_refunds, fake_paypal
):
    _, order, case = scenario(3000)
    policy.decide(case, engine_result(), facts_for(order))
    run_refunds()
    [job] = refund_jobs(db)
    job.status, job.attempts = "queued", 0  # simulate a redelivered job
    run_refunds()
    assert fake_paypal.total_refunded(order.capture_id) == 3000
    assert len({rid for _, _, rid in fake_paypal.refund_calls}) == 1


def test_PO_07_crash_after_paypal_success_retries_with_same_key(
    db, policy, scenario, run_refunds, fake_paypal
):
    _, order, case = scenario(3000)
    policy.decide(case, engine_result(), facts_for(order))
    [ma] = money_actions(db, case_id=case.id)
    # PayPal already applied this refund but our commit was lost: the money_action is still queued.
    fake_paypal.refund_capture(order.capture_id, amount=money(3000), request_id=ma.request_id)
    run_refunds()
    assert fake_paypal.total_refunded(order.capture_id) == 3000
    assert {rid for _, _, rid in fake_paypal.refund_calls} == {ma.request_id}


# ---------------------------------------------------------------- PO-08


def _todays_refund(db, seller, *, minor: int, status: str, at: datetime, currency: str = "GBP"):
    # Each earlier refund belongs to a different buyer, so the PO-04 repeat-claimant hold never
    # fires and only the daily cap decides the route.
    order = make_order(
        db,
        seller,
        amount_minor=minor,
        currency=currency,
        state=OrderState.RESOLVED,
        payer_id=f"PAYER-CAP-{uuid4().hex[:8]}",
    )
    case = make_case(db, order, state=CaseState.SETTLED)
    decision = add_decision(db, case, fix=Fix.REFUND, route="AUTO_REFUND")
    add_money_action(
        db, case, decision, status=status, amount_minor=minor, created_at=at, currency=currency
    )


def test_PO_08_daily_cap_routes_to_awaiting_seller(db, policy, scenario, clock):
    seller, order, case = scenario(3000)
    _todays_refund(db, seller, minor=30_000, status=MoneyActionStatus.QUEUED, at=clock.now())
    _todays_refund(db, seller, minor=18_000, status=MoneyActionStatus.COMPLETED, at=clock.now())
    db.commit()
    decision = policy.decide(case, engine_result(), facts_for(order))
    assert decision.route == CaseState.AWAITING_SELLER  # 48,000 + 3,000 > 50,000 cap
    assert money_actions(db, case_id=case.id) == []


@pytest.mark.parametrize("status", [MoneyActionStatus.QUEUED, MoneyActionStatus.COMPLETED])
def test_PO_08_daily_cap_counts_queued_and_completed(db, policy, scenario, clock, status):
    seller, order, case = scenario(3000)
    _todays_refund(db, seller, minor=48_000, status=status, at=clock.now())
    db.commit()
    assert policy.decide(case, engine_result(), facts_for(order)).route == CaseState.AWAITING_SELLER


def test_PO_08_daily_cap_resets_at_utc_midnight(db, rulebook, scenario):
    clock = FakeClock(datetime(2026, 11, 5, 0, 30, tzinfo=UTC))
    seller, order, case = scenario(3000)
    _todays_refund(
        db,
        seller,
        minor=48_000,
        status=MoneyActionStatus.COMPLETED,
        at=datetime(2026, 11, 4, 23, 30, tzinfo=UTC),
    )
    db.commit()
    decision = PolicyService(db, rulebook, clock).decide(case, engine_result(), facts_for(order))
    assert decision.route == CaseState.AUTO_REFUND


def test_PO_08_daily_cap_is_per_currency(db, policy, scenario, clock):
    seller, order, case = scenario(3000)
    _todays_refund(
        db, seller, minor=48_000, status=MoneyActionStatus.COMPLETED, at=clock.now(), currency="USD"
    )
    db.commit()
    assert policy.decide(case, engine_result(), facts_for(order)).route == CaseState.AUTO_REFUND


# ---------------------------------------------------------------- PO-09


def test_PO_09_money_action_writes_full_audit_record(db, policy, scenario, run_refunds):
    _, order, case = scenario(3000)
    policy.decide(
        case,
        engine_result(),
        facts_for(order),
        model_outputs={"explainer": {"text": "Carrier lost the parcel."}},
    )
    run_refunds()
    [ma] = money_actions(db, case_id=case.id)
    rows = list(
        db.scalars(
            select(AuditLog).where(
                AuditLog.entity_type == "money_action", AuditLog.entity_id == ma.id
            )
        )
    )
    assert rows, "no audit record for the money action"
    final = rows[-1].after
    for key in (
        "signals",
        "rule_id",
        "rule_version",
        "model_outputs",
        "approver",
        "paypal_response",
    ):
        assert key in final, f"audit record missing {key}"
    assert final["rule_id"] == "uk_possession_v1"
    assert final["model_outputs"] == {"explainer": {"text": "Carrier lost the parcel."}}
    assert final["approver"]["kind"] == ApproverKind.SYSTEM
    assert final["paypal_response"]


# ---------------------------------------------------------------- PO-10 (policy side)


def test_PO_10_limits_applied_in_order_currency(db, policy, scenario):
    _, order, case = scenario(
        2000, currency="USD", country="US", limits={"GBP": 100_000, "USD": 1000}
    )
    decision = policy.decide(case, engine_result(), facts_for(order, proofs=frozenset()))
    assert decision.route == CaseState.AWAITING_SELLER


def test_PO_10_claim_in_other_currency_is_rejected(db, policy, scenario):
    from app.money import CurrencyMismatch

    _, order, case = scenario(2000)
    facts = facts_for(order)
    facts = facts.model_copy(update={"claim_amount": money(2000, "USD")})
    with pytest.raises(CurrencyMismatch):
        policy.decide(case, engine_result(), facts)


# ---------------------------------------------------------------- PO-11


def test_PO_11_reship_fix_settles_without_money_action(db, policy, scenario):
    _, order, case = scenario(3000)
    decision = policy.decide(case, engine_result(), facts_for(order, fix=Fix.RESHIP))
    assert decision.fix == Fix.RESHIP
    assert decision.route == CaseState.AWAITING_SELLER
    policy.settle_with_reship(case, SELLER)
    assert case.state == CaseState.SETTLED
    assert db.get(Order, order.id).state == OrderState.RESOLVED
    assert money_actions(db, order_id=order.id) == []
    assert db.scalars(select(Approval).where(Approval.case_id == case.id)).first() is not None


# ---------------------------------------------------------------- PO-12


def test_PO_12_refund_5xx_and_timeout_retried_with_same_key_then_failed_and_held(
    db, policy, scenario, run_refunds, fake_paypal, settings
):
    _, order, case = scenario(3000)
    policy.decide(case, engine_result(), facts_for(order))
    fake_paypal.fail_next_refunds(
        PayPalTimeout(), PayPalServerError(503), PayPalServerError(500), PayPalServerError(503)
    )
    run_refunds(rounds=settings.max_attempts + 2, advance_s=10_000)
    assert len(fake_paypal.refund_calls) == settings.max_attempts
    assert len({rid for _, _, rid in fake_paypal.refund_calls}) == 1
    [ma] = money_actions(db, case_id=case.id)
    assert ma.status == MoneyActionStatus.FAILED
    assert reload(db, case).state == CaseState.HOLD_FOR_REVIEW


def test_PO_12_refund_succeeds_on_retry_before_max_attempts(
    db, policy, scenario, run_refunds, fake_paypal
):
    _, order, case = scenario(3000)
    policy.decide(case, engine_result(), facts_for(order))
    fake_paypal.fail_next_refunds(PayPalServerError(503))
    run_refunds(rounds=3, advance_s=10_000)
    [ma] = money_actions(db, case_id=case.id)
    assert ma.status == MoneyActionStatus.COMPLETED
    assert reload(db, case).state == CaseState.SETTLED


def test_PO_12_refund_4xx_fails_at_once_and_holds(db, policy, scenario, run_refunds, fake_paypal):
    _, order, case = scenario(3000)
    policy.decide(case, engine_result(), facts_for(order))
    fake_paypal.fail_next_refunds(PayPalClientError(422, "UNPROCESSABLE_ENTITY"))
    run_refunds(rounds=3, advance_s=10_000)
    assert len(fake_paypal.refund_calls) == 1
    [ma] = money_actions(db, case_id=case.id)
    assert ma.status == MoneyActionStatus.FAILED
    assert reload(db, case).state == CaseState.HOLD_FOR_REVIEW


# ---------------------------------------------------------------- PO-13


def test_PO_13_dispute_takes_precedence_over_hold(db, policy, scenario, clock):
    seller, order, case = scenario(3000, payer="PAYER-R")
    for days in (3, 4):
        _prior_case(db, seller, payer="PAYER-R", days_ago=days, clock=clock)
    db.add(Dispute(order_id=order.id, paypal_dispute_id="PP-D-3", status="open"))
    db.commit()
    result = engine_result(FailurePoint.CLAIM_CONTRADICTS_EVIDENCE, contradiction=True)
    assert policy.decide(case, result, facts_for(order)).route == CaseState.DISPUTE_EVIDENCE


def test_PO_13_hold_takes_precedence_over_auto_refund(db, policy, scenario):
    _, order, case = scenario(1000)
    decision = policy.decide(case, engine_result(confidence=Confidence.LOW), facts_for(order))
    assert decision.route == CaseState.HOLD_FOR_REVIEW


def test_PO_13_no_rule_set_takes_precedence_over_liability(db, policy, scenario):
    _, order, case = scenario(1000, currency="USD", country="CA")
    assert policy.decide(case, engine_result(), facts_for(order)).route == CaseState.HOLD_FOR_REVIEW


def test_PO_13_cap_checked_before_limit(db, policy, scenario, clock):
    _seller, order, case = scenario(3000, caps={"GBP": 2000})  # under the 5000 limit, over the cap
    assert policy.decide(case, engine_result(), facts_for(order)).route == CaseState.AWAITING_SELLER


def test_PO_13_pre_refund_recheck_blocks_on_capture_change(
    db, policy, scenario, run_refunds, fake_paypal
):
    _, order, case = scenario(3000)
    policy.decide(case, engine_result(), facts_for(order))
    fake_paypal.external_refund(order.capture_id, 1000)  # PayPal changed; Doorstep not told yet
    run_refunds()
    assert fake_paypal.refund_calls == []
    [ma] = money_actions(db, case_id=case.id)
    assert ma.status == MoneyActionStatus.BLOCKED
    assert reload(db, case).state == CaseState.HOLD_FOR_REVIEW


def test_PO_13_pre_refund_recheck_blocks_on_evidence_change(
    db, policy, scenario, run_refunds, fake_paypal
):
    _, order, case = scenario(3000)
    policy.decide(case, engine_result(), facts_for(order))
    db.add(Evidence(order_id=order.id, kind="buyer_photo", uri="s3://late-photo.jpg"))
    run_refunds()
    assert fake_paypal.refund_calls == []
    assert reload(db, case).state == CaseState.HOLD_FOR_REVIEW


def test_PO_13_never_refunds_an_order_without_capture(db, policy, fake_paypal):
    seller = make_seller(db)
    order = make_order(db, seller, amount_minor=1000, capture_id=None, capture_status=None)
    case = make_case(db, order)
    db.commit()
    decision = policy.decide(case, engine_result(), facts_for(order))
    assert decision.route == CaseState.HOLD_FOR_REVIEW
    assert db.scalars(select(MoneyAction)).all() == []
