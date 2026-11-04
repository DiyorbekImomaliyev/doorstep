"""PO reason codes: every route and every blocked refund reports a machine code (ADR-023).

Human sentences live in one message table (app.policy.reasons.MESSAGES); tests assert codes,
so wording can change without touching tests and mutants of the wording are not logic.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.cases.engine import Confidence, FailurePoint
from app.orders.enums import CaseState, Fix, MoneyActionStatus, ProofKind
from app.orders.models import Decision, Dispute, Evidence
from app.orders.state import transition_case
from app.policy.service import REFUND_JOB, PolicyService, RefundJobHandler
from tests.factories import make_case, make_order, make_seller, money_actions
from tests.helpers import engine_result, facts_for, money, run_jobs

pytestmark = pytest.mark.xfail(strict=True, reason="ADR-023: reason codes not built yet")


class _LazyReasons:
    """app.policy.reasons is imported when a test runs, so this module collects before it exists."""

    def __getattr__(self, name: str):
        from app.policy import reasons

        return getattr(reasons, name)


_r = _LazyReasons()


def R(name: str):
    return getattr(_r.Reason, name)


@pytest.fixture
def build(db, fake_paypal):
    def make(amount_minor: int = 4000, *, payer: str = "PAYER-1", seller=None, **order_kw):
        seller = seller or make_seller(db)
        order = make_order(db, seller, amount_minor=amount_minor, payer_id=payer, **order_kw)
        if order.capture_id:
            fake_paypal.add_capture(order.paypal_order_id, order.capture_id, money(amount_minor))
        case = make_case(db, order)
        db.commit()
        return seller, order, case

    return make


@pytest.fixture
def policy(db, rulebook, clock):
    return PolicyService(db, rulebook, clock)


def test_PO_13_every_reason_code_has_a_message():
    assert set(_r.MESSAGES) == set(_r.Reason)
    assert all(isinstance(text, str) and text.strip() for text in _r.MESSAGES.values())


def _decide(policy, case, order, *, engine=None, **facts):
    return policy.decide(case, engine_result(**(engine or {})), facts_for(order, **facts))


ROUTES = {
    "contradiction": ({"engine": {"contradiction": True}}, "CONTRADICTION"),
    "low_confidence": ({"engine": {"confidence": Confidence.LOW}}, "LOW_CONFIDENCE"),
    "no_rule_set": ({"country": "JP"}, "NO_RULE_SET"),
    "possession_proven": (
        {"proofs": frozenset({ProofKind.SIGNATURE})},
        "POSSESSION_PROVEN",
    ),
    "would_blame_buyer": (
        {"engine": {"fp": FailurePoint.STOLEN_AFTER_DELIVERY}},
        "WOULD_BLAME_BUYER",
    ),
    "reship": ({"fix": Fix.RESHIP}, "RESHIP"),
    "auto_refund": ({}, "AUTO_REFUND"),
}


@pytest.mark.parametrize("name", list(ROUTES))
def test_PO_13_route_reports_its_reason_code(db, policy, build, name):
    kwargs, code_name = ROUTES[name]
    code = R(code_name)
    _, order, case = build()
    result = _decide(policy, case, order, **kwargs)
    stored = db.scalars(select(Decision).where(Decision.case_id == case.id)).one()
    assert result.code == code
    assert result.codes[-1] == code
    assert stored.reason_codes == [c.value for c in result.codes]
    # the human sentence comes from the one message table (placeholders filled in)
    assert result.reasons[-1].startswith(_r.MESSAGES[code].split("{")[0])


def test_PO_13_open_dispute_reason_code(db, policy, build):
    _, order, case = build()
    db.add(Dispute(order_id=order.id, paypal_dispute_id="PP-D-RC", status="open"))
    db.flush()
    assert _decide(policy, case, order).code == R("OPEN_DISPUTE")


def test_PO_13_repeat_claimant_reason_code(policy, build):
    seller, _, _ = build(payer="PAYER-R")
    build(payer="PAYER-R", seller=seller)
    _, order, case = build(payer="PAYER-R", seller=seller)
    assert _decide(policy, case, order).code == R("REPEAT_CLAIMANT")


def test_PO_13_no_completed_capture_reason_code(policy, build):
    _, order, case = build(capture_status="PENDING")
    assert _decide(policy, case, order).code == R("NO_COMPLETED_CAPTURE")


def test_PO_13_liability_rule_reason_code_precedes_route(policy, build):
    _, order, case = build()
    assert R("LIABILITY_RULE") in _decide(policy, case, order).codes


def test_PO_13_undetermined_liability_reason_code(db, rules_copy, clock, build):
    import yaml

    from app.policy.rules import load_rules

    path = rules_copy / "uk_possession_v1.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["when_no_possession_proof"]["liable_party"] = "undetermined"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    _, order, case = build()
    result = PolicyService(db, load_rules(rules_copy), clock).decide(
        case, engine_result(), facts_for(order)
    )
    assert result.code == R("LIABILITY_UNDETERMINED")


def test_PO_13_nothing_left_reason_code(policy, build):
    _, order, case = build()
    policy.record_external_refund(order, money(4000), paypal_refund_id="EXT-RC")
    assert _decide(policy, case, order).code == R("NOTHING_LEFT_TO_REFUND")


def test_PO_13_daily_cap_reason_code(policy, build):
    _, order, case = build(3000, seller=None)
    order_seller = order.seller_id
    from app.orders.models import Seller

    seller = policy.session.get(Seller, order_seller)
    seller.daily_refund_caps = {"GBP": 2000, "USD": 2000}
    assert _decide(policy, case, order).code == R("DAILY_CAP_REACHED")


def test_PO_13_over_limit_reason_code(policy, build):
    _, order, case = build(8000)
    assert _decide(policy, case, order).code == R("OVER_AUTO_APPROVE_LIMIT")


# ---------------------------------------------------------------- blocked refunds (PO-12)


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


def _blocked_code(db, case) -> str:
    [ma] = money_actions(db, case_id=case.id)
    assert ma.status == MoneyActionStatus.BLOCKED
    return ma.paypal_response["blocked_code"]


def test_PO_12_blocked_case_finished_code(db, policy, build, run_refunds):
    _, order, case = build()
    _decide(policy, case, order)
    transition_case(db, case, CaseState.CLOSED, actor="reviewer")
    run_refunds()
    assert _blocked_code(db, case) == R("CASE_FINISHED")


def test_PO_12_blocked_dispute_opened_code(db, policy, build, run_refunds):
    _, order, case = build()
    _decide(policy, case, order)
    db.add(Dispute(order_id=order.id, paypal_dispute_id="PP-D-B", status="open"))
    run_refunds()
    assert _blocked_code(db, case) == R("DISPUTE_OPENED")


def test_PO_12_blocked_evidence_changed_code(db, policy, build, run_refunds):
    _, order, case = build()
    _decide(policy, case, order)
    db.add(Evidence(order_id=order.id, kind="buyer_photo", uri="s3://new.jpg"))
    run_refunds()
    assert _blocked_code(db, case) == R("EVIDENCE_CHANGED")


def test_PO_12_blocked_no_paypal_capture_code(db, policy, build, run_refunds):
    _, order, case = build()
    _decide(policy, case, order)
    order.capture_id = None
    run_refunds()
    assert _blocked_code(db, case) == R("NO_PAYPAL_CAPTURE")


def test_PO_12_blocked_currency_mismatch_code(db, policy, build, run_refunds, fake_paypal):
    _, order, case = build()
    fake_paypal.captures[order.capture_id].currency = "USD"
    _decide(policy, case, order)
    run_refunds()
    assert _blocked_code(db, case) == R("CAPTURE_CURRENCY_MISMATCH")


def test_PO_12_blocked_capture_not_refundable_code(db, policy, build, run_refunds, fake_paypal):
    _, order, case = build()
    fake_paypal.captures[order.capture_id].status = "DECLINED"
    _decide(policy, case, order)
    run_refunds()
    assert _blocked_code(db, case) == R("CAPTURE_NOT_REFUNDABLE")


def test_PO_12_blocked_unknown_refund_code(db, policy, build, run_refunds, fake_paypal):
    _, order, case = build()
    _decide(policy, case, order, amount_minor=1000)
    fake_paypal.external_refund(order.capture_id, 500)
    run_refunds()
    assert _blocked_code(db, case) == R("UNKNOWN_REFUND_AT_PAYPAL")


def test_PO_12_blocked_would_exceed_capture_code(db, policy, build, run_refunds, fake_paypal):
    _, order, case = build()
    _decide(policy, case, order)
    fake_paypal.captures[order.capture_id].amount_minor = 3000
    run_refunds()
    assert _blocked_code(db, case) == R("WOULD_EXCEED_CAPTURE")
