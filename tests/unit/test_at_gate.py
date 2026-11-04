"""AT-02 / AT-04: the agent's only money tool, request_refund, asks the policy and reports what
the policy already decided. It never creates a refund the policy did not queue."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.ai.explainer import Explainer
from app.cases.engine import ClaimReason
from app.cases.pipeline import CasePipeline
from app.cases.signals import EvidenceKind
from app.orders.enums import CaseState, OrderState
from app.orders.models import Case, Evidence, MoneyAction, TrackingEvent
from app.policy.agent_gate import RefundRequest, request_refund
from app.proof.receipts import ReceiptSigner
from tests.factories import make_order, make_seller, make_shipment


@pytest.fixture
def pipeline(db, rulebook, clock, settings) -> CasePipeline:
    return CasePipeline(db, rulebook, clock, settings, Explainer(), ReceiptSigner.generate())


def _case(db, pipeline, *, amount_minor: int, reason=ClaimReason.NOT_RECEIVED) -> Case:
    order = make_order(db, make_seller(db), state=OrderState.DELIVERED, amount_minor=amount_minor)
    order.address_check = "valid"
    shipment = make_shipment(db, order, tracking_number="SHIPPO_DELIVERED")
    db.add(
        TrackingEvent(
            shipment_id=shipment.id, status="DELIVERED", occurred_at=order.created_at, applied=True
        )
    )
    db.add(
        Evidence(
            order_id=order.id,
            kind=EvidenceKind.PACKING_PHOTO,
            ai_result={"verdict": "match", "expected_weight_g": 900},
        )
    )
    db.flush()
    report = pipeline.report(order, reason, reported_by="buyer")
    return db.get(Case, report.case_id)


def _money(db, case) -> list[MoneyAction]:
    return list(db.scalars(select(MoneyAction).where(MoneyAction.case_id == case.id)))


def test_AT_02_under_the_limit_the_policy_already_queued_the_refund(db, pipeline):
    case = _case(db, pipeline, amount_minor=4000)
    before = _money(db, case)
    outcome = request_refund(db, case.id)
    assert outcome.status == RefundRequest.QUEUED
    assert outcome.money_action_id == before[0].id
    assert _money(db, case) == before  # nothing new was created


def test_AT_04_over_the_limit_escalates_and_creates_nothing(db, pipeline):
    case = _case(db, pipeline, amount_minor=60000)
    assert case.state == CaseState.AWAITING_SELLER
    outcome = request_refund(db, case.id)
    assert outcome.status == RefundRequest.ESCALATED
    assert outcome.money_action_id is None
    assert _money(db, case) == []
    assert "seller" in outcome.message.lower()


def test_AT_04_a_held_case_stays_with_a_human(db, pipeline):
    case = _case(db, pipeline, amount_minor=4000, reason=ClaimReason.EMPTY_BOX)
    case.state = CaseState.HOLD_FOR_REVIEW.value  # contradictions hold (see FE-06)
    outcome = request_refund(db, case.id)
    assert outcome.status == RefundRequest.HELD
    assert _money(db, case) == []


def test_AT_04_an_open_dispute_takes_the_dispute_path(db, pipeline):
    case = _case(db, pipeline, amount_minor=60000)
    case.state = CaseState.DISPUTE_EVIDENCE.value
    assert request_refund(db, case.id).status == RefundRequest.DISPUTE


def test_AT_02_a_closed_case_is_not_refundable(db, pipeline):
    case = _case(db, pipeline, amount_minor=60000)
    case.state = CaseState.CLOSED.value
    assert request_refund(db, case.id).status == RefundRequest.NOT_REFUNDABLE


def test_AT_02_an_unknown_case_is_not_refundable(db):
    assert request_refund(db, "no-such-case").status == RefundRequest.NOT_REFUNDABLE
