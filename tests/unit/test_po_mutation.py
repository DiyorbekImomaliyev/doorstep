"""PO / RL / PR mutation-killing tests (human-approved addendum, ADR-023).

Each test pins one behaviour a surviving mutant changed in app/policy. Grouped in the order the
human approved: daily cap, dispute scope, external refunds, bad-address override, audit fields,
then the remaining logic survivors.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.money import CurrencyMismatch
from app.orders.enums import (
    ApproverKind,
    BuyerType,
    CaseState,
    Fix,
    MoneyActionKind,
    MoneyActionStatus,
    OrderState,
)
from app.orders.models import Approval, AuditLog, Case, Decision, Dispute, Evidence, MoneyAction
from app.orders.state import transition_case
from app.paypal_client.errors import PayPalClientError
from app.paypal_client.types import CaptureState, RefundResult
from app.policy.fingerprint import evidence_fingerprint
from app.policy.rules import RuleValidationError, load_rules
from app.policy.service import (
    REFUND_JOB,
    Approver,
    PolicyService,
    PolicyStateError,
    RefundJobHandler,
)
from app.worker.models import Job
from app.worker.runner import enqueue
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
_buyer = iter(range(10_000))


@pytest.fixture
def scenario(db, fake_paypal):
    """A GB order captured in the fake PayPal, with a case in GATHERING_EVIDENCE."""

    def build(
        amount_minor: int = 4000,
        *,
        seller=None,
        caps: dict[str, int] | None = None,
        payer: str | None = None,
        override: bool = False,
    ):
        seller = seller or make_seller(db, daily_refund_caps=caps)
        order = make_order(
            db, seller, amount_minor=amount_minor, payer_id=payer or f"PAYER-{next(_buyer)}"
        )
        order.address_override = override
        fake_paypal.add_capture(order.paypal_order_id, order.capture_id, money(amount_minor))
        case = make_case(db, order)
        db.commit()
        return seller, order, case

    return build


@pytest.fixture
def policy(db, rulebook, clock):
    return PolicyService(db, rulebook, clock)


@pytest.fixture
def run_refunds(session_factory, fake_paypal, rulebook, clock, settings, db):
    def run(gateway=None, rounds: int = 1) -> None:
        db.commit()
        run_jobs(
            session_factory,
            {REFUND_JOB: RefundJobHandler(gateway or fake_paypal, rulebook, clock)},
            clock,
            settings,
            rounds=rounds,
        )
        db.expire_all()

    return run


def _past_refund(
    db,
    seller,
    minor: int,
    *,
    at: datetime,
    status=MoneyActionStatus.COMPLETED,
    kind: str = "refund",
    external: bool = False,
    currency: str = "GBP",
):
    order = make_order(
        db,
        seller,
        amount_minor=minor,
        currency=currency,
        state=OrderState.RESOLVED,
        payer_id=f"PAYER-{next(_buyer)}",
    )
    case = make_case(db, order, state=CaseState.SETTLED)
    decision = add_decision(db, case, fix=Fix.REFUND, route="AUTO_REFUND")
    ma = add_money_action(db, case, decision, status=status, amount_minor=minor, created_at=at)
    ma.kind = kind
    ma.external = external
    db.flush()
    return ma


def _route(policy, case, order, **facts):
    return policy.decide(case, engine_result(**facts.pop("engine", {})), facts_for(order, **facts))


# ---------------------------------------------------------------- 1. daily cap (PO-08)


def test_PO_08_empty_day_allows_refund_equal_to_cap(policy, scenario):
    _, order, case = scenario(3000, caps={"GBP": 3000, "USD": 3000})
    assert _route(policy, case, order).route == CaseState.AUTO_REFUND  # 0 + 3000 == cap


def test_PO_08_refund_reaching_cap_exactly_auto_refunds(db, policy, scenario, clock):
    seller, order, case = scenario(2000)
    _past_refund(db, seller, 48_000, at=clock.now())
    assert _route(policy, case, order).route == CaseState.AUTO_REFUND  # 48,000 + 2,000 == cap


def test_PO_08_cap_ignores_other_sellers_refunds(db, policy, scenario, clock):
    _, order, case = scenario(3000)
    _past_refund(db, make_seller(db), 48_000, at=clock.now())
    assert _route(policy, case, order).route == CaseState.AUTO_REFUND


def test_PO_08_cap_ignores_external_refunds(db, policy, scenario, clock):
    seller, order, case = scenario(3000)
    _past_refund(db, seller, 48_000, at=clock.now(), external=True)
    assert _route(policy, case, order).route == CaseState.AUTO_REFUND


@pytest.mark.parametrize("status", [MoneyActionStatus.FAILED, MoneyActionStatus.BLOCKED])
def test_PO_08_cap_ignores_failed_and_blocked_refunds(db, policy, scenario, clock, status):
    seller, order, case = scenario(3000)
    _past_refund(db, seller, 48_000, at=clock.now(), status=status)
    assert _route(policy, case, order).route == CaseState.AUTO_REFUND


def test_PO_08_cap_ignores_non_refund_money_actions(db, policy, scenario, clock):
    seller, order, case = scenario(3000)
    _past_refund(db, seller, 48_000, at=clock.now(), kind=MoneyActionKind.EVIDENCE.value)
    assert _route(policy, case, order).route == CaseState.AUTO_REFUND


def test_PO_08_cap_counts_refund_made_at_utc_midnight(db, policy, scenario, clock):
    seller, order, case = scenario(3000)
    midnight = datetime.combine(clock.now().date(), datetime.min.time(), tzinfo=UTC)
    _past_refund(db, seller, 48_000, at=midnight)
    assert _route(policy, case, order).route == CaseState.AWAITING_SELLER


def test_PO_08_cap_excludes_refund_at_next_utc_midnight(db, policy, scenario, clock):
    seller, order, case = scenario(3000)
    tomorrow = datetime.combine(clock.now().date(), datetime.min.time(), tzinfo=UTC)
    _past_refund(db, seller, 48_000, at=tomorrow + timedelta(days=1))
    assert _route(policy, case, order).route == CaseState.AUTO_REFUND


# ---------------------------------------------------------------- 2. dispute scope (PO-05)


def _dispute(db, order, status: str) -> None:
    db.add(Dispute(order_id=order.id, paypal_dispute_id=f"PP-D-{order.id[:8]}", status=status))
    db.flush()


def test_PO_05_dispute_on_another_order_does_not_block_refund(db, policy, scenario):
    seller, other, _ = scenario(4000)
    _dispute(db, other, "open")
    _, order, case = scenario(4000, seller=seller)
    assert _route(policy, case, order).route == CaseState.AUTO_REFUND


def test_PO_05_resolved_dispute_does_not_block_refund(db, policy, scenario):
    _, order, case = scenario(4000)
    _dispute(db, order, "resolved")
    assert _route(policy, case, order).route == CaseState.AUTO_REFUND


def test_PO_05_dispute_opened_on_another_order_does_not_block_queued_refund(
    db, policy, scenario, run_refunds
):
    seller, order, case = scenario(4000)
    _route(policy, case, order)
    _, other, _ = scenario(4000, seller=seller)
    _dispute(db, other, "open")
    run_refunds()
    [ma] = money_actions(db, case_id=case.id)
    assert ma.status == MoneyActionStatus.COMPLETED


# ---------------------------------------------------------------- 3. external refunds (PO-06)


def test_PO_06_external_partial_refund_recorded_as_partial(policy, scenario):
    _, order, _ = scenario(4000)
    ma = policy.record_external_refund(order, money(3999), paypal_refund_id="EXT-P")
    assert ma.kind == MoneyActionKind.PARTIAL_REFUND


def test_PO_06_external_full_refund_recorded_as_full(policy, scenario):
    _, order, _ = scenario(4000)
    ma = policy.record_external_refund(order, money(4000), paypal_refund_id="EXT-F")
    assert ma.kind == MoneyActionKind.REFUND


def test_PO_06_external_refund_record_fields(policy, scenario, clock):
    _, order, _ = scenario(4000)
    ma = policy.record_external_refund(order, money(1000), paypal_refund_id="EXT-R")
    assert ma.external is True
    assert ma.case_id is None
    assert (ma.status, ma.paypal_status) == (MoneyActionStatus.COMPLETED, "COMPLETED")
    assert ma.paypal_refund_id == "EXT-R"
    assert ma.created_at == clock.now()
    assert ma.completed_at == clock.now()


def test_PO_06_partial_external_refund_reduces_auto_refund_amount(db, policy, scenario):
    _, order, case = scenario(4000)
    policy.record_external_refund(order, money(1500), paypal_refund_id="EXT-1")
    decision = _route(policy, case, order)
    assert decision.refund_amount == money(2500)
    stored = db.get(Decision, db.scalars(select(Decision.id)).one())
    assert stored.capture_snapshot["refunded_minor"] == 1500
    assert stored.signals["remaining_refundable_minor"] == 2500


def test_PO_06_dispute_evidence_action_does_not_reduce_refundable(db, policy, scenario):
    _, order, case = scenario(4000)
    decision = add_decision(db, case, fix=Fix.REFUND)
    evidence = add_money_action(db, case, decision, status=MoneyActionStatus.COMPLETED)
    evidence.kind = MoneyActionKind.EVIDENCE.value
    db.flush()
    assert _route(policy, case, order).refund_amount == money(4000)


def test_PO_12_recheck_passes_when_paypal_refunds_match_recorded_ones(
    db, policy, scenario, run_refunds, fake_paypal
):
    """PayPal's refunded total equals the refunds Doorstep knows (here a recorded external one)."""
    _, order, case = scenario(4000)
    ext = fake_paypal.external_refund(order.capture_id, 1500)
    policy.record_external_refund(order, money(1500), paypal_refund_id=ext)
    assert _route(policy, case, order).refund_amount == money(2500)
    run_refunds()
    [ma] = money_actions(db, case_id=case.id)
    assert ma.status == MoneyActionStatus.COMPLETED
    assert fake_paypal.total_refunded(order.capture_id) == 4000


# ---------------------------------------------------------------- 4. bad-address override (FE-11)


def test_FE_11_bad_address_with_buyer_override_goes_to_seller(policy, scenario):
    from app.cases.engine import FailurePoint

    _, order, case = scenario(4000, override=True)
    decision = _route(policy, case, order, engine={"fp": FailurePoint.BAD_ADDRESS})
    assert decision.route == CaseState.AWAITING_SELLER


def test_FE_11_bad_address_without_override_follows_liability(policy, scenario):
    from app.cases.engine import FailurePoint

    _, order, case = scenario(4000, override=False)
    decision = _route(policy, case, order, engine={"fp": FailurePoint.BAD_ADDRESS})
    assert decision.route == CaseState.AUTO_REFUND


def test_FE_11_address_override_alone_does_not_route_to_seller(policy, scenario):
    _, order, case = scenario(4000, override=True)
    assert _route(policy, case, order).route == CaseState.AUTO_REFUND  # CARRIER_LOST


def test_PO_13_pending_capture_holds_for_review(db, policy, scenario):
    _, order, case = scenario(4000)
    order.capture_status = "PENDING"  # capture id present, not completed
    assert _route(policy, case, order).route == CaseState.HOLD_FOR_REVIEW


# ---------------------------------------------------------------- 5. audit fields (PO-09)


def _money_audits(db, ma_id: str) -> dict[str, AuditLog]:
    rows = db.scalars(select(AuditLog).where(AuditLog.entity_id == ma_id)).all()
    return {row.action: row for row in rows}


def test_PO_09_decision_row_records_all_fields(db, policy, scenario, clock):
    _, order, case = scenario(4000)
    db.add(Evidence(order_id=order.id, kind="packing_photo", uri="s3://p.jpg"))
    db.flush()
    result = _route(policy, case, order)
    stored = db.scalars(select(Decision).where(Decision.case_id == case.id)).one()
    assert (stored.failure_point, stored.confidence) == ("CARRIER_LOST", "high")
    assert stored.currency == "GBP"
    assert stored.fix == Fix.REFUND.value
    assert stored.reasons == result.reasons and stored.reasons
    assert stored.explanation == ""
    assert stored.signals["engine"]["failure_point"] == "CARRIER_LOST"
    assert stored.signals["facts"]["buyer_country"] == "GB"
    assert stored.signals["liability"]["rule_id"] == "uk_possession_v1"
    assert stored.evidence_fingerprint == evidence_fingerprint(db, order.id)
    assert stored.capture_snapshot == {
        "capture_id": order.capture_id,
        "status": "COMPLETED",
        "refunded_minor": 0,
    }
    assert stored.created_at == clock.now()
    assert result.money_action_id == money_actions(db, case_id=case.id)[0].id


def test_PO_09_decision_without_rule_records_no_liability(db, policy, scenario):
    _, order, case = scenario(4000)
    result = policy.decide(case, engine_result(), facts_for(order, country="JP"))
    stored = db.scalars(select(Decision).where(Decision.case_id == case.id)).one()
    assert stored.signals["liability"] is None
    assert result.money_action_id is None


def test_PO_09_reship_decision_records_no_refund_amount(db, policy, scenario):
    _, order, case = scenario(4000)
    result = _route(policy, case, order, fix=Fix.RESHIP)
    stored = db.scalars(select(Decision).where(Decision.case_id == case.id)).one()
    assert result.refund_amount is None
    assert stored.refund_amount_minor is None
    assert result.money_action_id is None


def test_PO_09_approvals_record_rule_and_time(db, policy, scenario, clock):
    _, order, case = scenario(8000)  # over the auto-approve limit
    _route(policy, case, order)
    clock.advance(60)
    policy.approve(case, SELLER)
    [approval] = db.scalars(select(Approval).where(Approval.case_id == case.id)).all()
    assert approval.rule_id == "uk_possession_v1"
    assert approval.at == clock.now()


def test_PO_09_reship_approval_records_rule(db, policy, scenario):
    _, order, case = scenario(8000)
    _route(policy, case, order, fix=Fix.RESHIP)
    policy.approve(case, SELLER)
    [approval] = db.scalars(select(Approval).where(Approval.case_id == case.id)).all()
    assert approval.rule_id == "uk_possession_v1"


def test_PO_09_money_audit_rows_name_the_approver(db, policy, scenario, run_refunds):
    _, order, case = scenario(4000)
    _route(policy, case, order)
    run_refunds()
    [ma] = money_actions(db, case_id=case.id)
    audits = _money_audits(db, ma.id)
    for action in ("refund.queued", "refund.completed"):
        row = audits[action]
        assert row.after["approver"] == {"kind": "system", "id": "policy"}, action
        assert row.before is not None and row.at is not None, action


def test_PO_09_failed_refund_audit_names_the_approver(
    db, policy, scenario, run_refunds, fake_paypal, settings
):
    _, order, case = scenario(8000)
    _route(policy, case, order)
    policy.approve(case, SELLER)
    fake_paypal.fail_next_refunds(PayPalClientError(422, "UNPROCESSABLE_ENTITY"))
    run_refunds()
    [ma] = money_actions(db, case_id=case.id)
    row = _money_audits(db, ma.id)["refund.failed"]
    assert row.after["approver"] == {"kind": "seller", "id": "seller-1"}
    assert row.before == {"status": "queued"}


def test_PO_09_blocked_refund_audit_names_the_approver(
    db, policy, scenario, run_refunds, fake_paypal
):
    _, order, case = scenario(4000)
    _route(policy, case, order)
    fake_paypal.captures[order.capture_id].amount_minor = 3000
    run_refunds()
    [ma] = money_actions(db, case_id=case.id)
    assert _money_audits(db, ma.id)["refund.blocked"].after["approver"]["kind"] == "system"


def test_PO_09_money_action_has_creation_time_and_deduped_job(db, policy, scenario, clock):
    _, order, case = scenario(4000)
    _route(policy, case, order)
    [ma] = money_actions(db, case_id=case.id)
    [job] = db.scalars(select(Job).where(Job.kind == REFUND_JOB)).all()
    assert ma.created_at == clock.now()
    assert job.dedupe_key == f"refund:{ma.id}"


def test_PO_09_audit_rows_carry_timestamp(db, policy, scenario, clock):
    _, order, case = scenario(4000)
    _route(policy, case, order)
    [ma] = money_actions(db, case_id=case.id)
    assert _money_audits(db, ma.id)["refund.queued"].at == clock.now()


# ---------------------------------------------------------------- 6. remaining logic survivors


def test_PO_12_refund_failure_on_held_case_keeps_it_held(
    db, policy, scenario, run_refunds, fake_paypal
):
    from app.cases.engine import Confidence

    _, order, case = scenario(4000)
    assert _route(policy, case, order, engine={"confidence": Confidence.LOW}).route == (
        CaseState.HOLD_FOR_REVIEW
    )
    policy.approve(case, REVIEWER)  # the refund is queued from HOLD_FOR_REVIEW
    fake_paypal.fail_next_refunds(PayPalClientError(422, "UNPROCESSABLE_ENTITY"))
    run_refunds()
    [ma] = money_actions(db, case_id=case.id)
    assert ma.status == MoneyActionStatus.FAILED
    assert db.get(Case, case.id).state == CaseState.HOLD_FOR_REVIEW


def test_PO_09_latest_decision_is_newest_not_highest_id(db, policy, scenario, clock):
    _, _, case = scenario(8000)
    transition_case(db, case, CaseState.DECIDED, actor="test")
    transition_case(db, case, CaseState.AWAITING_SELLER, actor="test")
    older = Decision(
        id="zzzz-older",
        case_id=case.id,
        route="AWAITING_SELLER",
        fix=Fix.REFUND.value,
        refund_amount_minor=8000,
        rule_id="uk_possession_v1",
        created_at=clock.now() - timedelta(minutes=5),
    )
    newer = Decision(
        id="aaaa-newer",
        case_id=case.id,
        route="AWAITING_SELLER",
        fix=Fix.RESHIP.value,
        rule_id="uk_possession_v1",
        created_at=clock.now(),
    )
    db.add_all([older, newer])
    db.flush()
    assert policy.approve(case, SELLER) is None  # acts on the newer reship decision
    assert case.state == CaseState.SETTLED


def test_PO_04_claim_exactly_at_window_start_counts(db, policy, scenario, clock):
    seller, _, first = scenario(4000, payer="PAYER-R")
    first.opened_at = clock.now() - timedelta(days=90)
    scenario(4000, payer="PAYER-R", seller=seller)
    _, order, case = scenario(4000, payer="PAYER-R", seller=seller)
    assert _route(policy, case, order).route == CaseState.HOLD_FOR_REVIEW


def test_PO_12_refund_job_with_missing_case_fails_and_logs_error(
    db, policy, scenario, run_refunds, fake_paypal, caplog
):
    caplog.set_level(logging.ERROR, logger="doorstep.policy")
    _, order, case = scenario(4000)
    _route(policy, case, order)
    [ma] = money_actions(db, case_id=case.id)
    ma.case_id = "gone"
    run_refunds()
    [job] = db.scalars(select(Job).where(Job.kind == REFUND_JOB)).all()
    assert (job.status, job.attempts) == ("failed", 1)
    assert db.get(MoneyAction, ma.id).status == MoneyActionStatus.FAILED
    assert "MissingRecord" in str(db.get(MoneyAction, ma.id).paypal_response)
    assert fake_paypal.refund_calls == []
    assert any(r.levelno == logging.ERROR for r in caplog.records)


@pytest.mark.parametrize("column", ["case_id", "decision_id"])
def test_PO_12_refund_job_for_money_action_without_reference_fails(
    db, policy, scenario, run_refunds, fake_paypal, column
):
    _, order, case = scenario(4000)
    _route(policy, case, order)
    [ma] = money_actions(db, case_id=case.id)
    setattr(ma, column, None)
    run_refunds()
    [job] = db.scalars(select(Job).where(Job.kind == REFUND_JOB)).all()
    assert (job.status, job.attempts) == ("failed", 1)
    assert fake_paypal.refund_calls == []


@pytest.mark.xfail(strict=True, reason="ADR-021: payload without money_action_id is retried")
def test_PO_12_orphaned_refund_job_without_payload_id_fails_and_logs_error(
    db, clock, run_refunds, caplog
):
    caplog.set_level(logging.ERROR, logger="doorstep.policy")
    enqueue(db, REFUND_JOB, {}, clock=clock)
    run_refunds()
    [job] = db.scalars(select(Job).where(Job.kind == REFUND_JOB)).all()
    assert (job.status, job.attempts) == ("failed", 1)
    assert any("orphaned" in r.getMessage() for r in caplog.records)


class _MixedCurrencyGateway:
    """PayPal reports the capture in GBP but its refunded total in USD."""

    def __init__(self) -> None:
        self.refunds: list[str] = []

    def get_capture_state(self, paypal_order_id: str, capture_id: str) -> CaptureState:
        return CaptureState(
            capture_id=capture_id,
            status="COMPLETED",
            amount=money(4000),
            refunded_total=money(0, "USD"),
        )

    def refund_capture(
        self, capture_id, *, amount, request_id, **_: object
    ) -> RefundResult:  # pragma: no cover
        self.refunds.append(request_id)
        raise AssertionError("must not refund")


def test_PO_12_recheck_blocks_when_refunded_total_currency_differs(
    db, policy, scenario, run_refunds
):
    _, order, case = scenario(4000)
    _route(policy, case, order)
    gateway = _MixedCurrencyGateway()
    run_refunds(gateway)
    [ma] = money_actions(db, case_id=case.id)
    assert ma.status == MoneyActionStatus.BLOCKED
    assert gateway.refunds == []


def test_PR_04_evidence_fingerprint_matches_golden_value(db, scenario):
    """Fingerprints are stored on decisions; a format change would block every pending refund."""
    _, order, _ = scenario(4000)
    db.add_all(
        [
            Evidence(
                id="ev-2", order_id=order.id, kind="signature", uri="s3://b/2.png", ai_result={}
            ),
            Evidence(
                id="ev-1",
                order_id=order.id,
                kind="packing_photo",
                uri="s3://b/1.jpg",
                ai_result={"matches": True},
            ),
        ]
    )
    db.flush()
    assert evidence_fingerprint(db, order.id) == (
        "10898e17fba7f30eba46ebed5c7ff6ba410fc30c2447cd4d3d78e7c9850ff7ca"
    )


def test_RL_03_business_buyer_has_no_rule_set_and_holds(policy, scenario):
    from app.policy.service import CaseFacts

    _, _order, case = scenario(4000)
    facts = CaseFacts(
        buyer_country="GB",
        buyer_type=BuyerType.BUSINESS,
        proofs=frozenset(),
        claim_amount=money(4000),
        preferred_fix=Fix.REFUND,
    )
    assert policy.decide(case, engine_result(), facts).route == CaseState.HOLD_FOR_REVIEW


def test_PO_13_decide_rejects_case_without_seller(db, policy, scenario):
    _, order, case = scenario(4000)
    case.seller_id = "missing"
    with pytest.raises(Exception, match="order or seller missing"):
        policy.decide(case, engine_result(), facts_for(order))


def test_PO_10_decide_names_both_currencies_on_mismatch(policy, scenario):
    _, order, case = scenario(4000)
    facts = facts_for(order)
    facts = facts.model_copy(update={"claim_amount": money(4000, "USD")})
    with pytest.raises(CurrencyMismatch, match=r"USD.*GBP"):
        policy.decide(case, engine_result(), facts)


def test_PO_10_external_refund_names_both_currencies_on_mismatch(policy, scenario):
    _, order, _ = scenario(4000)
    with pytest.raises(CurrencyMismatch, match=r"USD.*GBP"):
        policy.record_external_refund(order, money(100, "USD"), paypal_refund_id="EXT-X")


def test_PO_03_decline_after_approval_explains_why(policy, scenario):
    _, order, case = scenario(8000)
    _route(policy, case, order)
    policy.approve(case, SELLER)
    with pytest.raises(PolicyStateError, match="refund already approved"):
        policy.decline(case, SELLER)


def test_PP_07_rest_gateway_refund_passes_amount_and_request_id(httpserver):
    from app.paypal_client.client import PayPalClient, PayPalConfig
    from app.policy.gateway import RestRefundGateway

    httpserver.expect_request("/v1/oauth2/token", method="POST").respond_with_json(
        {"access_token": "T", "token_type": "Bearer", "expires_in": 32400}
    )
    httpserver.expect_request(
        "/v2/payments/captures/CAP-9/refund", method="POST"
    ).respond_with_json({"id": "R-9", "status": "COMPLETED"}, status=201)
    client = PayPalClient(
        PayPalConfig(
            client_id="c",
            client_secret="s",
            base_url=httpserver.url_for("").rstrip("/"),
            timeout_s=2,
        ),
        FakeClock(),
    )
    result = RestRefundGateway(client).refund_capture(
        "CAP-9", amount=money(1500), request_id="case:c9:partial_refund:m9"
    )
    [sent] = [r for r, _ in httpserver.log if r.path.endswith("/refund")]
    assert result.refund_id == "R-9"
    assert sent.headers["PayPal-Request-Id"] == "case:c9:partial_refund:m9"
    assert '"value": "15.00"' in sent.get_data(as_text=True).replace('":"', '": "')


def test_RL_01_non_string_kind_fails_validation(rules_copy):
    path = rules_copy / "uk_possession_v1.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("kind: liability", "kind: [x]"), encoding="utf-8"
    )
    with pytest.raises(RuleValidationError, match="unknown kind"):
        load_rules(rules_copy)


@pytest.mark.parametrize("field", ["auto_approve_limit_minor", "daily_refund_cap_minor"])
def test_RL_11_missing_currency_error_names_the_field(rules_copy, field):
    import yaml

    path = rules_copy / "money_limits_v1.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data[field].pop("USD")
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(RuleValidationError, match=field):
        load_rules(rules_copy)


def test_RL_03_overlap_error_names_both_rule_sets(rules_copy):
    import yaml

    path = rules_copy / "eu_possession_v1.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["applies_if"]["buyer_country"] = [*data["applies_if"]["buyer_country"], "GB"]
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    both = r"(eu_possession_v1.*uk_possession_v1|uk_possession_v1.*eu_possession_v1)"
    with pytest.raises(RuleValidationError, match=both):
        load_rules(rules_copy)


def test_RL_01_missing_seller_protection_file_error_names_the_kind(rules_copy):
    (rules_copy / "paypal_sp_v1.yaml").unlink()
    with pytest.raises(RuleValidationError, match="seller_protection"):
        load_rules(rules_copy)
