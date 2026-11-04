"""AT-02 mutation gaps in app/policy/agent_gate.py: the gate counts only this case's refunds,
only refund kinds, and says exactly what happened."""

from __future__ import annotations

import pytest

from app.ai.explainer import Explainer
from app.cases.engine import ClaimReason
from app.cases.pipeline import CasePipeline
from app.cases.signals import EvidenceKind
from app.orders.enums import CaseState, MoneyActionKind, OrderState
from app.orders.models import Case, Evidence, MoneyAction, Order, TrackingEvent
from app.policy.agent_gate import RefundRequest, request_refund
from app.proof.receipts import ReceiptSigner
from tests.factories import make_order, make_seller, make_shipment


@pytest.fixture
def pipeline(db, rulebook, clock, settings) -> CasePipeline:
    return CasePipeline(db, rulebook, clock, settings, Explainer(), ReceiptSigner.generate())


def _case(db, pipeline, *, amount_minor: int) -> Case:
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
    report = pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")
    return db.get(Case, report.case_id)


def test_AT_02_another_cases_refund_does_not_count(db, pipeline):
    queued_elsewhere = _case(db, pipeline, amount_minor=4000)
    over_limit = _case(db, pipeline, amount_minor=60000)
    assert request_refund(db, queued_elsewhere.id).status == RefundRequest.QUEUED
    assert request_refund(db, over_limit.id).status == RefundRequest.ESCALATED


def test_AT_02_a_dispute_evidence_action_is_not_a_refund(db, pipeline):
    case = _case(db, pipeline, amount_minor=60000)
    order = db.get(Order, case.order_id)
    case.state = CaseState.DISPUTE_EVIDENCE.value
    db.add(
        MoneyAction(
            order_id=order.id,
            case_id=case.id,
            seller_id=order.seller_id,
            kind=MoneyActionKind.EVIDENCE.value,
            amount_minor=0,
            currency=order.currency,
            request_id=f"case:{case.id}:evidence",
            status="queued",
        )
    )
    db.flush()
    assert request_refund(db, case.id).status == RefundRequest.DISPUTE


def test_AT_02_a_partial_refund_counts_as_queued(db, pipeline):
    case = _case(db, pipeline, amount_minor=60000)
    order = db.get(Order, case.order_id)
    db.add(
        MoneyAction(
            order_id=order.id,
            case_id=case.id,
            seller_id=order.seller_id,
            kind=MoneyActionKind.PARTIAL_REFUND.value,
            amount_minor=1000,
            currency=order.currency,
            request_id=f"case:{case.id}:partial",
            status="QUEUED",
        )
    )
    db.flush()
    assert request_refund(db, case.id).status == RefundRequest.QUEUED


def test_AT_02_the_queued_message_is_exact(db, pipeline):
    case = _case(db, pipeline, amount_minor=4000)
    outcome = request_refund(db, case.id)
    status = db.get(MoneyAction, outcome.money_action_id).status
    assert outcome.message == (
        f"Refund {status.lower()} under the seller's rules "
        "(policy decision, idempotent PayPal request)."
    )
    assert status.lower() != status.upper()  # the status word is cased, so .lower() matters
