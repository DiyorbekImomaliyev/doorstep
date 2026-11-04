"""PO / RL error paths: every refusal in app/policy is exercised (approved coverage addendum).

Each test names the acceptance criterion whose guard it proves. Nothing here weakens a locked test;
these only add cases the locked suite does not reach.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from pytest_httpserver import HTTPServer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.money import CurrencyMismatch
from app.orders.enums import ApproverKind, CaseState, Fix, MoneyActionStatus
from app.orders.models import AuditLog, Case, Decision, MoneyAction
from app.orders.state import transition_case
from app.paypal_client.client import PayPalClient, PayPalConfig
from app.paypal_client.types import DisputeEvidence
from app.policy import service as service_module
from app.policy.gateway import RestRefundGateway
from app.policy.rules import RuleValidationError, load_rules
from app.policy.service import REFUND_JOB, Approver, PolicyError, PolicyService, RefundJobHandler
from app.worker.models import Job
from app.worker.runner import JobRunner, PermanentError, enqueue
from tests.factories import (
    add_approval,
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
SYSTEM = Approver(kind=ApproverKind.SYSTEM, id="system")
OVER_LIMIT = 8000  # default GBP auto-approve limit is 5000 -> AWAITING_SELLER


@pytest.fixture
def scenario(db, fake_paypal):
    def build(amount_minor: int = 4000, *, payer: str = "PAYER-1", seller=None):
        seller = seller or make_seller(db)
        order = make_order(db, seller, amount_minor=amount_minor, payer_id=payer)
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


def _edit(path: Path, change) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _awaiting_seller(policy, scenario, **kw):
    _, order, case = scenario(OVER_LIMIT, **kw)
    decision = policy.decide(case, engine_result(), facts_for(order))
    assert decision.route == CaseState.AWAITING_SELLER
    return order, case


def facts_for_amount(minor: int):
    class _O:  # facts_for only reads these three attributes
        amount_minor = minor
        currency = "GBP"
        buyer_country = "GB"

    return facts_for(_O())  # type: ignore[arg-type]


# ---------------------------------------------------------------- RL-01 / RL-11: rule loading


def test_RL_01_yaml_syntax_error_fails_validation(rules_copy):
    (rules_copy / "uk_possession_v1.yaml").write_text("id: [unclosed\n", encoding="utf-8")
    with pytest.raises(RuleValidationError, match="invalid YAML"):
        load_rules(rules_copy)


def test_RL_01_rule_file_must_be_a_mapping(rules_copy):
    (rules_copy / "uk_possession_v1.yaml").write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(RuleValidationError, match="mapping"):
        load_rules(rules_copy)


def test_RL_01_unknown_rule_kind_fails_validation(rules_copy):
    _edit(rules_copy / "uk_possession_v1.yaml", lambda d: d.update(kind="weird"))
    with pytest.raises(RuleValidationError, match="unknown kind"):
        load_rules(rules_copy)


def test_RL_01_rule_id_must_equal_file_name(rules_copy):
    _edit(rules_copy / "uk_possession_v1.yaml", lambda d: d.update(id="uk_possession_v9"))
    with pytest.raises(RuleValidationError, match="file name"):
        load_rules(rules_copy)


def test_RL_01_empty_rules_directory_fails(tmp_path):
    empty = tmp_path / "rules"
    empty.mkdir()
    with pytest.raises(RuleValidationError, match="no rule files"):
        load_rules(empty)


def test_RL_11_second_money_limits_file_fails(rules_copy):
    second = rules_copy / "money_limits_v2.yaml"
    shutil.copy(rules_copy / "money_limits_v1.yaml", second)
    _edit(second, lambda d: d.update(id="money_limits_v2"))
    with pytest.raises(RuleValidationError, match="exactly one money_limits"):
        load_rules(rules_copy)


def test_RL_11_money_limits_missing_a_currency_fails(rules_copy):
    _edit(
        rules_copy / "money_limits_v1.yaml",
        lambda d: d["auto_approve_limit_minor"].pop("USD"),
    )
    with pytest.raises(RuleValidationError, match="missing currencies"):
        load_rules(rules_copy)


# ---------------------------------------------------------------- PO-13: decide() guards


def test_PO_13_decide_rejects_case_without_order(policy):
    orphan = Case(order_id="missing", seller_id="missing", state=CaseState.GATHERING_EVIDENCE)
    with pytest.raises(PolicyError, match="order or seller missing"):
        policy.decide(orphan, engine_result(), facts_for_amount(4000))


def test_PO_13_undetermined_liability_holds_for_review(db, rules_copy, clock, scenario):
    _edit(
        rules_copy / "uk_possession_v1.yaml",
        lambda d: d["when_no_possession_proof"].update(liable_party="undetermined"),
    )
    service = PolicyService(db, load_rules(rules_copy), clock)
    _, order, case = scenario(4000)
    decision = service.decide(case, engine_result(), facts_for(order))
    assert decision.route == CaseState.HOLD_FOR_REVIEW
    assert money_actions(db, case_id=case.id) == []


def test_PO_09_redecide_at_same_instant_keeps_decisions_ordered(db, policy, scenario):
    order, case = _awaiting_seller(policy, scenario)
    transition_case(db, case, CaseState.HOLD_FOR_REVIEW, actor="reviewer")
    transition_case(db, case, CaseState.GATHERING_EVIDENCE, actor="reviewer")
    policy.decide(case, engine_result(), facts_for(order, fix=Fix.RESHIP))  # clock not advanced
    first, second = db.scalars(
        select(Decision).where(Decision.case_id == case.id).order_by(Decision.created_at)
    ).all()
    assert second.created_at > first.created_at
    assert second.fix == Fix.RESHIP.value  # approve() acts on the newest decision
    assert policy.approve(case, SELLER) is None
    assert case.state == CaseState.SETTLED


def test_PO_04_case_without_payer_is_never_a_repeat_claimant(db, policy, scenario):
    seller, _, _ = scenario(4000, payer="")
    scenario(4000, payer="", seller=seller)
    _, order, case = scenario(4000, payer="", seller=seller)
    assert policy.decide(case, engine_result(), facts_for(order)).route == CaseState.AUTO_REFUND


# ---------------------------------------------------------------- PO-03 / PO-11: approve, decline


def test_PO_03_system_approver_cannot_approve(policy, scenario):
    _, case = _awaiting_seller(policy, scenario)
    with pytest.raises(PolicyError, match="system approvals"):
        policy.approve(case, SYSTEM)


def test_PO_03_approve_without_decision_raises(policy, scenario):
    _, _, case = scenario(4000)
    with pytest.raises(PolicyError, match="no decision"):
        policy.approve(case, SELLER)


def test_PO_03_approve_after_decline_raises(policy, scenario):
    _, case = _awaiting_seller(policy, scenario)
    policy.decline(case, SELLER)
    with pytest.raises(PolicyError, match="cannot be approved"):
        policy.approve(case, SELLER)


def test_PO_03_approve_without_completed_capture_raises(db, policy, scenario):
    order, case = _awaiting_seller(policy, scenario)
    order.capture_status = "PENDING"
    db.flush()
    with pytest.raises(PolicyError, match="no completed capture"):
        policy.approve(case, SELLER)
    assert money_actions(db, case_id=case.id) == []


def test_PO_03_decline_outside_awaiting_seller_raises(policy, scenario):
    _, order, case = scenario(4000)
    assert policy.decide(case, engine_result(), facts_for(order)).route == CaseState.AUTO_REFUND
    with pytest.raises(PolicyError, match="cannot be declined"):
        policy.decline(case, SELLER)


@pytest.mark.xfail(strict=True, reason="BUG-1: decline allowed while a refund is queued")
def test_PO_03_decline_after_approval_is_refused_while_refund_queued(db, policy, scenario):
    """Money is already in flight once the seller approves; a later decline must not orphan it."""
    _, case = _awaiting_seller(policy, scenario)
    ma = policy.approve(case, SELLER)
    assert ma is not None and ma.status == MoneyActionStatus.QUEUED
    with pytest.raises(PolicyError):
        policy.decline(case, SELLER)
    assert case.state == CaseState.AWAITING_SELLER


def test_PO_11_approving_a_reship_settles_without_money(db, policy, scenario):
    _, order, case = scenario(OVER_LIMIT)
    decision = policy.decide(case, engine_result(), facts_for(order, fix=Fix.RESHIP))
    assert decision.route == CaseState.AWAITING_SELLER
    assert policy.approve(case, SELLER) is None
    assert case.state == CaseState.SETTLED
    assert money_actions(db, case_id=case.id) == []


def test_PO_11_settle_with_reship_rejects_a_refund_decision(policy, scenario):
    _, case = _awaiting_seller(policy, scenario)
    with pytest.raises(PolicyError, match="not a reship"):
        policy.settle_with_reship(case, SELLER)


# ---------------------------------------------------------------- PO-06 / PO-10: refundable amount


def test_PO_06_approve_with_nothing_left_to_refund_queues_nothing(db, policy, scenario):
    order, case = _awaiting_seller(policy, scenario)
    policy.record_external_refund(order, money(OVER_LIMIT), paypal_refund_id="EXT-1")
    assert policy.approve(case, SELLER) is None
    [only] = money_actions(db, order_id=order.id)
    assert only.external  # the seller's own refund; Doorstep queued nothing
    assert db.scalars(select(Job).where(Job.kind == REFUND_JOB)).all() == []


def test_PO_06_decide_with_nothing_left_to_refund_holds_for_review(db, policy, scenario):
    _, order, case = scenario(4000)
    policy.record_external_refund(order, money(4000), paypal_refund_id="EXT-0")
    decision = policy.decide(case, engine_result(), facts_for(order))
    assert decision.route == CaseState.HOLD_FOR_REVIEW
    assert db.scalars(select(Job).where(Job.kind == REFUND_JOB)).all() == []


def test_PO_06_recheck_blocks_refund_above_live_capture(
    db, policy, scenario, run_refunds, fake_paypal
):
    """Deterministic ceiling example: PayPal captured less than Doorstep's order amount."""
    _, order, case = scenario(4000)
    assert policy.decide(case, engine_result(), facts_for(order)).route == CaseState.AUTO_REFUND
    fake_paypal.captures[order.capture_id].amount_minor = 3000  # 0 refunded + 4000 > 3000
    run_refunds()
    [ma] = money_actions(db, case_id=case.id)
    assert ma.status == MoneyActionStatus.BLOCKED
    assert "exceed" in str(ma.paypal_response)
    assert fake_paypal.refund_calls == []
    assert db.get(Case, case.id).state == CaseState.HOLD_FOR_REVIEW


def _bypass_lock(monkeypatch, order, *, existing_after_conflict: bool) -> None:
    """Fault injection: simulate a concurrent approval whose uncommitted refund this session cannot
    see (as if the case row lock were bypassed). Both pre-insert reads are stale; only the unique
    (decision_id, kind) claim notices the other refund."""
    monkeypatch.setattr(
        service_module, "_remaining_refundable", lambda session, o: money(order.amount_minor)
    )
    real = PolicyService._existing_refund
    calls = iter([None])

    def stale_then_real(self, decision):
        first = next(calls, "real")
        if first is None or not existing_after_conflict:
            return None
        return real(self, decision)

    monkeypatch.setattr(PolicyService, "_existing_refund", stale_then_real)


def test_PO_07_approve_race_returns_the_winning_refund(db, policy, scenario, monkeypatch):
    order, case = _awaiting_seller(policy, scenario)
    decision = db.scalars(select(Decision).where(Decision.case_id == case.id)).one()
    winner = add_money_action(
        db, case, decision, status=MoneyActionStatus.QUEUED, amount_minor=OVER_LIMIT
    )
    _bypass_lock(monkeypatch, order, existing_after_conflict=True)
    found = policy.approve(case, SELLER)
    assert found is not None and found.id == winner.id
    assert len(money_actions(db, case_id=case.id)) == 1


def test_PO_07_approve_reraises_unexplained_integrity_error(db, policy, scenario, monkeypatch):
    """The claim is rejected but no matching refund can be found: never swallow the error."""
    order, case = _awaiting_seller(policy, scenario)
    decision = db.scalars(select(Decision).where(Decision.case_id == case.id)).one()
    add_money_action(db, case, decision, status=MoneyActionStatus.QUEUED, amount_minor=OVER_LIMIT)
    _bypass_lock(monkeypatch, order, existing_after_conflict=False)
    with pytest.raises(IntegrityError):
        policy.approve(case, SELLER)


def test_PO_06_external_refund_is_recorded_once(db, policy, scenario):
    _, order, _ = scenario(4000)
    first = policy.record_external_refund(order, money(1000), paypal_refund_id="EXT-7")
    again = policy.record_external_refund(order, money(1000), paypal_refund_id="EXT-7")
    assert again.id == first.id
    assert len(money_actions(db, order_id=order.id)) == 1


def test_PO_10_external_refund_in_another_currency_rejected(db, policy, scenario):
    _, order, _ = scenario(4000)
    with pytest.raises(CurrencyMismatch):
        policy.record_external_refund(order, money(1000, "USD"), paypal_refund_id="EXT-8")
    assert money_actions(db, order_id=order.id) == []


# ---------------------------------------------------------------- PO-12: refund job re-check


def test_PO_12_recheck_blocks_when_order_has_no_capture_id(db, policy, scenario, run_refunds):
    _, order, case = scenario(4000)
    policy.decide(case, engine_result(), facts_for(order))
    order.capture_id = None
    run_refunds()
    [ma] = money_actions(db, case_id=case.id)
    assert ma.status == MoneyActionStatus.BLOCKED
    assert db.get(Case, case.id).state == CaseState.HOLD_FOR_REVIEW


def test_PO_12_recheck_blocks_on_capture_currency_mismatch(
    db, policy, scenario, run_refunds, fake_paypal
):
    _, order, case = scenario(4000)
    fake_paypal.captures[order.capture_id].currency = "USD"
    policy.decide(case, engine_result(), facts_for(order))
    run_refunds()
    [ma] = money_actions(db, case_id=case.id)
    assert ma.status == MoneyActionStatus.BLOCKED
    assert fake_paypal.refund_calls == []
    assert db.get(Case, case.id).state == CaseState.HOLD_FOR_REVIEW


@pytest.mark.xfail(strict=True, reason="BUG-2: re-check ignores a finished case")
def test_PO_12_refund_job_for_closed_case_sends_no_money(
    db, policy, scenario, run_refunds, fake_paypal
):
    """A case closed while its refund was queued must not move money afterwards."""
    _, order, case = scenario(4000)
    assert policy.decide(case, engine_result(), facts_for(order)).route == CaseState.AUTO_REFUND
    transition_case(db, case, CaseState.CLOSED, actor="reviewer", reason="closed by reviewer")
    run_refunds()
    [ma] = money_actions(db, case_id=case.id)
    assert fake_paypal.refund_calls == []
    assert ma.status == MoneyActionStatus.BLOCKED
    assert db.get(Case, case.id).state == CaseState.CLOSED


@pytest.mark.xfail(strict=True, reason="BUG-2: re-check ignores a finished case")
def test_PO_12_refund_job_for_settled_case_sends_no_money(
    db, policy, scenario, run_refunds, fake_paypal
):
    """A case settled another way (here: an approved reship) never sends its queued refund."""
    _, order, case = scenario(4000)
    assert policy.decide(case, engine_result(), facts_for(order)).route == CaseState.AUTO_REFUND
    reship = add_decision(db, case, fix=Fix.RESHIP)
    add_approval(db, case, reship)
    transition_case(db, case, CaseState.SETTLED, actor="seller:seller-1", reason="reshipped")
    run_refunds()
    [ma] = money_actions(db, case_id=case.id)
    assert fake_paypal.refund_calls == []
    assert ma.status == MoneyActionStatus.BLOCKED
    assert db.get(Case, case.id).state == CaseState.SETTLED


def _job_failed_audit(db, job: Job) -> list[AuditLog]:
    return list(
        db.scalars(
            select(AuditLog).where(AuditLog.entity_id == job.id, AuditLog.action == "job.failed")
        )
    )


@pytest.mark.xfail(strict=True, reason="BUG-3: missing row is retried, on_failed raises")
def test_PO_12_refund_job_for_missing_money_action_fails_permanently(
    db, clock, settings, run_refunds
):
    enqueue(db, REFUND_JOB, {"money_action_id": "does-not-exist"}, clock=clock)
    run_refunds(rounds=settings.max_attempts, advance_s=settings.backoff_base_s * 8)
    [job] = db.scalars(select(Job).where(Job.kind == REFUND_JOB)).all()
    assert job.status == "failed"
    assert job.attempts == 1  # permanent: a missing row never appears on retry
    assert "money action missing" in (job.last_error or "")
    assert len(_job_failed_audit(db, job)) == 1


@pytest.mark.xfail(strict=True, reason="BUG-3: missing row is retried, on_failed raises")
@pytest.mark.parametrize("missing", ["decision_id", "order_id"])
def test_PO_12_refund_job_with_dangling_reference_fails_and_holds_case(
    db, policy, scenario, run_refunds, fake_paypal, missing
):
    """Fault injection: the money action survives but a row it points at is gone
    (SQLite does not enforce foreign keys, so the dangling id can be written directly)."""
    _, order, case = scenario(4000)
    assert policy.decide(case, engine_result(), facts_for(order)).route == CaseState.AUTO_REFUND
    [ma] = money_actions(db, case_id=case.id)
    setattr(ma, missing, "gone")
    run_refunds()
    [job] = db.scalars(select(Job).where(Job.kind == REFUND_JOB)).all()
    assert job.status == "failed"
    assert job.attempts == 1
    assert "missing" in (job.last_error or "")
    assert len(_job_failed_audit(db, job)) == 1
    assert fake_paypal.refund_calls == []
    assert db.get(Case, case.id).state == CaseState.HOLD_FOR_REVIEW


@pytest.mark.xfail(strict=True, reason="BUG-3: one bad job crashes the worker loop")
def test_PO_12_worker_survives_a_bad_refund_job(
    db, policy, scenario, run_refunds, fake_paypal, clock, settings
):
    enqueue(db, REFUND_JOB, {"money_action_id": "does-not-exist"}, clock=clock)
    _, order, case = scenario(4000)
    policy.decide(case, engine_result(), facts_for(order))
    run_refunds(rounds=settings.max_attempts, advance_s=settings.backoff_base_s * 8)
    [ma] = money_actions(db, case_id=case.id)
    assert ma.status == MoneyActionStatus.COMPLETED
    assert db.get(Case, case.id).state == CaseState.SETTLED


@pytest.mark.xfail(strict=True, reason="BUG-3: one bad job crashes the worker loop")
def test_PP_04_worker_survives_a_handler_whose_on_failed_raises(
    db, session_factory, clock, settings
):
    class Exploding:
        def run(self, session, job):
            raise PermanentError("bad job")

        def on_failed(self, session, job, error):
            raise RuntimeError("on_failed exploded")

    class Fine:
        ran = 0

        def run(self, session, job):
            Fine.ran += 1

        def on_failed(self, session, job, error):  # pragma: no cover - never fails
            raise AssertionError

    bad = enqueue(db, "explode", {}, clock=clock)
    enqueue(db, "fine", {}, clock=clock)
    db.commit()
    JobRunner(session_factory, {"explode": Exploding(), "fine": Fine()}, clock, settings).run_due()
    db.expire_all()
    assert Fine.ran == 1
    failed = db.get(Job, bad.id)
    assert failed.status == "failed"
    assert len(_job_failed_audit(db, failed)) == 1


def test_PO_12_on_failed_leaves_a_completed_refund_untouched(
    db, policy, scenario, run_refunds, fake_paypal, rulebook, clock
):
    _, order, case = scenario(4000)
    policy.decide(case, engine_result(), facts_for(order))
    run_refunds()
    [ma] = money_actions(db, case_id=case.id)
    assert ma.status == MoneyActionStatus.COMPLETED
    [job] = db.scalars(select(Job).where(Job.kind == REFUND_JOB)).all()
    RefundJobHandler(fake_paypal, rulebook, clock).on_failed(db, job, RuntimeError("late"))
    db.flush()
    assert db.get(MoneyAction, ma.id).status == MoneyActionStatus.COMPLETED
    assert db.get(Case, case.id).state == CaseState.SETTLED


# ---------------------------------------------------------------- PO-05: dispute evidence gateway


def test_PO_05_rest_gateway_sends_dispute_evidence(httpserver: HTTPServer):
    httpserver.expect_request("/v1/oauth2/token", method="POST").respond_with_json(
        {"access_token": "TOKEN-1", "token_type": "Bearer", "expires_in": 32400}
    )
    httpserver.expect_request(
        "/v1/customer/disputes/PP-D-1/provide-evidence", method="POST"
    ).respond_with_json({"links": []})
    client = PayPalClient(
        PayPalConfig(
            client_id="client-id",
            client_secret="test-secret",
            base_url=httpserver.url_for("").rstrip("/"),
            timeout_s=2,
        ),
        FakeClock(),
    )
    evidence = DisputeEvidence(evidence_type="PROOF_OF_FULFILLMENT", notes="Delivered and signed.")
    RestRefundGateway(client).provide_dispute_evidence(
        "PP-D-1", evidence=evidence, request_id="case:c1:evidence"
    )
    [sent] = [r for r, _ in httpserver.log if r.path.endswith("/provide-evidence")]
    assert sent.headers["PayPal-Request-Id"] == "case:c1:evidence"
    assert "PROOF_OF_FULFILLMENT" in sent.get_data(as_text=True)
