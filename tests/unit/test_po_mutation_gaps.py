"""Mutation gaps in app/policy (human-approved addendum, ADR-023, docs/mutation-report.md).

Part 1 pins the logic gaps, money first. Part 2 is one table-driven golden test of the full audit
row written by every money or state action.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import select

from app.orders.enums import (
    ApproverKind,
    CaseState,
    Fix,
    MoneyActionKind,
    MoneyActionStatus,
)
from app.orders.models import AuditLog, Case, Decision, Dispute, MoneyAction
from app.orders.schemas import Address
from app.paypal_client.errors import PayPalClientError
from app.policy.service import REFUND_JOB, Approver, PolicyService, RefundJobHandler
from app.worker.models import Job
from tests.factories import add_money_action, make_case, make_order, make_seller, money_actions
from tests.helpers import engine_result, facts_for, money, run_jobs

SELLER = Approver(kind=ApproverKind.SELLER, id="seller-1")
REVIEWER = Approver(kind=ApproverKind.REVIEWER, id="reviewer-1")


@pytest.fixture
def policy(db, rulebook, clock):
    return PolicyService(db, rulebook, clock)


@pytest.fixture
def opened(db, fake_paypal):
    """A GB order captured in the fake PayPal, with a case in GATHERING_EVIDENCE."""

    def build(amount_minor: int = 4000):
        order = make_order(db, make_seller(db), amount_minor=amount_minor)
        fake_paypal.add_capture(order.paypal_order_id, order.capture_id, money(amount_minor))
        case = make_case(db, order)
        db.commit()
        return order, case

    return build


@pytest.fixture
def run_refunds(session_factory, fake_paypal, rulebook, clock, settings, db):
    def run() -> None:
        db.commit()
        run_jobs(
            session_factory,
            {REFUND_JOB: RefundJobHandler(fake_paypal, rulebook, clock)},
            clock,
            settings,
        )
        db.expire_all()

    return run


def _decide(policy, case, order, claim_minor: int | None = None, **facts):
    return policy.decide(case, engine_result(), facts_for(order, claim_minor, **facts))


# ================================================================ part 1: logic gaps


# ---------------------------------------------------------------- 1, 4, 9: the refund itself


def test_PO_07_partial_refund_reaches_paypal_with_its_exact_amount(
    db, policy, opened, run_refunds, fake_paypal, clock
):
    order, case = opened(4000)
    _decide(policy, case, order, claim_minor=1000)
    [ma] = money_actions(db, case_id=case.id)
    assert ma.kind == MoneyActionKind.PARTIAL_REFUND
    run_refunds()
    assert fake_paypal.refund_calls == [(order.capture_id, 1000, ma.request_id)]
    assert fake_paypal.total_refunded(order.capture_id) == 1000  # partial stays partial


def test_PO_07_full_refund_is_kind_refund_and_sends_the_full_amount(
    db, policy, opened, run_refunds, fake_paypal
):
    order, case = opened(4000)
    _decide(policy, case, order)
    [ma] = money_actions(db, case_id=case.id)
    assert ma.kind == MoneyActionKind.REFUND
    run_refunds()
    assert fake_paypal.refund_calls == [(order.capture_id, 4000, ma.request_id)]


def test_PO_07_completed_refund_stores_paypal_status_and_completion_time(
    db, policy, opened, run_refunds, clock
):
    order, case = opened(4000)
    _decide(policy, case, order)
    run_refunds()
    [ma] = money_actions(db, case_id=case.id)
    assert ma.status == MoneyActionStatus.COMPLETED
    assert ma.paypal_status == "COMPLETED"
    assert ma.completed_at == clock.now()
    assert ma.paypal_response["refund_id"] == ma.paypal_refund_id
    assert ma.paypal_response["status"] == "COMPLETED"


# ---------------------------------------------------------------- 2: custom_id always sent


def test_PP_07_rest_gateway_forwards_custom_id(httpserver):
    import json

    from app.paypal_client.client import PayPalClient, PayPalConfig
    from app.policy.gateway import RestRefundGateway
    from tests.fakes import FakeClock

    httpserver.expect_request("/v1/oauth2/token", method="POST").respond_with_json(
        {"access_token": "T", "token_type": "Bearer", "expires_in": 32400}
    )
    httpserver.expect_request(
        "/v2/payments/captures/CAP-5/refund", method="POST"
    ).respond_with_json({"id": "R-5", "status": "COMPLETED"}, status=201)
    client = PayPalClient(
        PayPalConfig(
            client_id="c",
            client_secret="s",
            base_url=httpserver.url_for("").rstrip("/"),
            timeout_s=2,
        ),
        FakeClock(),
    )
    RestRefundGateway(client).refund_capture(
        "CAP-5", amount=money(1500), request_id="req-5", custom_id="ma-5"
    )
    [sent] = [r for r, _ in httpserver.log if r.path.endswith("/refund")]
    assert json.loads(sent.get_data(as_text=True))["custom_id"] == "ma-5"


def test_PO_07_every_refund_attempt_sends_custom_id(db, policy, opened, run_refunds, fake_paypal):
    order, case = opened(8000)  # over the limit: seller approves
    _decide(policy, case, order)
    ma = policy.approve(case, SELLER)
    run_refunds()
    assert fake_paypal.refund_custom_ids == [ma.id]


# ---------------------------------------------------------------- 3: crash-recovery conditions


def test_PO_07_our_landed_refund_plus_an_unknown_refund_blocks(
    db, policy, opened, run_refunds, fake_paypal
):
    """Our refund landed (our custom_id, same amount), but PayPal also shows a refund we cannot
    identify: refunded != known + ours, so nothing is recovered and nothing is resent."""
    order, case = opened(4000)
    _decide(policy, case, order, claim_minor=1000)
    [ma] = money_actions(db, case_id=case.id)
    fake_paypal.refund_capture(
        order.capture_id, amount=money(1000), request_id=ma.request_id, custom_id=ma.id
    )
    fake_paypal.external_refund(order.capture_id, 500)
    calls_before = len(fake_paypal.refund_calls)
    run_refunds()
    stored = db.get(MoneyAction, ma.id)
    assert stored.status == MoneyActionStatus.BLOCKED
    assert stored.paypal_response["blocked_code"] == "unknown_refund_at_paypal"
    assert len(fake_paypal.refund_calls) == calls_before
    assert db.get(Case, case.id).state == CaseState.HOLD_FOR_REVIEW


def test_PO_07_two_landed_refunds_with_our_custom_id_block(
    db, policy, opened, run_refunds, fake_paypal
):
    order, case = opened(4000)
    _decide(policy, case, order, claim_minor=1000)
    [ma] = money_actions(db, case_id=case.id)
    for request_id in ("first", "second"):
        fake_paypal.refund_capture(
            order.capture_id, amount=money(1000), request_id=request_id, custom_id=ma.id
        )
    run_refunds()
    assert db.get(MoneyAction, ma.id).status == MoneyActionStatus.BLOCKED


# ---------------------------------------------------------------- 5: refund kind filter


def test_PO_07_approve_ignores_a_dispute_evidence_action_on_the_same_decision(db, policy, opened):
    order, case = opened(8000)
    _decide(policy, case, order)
    decision = db.scalars(select(Decision).where(Decision.case_id == case.id)).one()
    evidence = add_money_action(db, case, decision, status=MoneyActionStatus.COMPLETED)
    evidence.kind = MoneyActionKind.EVIDENCE.value
    db.flush()
    ma = policy.approve(case, SELLER)
    assert ma is not None and ma.id != evidence.id
    assert (ma.kind, ma.status) == (MoneyActionKind.REFUND, MoneyActionStatus.QUEUED)


# ---------------------------------------------------------------- 6: routing never crashes


def test_PO_12_dispute_opened_on_a_held_case_blocks_and_keeps_it_held(
    db, policy, opened, run_refunds, fake_paypal
):
    """The blocked refund targets DISPUTE_EVIDENCE, which HOLD_FOR_REVIEW cannot reach: the case
    stays held and the job does not crash."""
    from app.cases.engine import Confidence

    order, case = opened(4000)
    decision = policy.decide(case, engine_result(confidence=Confidence.LOW), facts_for(order))
    assert decision.route == CaseState.HOLD_FOR_REVIEW
    ma = policy.approve(case, REVIEWER)
    db.add(Dispute(order_id=order.id, paypal_dispute_id="PP-D-HELD", status="open"))
    run_refunds()
    stored = db.get(MoneyAction, ma.id)
    assert stored.status == MoneyActionStatus.BLOCKED
    assert stored.paypal_response["blocked_code"] == "dispute_opened"
    assert db.get(Case, case.id).state == CaseState.HOLD_FOR_REVIEW
    [job] = db.scalars(select(Job).where(Job.kind == REFUND_JOB)).all()
    assert job.status == "done"
    assert fake_paypal.refund_calls == []


# ---------------------------------------------------------------- 7, 8: missing records


def test_PO_12_money_action_with_a_case_but_no_decision_fails_without_refund(
    db, policy, opened, run_refunds, fake_paypal
):
    order, case = opened(4000)
    _decide(policy, case, order)
    [ma] = money_actions(db, case_id=case.id)
    ma.decision_id = None
    run_refunds()
    assert db.get(MoneyAction, ma.id).status == MoneyActionStatus.FAILED
    assert fake_paypal.refund_calls == []
    assert db.get(Case, case.id).state == CaseState.HOLD_FOR_REVIEW


def test_PO_12_dangling_order_marks_the_existing_money_action_failed(
    db, policy, opened, run_refunds, fake_paypal
):
    order, case = opened(4000)
    _decide(policy, case, order)
    [ma] = money_actions(db, case_id=case.id)
    ma.order_id = "gone"
    run_refunds()
    stored = db.get(MoneyAction, ma.id)
    assert stored.status == MoneyActionStatus.FAILED
    assert "MissingRecord" in stored.paypal_response["error"]
    assert db.get(Case, case.id).state == CaseState.HOLD_FOR_REVIEW


# ---------------------------------------------------------------- 10: decision signals


def _plain_json(value: Any) -> bool:
    if isinstance(value, dict):
        return all(type(k) is str and _plain_json(v) for k, v in value.items())
    if isinstance(value, list):
        return all(_plain_json(v) for v in value)
    return value is None or type(value) in (str, int, float, bool)


def test_PO_09_decision_signals_are_stored_as_plain_json(db, policy, opened):
    order, case = opened(4000)
    _decide(policy, case, order)
    stored = db.scalars(select(Decision).where(Decision.case_id == case.id)).one()
    assert stored.signals["liability"] is not None
    assert _plain_json(stored.signals["engine"])  # enums as their values, not enum members
    assert _plain_json(stored.signals["liability"])


# ---------------------------------------------------------------- 11: address comparison


def test_RL_07_addresses_differing_only_in_a_letter_are_redirected(rulebook):
    base = Address(
        name="Sam",
        line1="Flat 2A, 10 High Street",
        city="London",
        postal_code="N1 9GU",
        country="GB",
    )
    other = base.model_copy(update={"line1": "Flat 2B, 10 High Street"})
    assert rulebook.seller_protection.is_redirected(base, other) is True


# ================================================================ part 2: audit golden


def _at(value: datetime, clock) -> str:
    return "clock" if value == clock.now() else "db-default"


def _trail(db, clock, labels: dict[str, str], since: set[str]) -> list[tuple]:
    rows = db.scalars(select(AuditLog)).all()
    return sorted(
        (
            r.actor,
            r.action,
            r.entity_type,
            labels.get(r.entity_id, "?"),
            r.before,
            r.after,
            _at(r.at, clock),
        )
        for r in rows
        if r.id not in since
    )


def _money(db, ma: MoneyAction, status: str, approver: dict, paypal_response: dict) -> dict:
    """The full money audit payload (app/policy/service.py _money_audit_payload)."""
    decision = db.get(Decision, ma.decision_id)
    return {
        "money_action_id": ma.id,
        "status": status,
        "amount_minor": ma.amount_minor,
        "currency": ma.currency,
        "request_id": ma.request_id,
        "signals": decision.signals,
        "rule_id": decision.rule_id,
        "rule_version": decision.rule_version,
        "model_outputs": decision.model_outputs,
        "approver": approver,
        "paypal_response": paypal_response,
    }


def _case(actor: str, reason: str, before: str, after: str) -> tuple:
    return (actor, f"transition: {reason}", "case", "case", {"state": before}, {"state": after})


def _order_resolved(actor: str, case_state: str) -> tuple:
    return (
        actor,
        f"transition: case {case_state}",
        "order",
        "order",
        {"state": "CASE_OPEN"},
        {"state": "RESOLVED"},
    )


SYSTEM = {"kind": "system", "id": "policy"}
SELLER_APPROVER = {"kind": "seller", "id": "seller-1"}


def _auto_refund_completes(db, policy, opened, run_refunds, fake_paypal, clock):
    order, case = opened(4000)
    yield order, case
    _decide(policy, case, order)
    run_refunds()
    [ma] = money_actions(db, case_id=case.id)
    yield (
        ma,
        [
            (*_case("policy", "decision recorded", "GATHERING_EVIDENCE", "DECIDED"), "db-default"),
            (*_case("policy", "auto_refund", "DECIDED", "AUTO_REFUND"), "db-default"),
            (
                "system:policy",
                "refund.queued",
                "money_action",
                "ma",
                {},
                _money(db, ma, "queued", SYSTEM, {}),
                "clock",
            ),
            (
                "paypal",
                "refund.completed",
                "money_action",
                "ma",
                {"status": "queued"},
                _money(db, ma, "completed", SYSTEM, ma.paypal_response),
                "clock",
            ),
            (*_case("policy", "refund completed", "AUTO_REFUND", "SETTLED"), "db-default"),
            (*_order_resolved("policy", "SETTLED"), "db-default"),
        ],
    )


def _seller_approves_refund(db, policy, opened, run_refunds, fake_paypal, clock):
    order, case = opened(8000)
    _decide(policy, case, order)
    yield order, case
    policy.approve(case, SELLER)
    run_refunds()
    [ma] = money_actions(db, case_id=case.id)
    yield (
        ma,
        [
            (
                "seller:seller-1",
                "refund.queued",
                "money_action",
                "ma",
                {},
                _money(db, ma, "queued", SELLER_APPROVER, {}),
                "clock",
            ),
            (
                "paypal",
                "refund.completed",
                "money_action",
                "ma",
                {"status": "queued"},
                _money(db, ma, "completed", SELLER_APPROVER, ma.paypal_response),
                "clock",
            ),
            (*_case("policy", "refund completed", "AWAITING_SELLER", "SETTLED"), "db-default"),
            (*_order_resolved("policy", "SETTLED"), "db-default"),
        ],
    )


def _refund_blocked(db, policy, opened, run_refunds, fake_paypal, clock):
    order, case = opened(4000)
    _decide(policy, case, order)
    yield order, case
    fake_paypal.captures[order.capture_id].amount_minor = 3000  # would exceed the capture
    run_refunds()
    [ma] = money_actions(db, case_id=case.id)
    blocked = {
        "blocked": "refund could exceed the captured amount",
        "blocked_code": "would_exceed_capture",
    }
    assert ma.paypal_response == blocked
    yield (
        ma,
        [
            (
                "policy",
                "refund.blocked",
                "money_action",
                "ma",
                {"status": "queued"},
                _money(db, ma, "blocked", SYSTEM, blocked),
                "clock",
            ),
            (
                *_case("policy", "would_exceed_capture", "AUTO_REFUND", "HOLD_FOR_REVIEW"),
                "db-default",
            ),
        ],
    )


def _refund_failed(db, policy, opened, run_refunds, fake_paypal, clock):
    order, case = opened(8000)
    _decide(policy, case, order)
    policy.approve(case, SELLER)
    yield order, case
    fake_paypal.fail_next_refunds(PayPalClientError(422, "UNPROCESSABLE_ENTITY"))
    run_refunds()
    [ma] = money_actions(db, case_id=case.id)
    [job] = db.scalars(select(Job).where(Job.kind == REFUND_JOB)).all()
    yield (
        ma,
        [
            (
                "worker",
                "refund.failed",
                "money_action",
                "ma",
                {"status": "queued"},
                _money(db, ma, "failed", SELLER_APPROVER, ma.paypal_response),
                "clock",
            ),
            (*_case("policy", "refund failed", "AWAITING_SELLER", "HOLD_FOR_REVIEW"), "db-default"),
            *_job_failed(db, job, clock),
        ],
    )


def _record_missing(db, policy, opened, run_refunds, fake_paypal, clock):
    order, case = opened(4000)
    _decide(policy, case, order)
    yield order, case
    [ma] = money_actions(db, case_id=case.id)
    ma.order_id = "gone"
    run_refunds()
    [job] = db.scalars(select(Job).where(Job.kind == REFUND_JOB)).all()
    yield (
        ma,
        [
            (*_case("policy", "record missing", "AUTO_REFUND", "HOLD_FOR_REVIEW"), "db-default"),
            *_job_failed(db, job, clock),
        ],
    )


def _external_refund(db, policy, opened, run_refunds, fake_paypal, clock):
    order, case = opened(4000)
    yield order, case
    ma = policy.record_external_refund(order, money(1500), paypal_refund_id="EXT-9")
    db.flush()
    yield (
        ma,
        [
            (
                "paypal",
                "refund.external",
                "money_action",
                "ma",
                {},
                {"amount_minor": 1500, "currency": "GBP", "paypal_refund_id": "EXT-9"},
                "clock",
            ),
        ],
    )


def _seller_declines(db, policy, opened, run_refunds, fake_paypal, clock):
    order, case = opened(8000)
    _decide(policy, case, order)
    yield order, case
    policy.decline(case, SELLER)
    db.flush()
    yield (
        None,
        [
            (
                *_case("seller:seller-1", "seller declined", "AWAITING_SELLER", "CLOSED"),
                "db-default",
            ),
            (*_order_resolved("seller:seller-1", "CLOSED"), "db-default"),
        ],
    )


def _reship_settles(db, policy, opened, run_refunds, fake_paypal, clock):
    order, case = opened(8000)
    _decide(policy, case, order, fix=Fix.RESHIP)
    yield order, case
    policy.approve(case, SELLER)
    db.flush()
    yield (
        None,
        [
            (
                *_case("seller:seller-1", "reship recorded", "AWAITING_SELLER", "SETTLED"),
                "db-default",
            ),
            (*_order_resolved("seller:seller-1", "SETTLED"), "db-default"),
        ],
    )


def _job_failed(db, job, clock) -> list[tuple]:
    """The runner's own row (app/worker, not app/policy): copied from the stored row so the golden
    pins the policy rows, not the runner's format."""
    rows = db.scalars(
        select(AuditLog).where(AuditLog.entity_id == job.id, AuditLog.action == "job.failed")
    ).all()
    assert len(rows) == 1
    r = rows[0]
    return [(r.actor, r.action, r.entity_type, "job", r.before, r.after, _at(r.at, clock))]


AUDIT_ACTIONS = {
    "auto_refund_completes": _auto_refund_completes,
    "seller_approves_refund": _seller_approves_refund,
    "refund_blocked": _refund_blocked,
    "refund_failed": _refund_failed,
    "record_missing": _record_missing,
    "external_refund": _external_refund,
    "seller_declines": _seller_declines,
    "reship_settles": _reship_settles,
}


@pytest.mark.parametrize("action", list(AUDIT_ACTIONS))
def test_PO_09_audit_rows_match_golden(db, policy, opened, run_refunds, fake_paypal, clock, action):
    """Every money or state action writes exactly these audit rows: actor, action, entity type,
    entity, before, full after payload, and timestamp (policy rows use the service clock)."""
    steps = AUDIT_ACTIONS[action](db, policy, opened, run_refunds, fake_paypal, clock)
    order, case = next(steps)
    db.flush()
    since = set(db.scalars(select(AuditLog.id)).all())
    ma, expected = next(steps)
    labels = {order.id: "order", case.id: "case"}
    if ma is not None:
        labels[ma.id] = "ma"
    for job in db.scalars(select(Job)).all():
        labels[job.id] = "job"
    assert _trail(db, clock, labels, since) == sorted(expected)


def test_PO_09_money_audit_payload_has_exactly_the_documented_keys(db, policy, opened, run_refunds):
    order, case = opened(4000)
    _decide(policy, case, order)
    [ma] = money_actions(db, case_id=case.id)
    row = db.scalars(select(AuditLog).where(AuditLog.entity_id == ma.id)).one()
    assert sorted(row.after) == sorted(
        [
            "money_action_id",
            "status",
            "amount_minor",
            "currency",
            "request_id",
            "signals",
            "rule_id",
            "rule_version",
            "model_outputs",
            "approver",
            "paypal_response",
        ]
    )
