"""BC-02 / BC-03 and the case pipeline: "Got it" proves possession; "Not here" opens a case that
runs evidence -> engine -> policy -> explanation -> signed receipt."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.ai.explainer import Explainer
from app.cases.engine import AddressCheck, ClaimReason, FailurePoint, PackingCheck
from app.cases.pipeline import BuyerActionError, CasePipeline
from app.cases.signals import EvidenceKind, assemble_signals
from app.orders.enums import CaseState, OrderState, ProofKind
from app.orders.models import Case, Decision, Evidence, Order, TrackingEvent
from app.proof.models import Receipt
from app.proof.receipts import ReceiptSigner, verify_chain
from tests.factories import make_order, make_seller, make_shipment


@pytest.fixture
def signer() -> ReceiptSigner:
    return ReceiptSigner.generate()


@pytest.fixture
def pipeline(db, rulebook, clock, settings, signer) -> CasePipeline:
    return CasePipeline(db, rulebook, clock, settings, Explainer(), signer)


def _delivered(db, *, amount_minor=4000, country="GB", currency="GBP", method="home"):
    seller = make_seller(db, region=country)
    order = make_order(
        db, seller, amount_minor=amount_minor, buyer_country=country, currency=currency
    )
    order.address_check = AddressCheck.VALID.value
    shipment = make_shipment(db, order, tracking_number="SHIPPO_DELIVERED")
    shipment.method = method
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
    return order, shipment


# ---------------------------------------------------------------- BC-02 got it


def test_BC_02_got_it_confirms_and_stores_possession_proof(db, pipeline):
    order, _ = _delivered(db)
    pipeline.confirm_received(order)
    assert order.state == OrderState.CONFIRMED
    [proof] = db.scalars(
        select(Evidence).where(Evidence.kind == EvidenceKind.BUYER_CONFIRMATION)
    ).all()
    assert proof.order_id == order.id


def test_BC_02_got_it_before_the_delivered_scan_still_confirms(db, pipeline):
    order = make_order(db, make_seller(db), state=OrderState.IN_TRANSIT)
    pipeline.confirm_received(order)
    assert order.state == OrderState.CONFIRMED


def test_BC_02_got_it_twice_is_harmless(db, pipeline):
    order, _ = _delivered(db)
    pipeline.confirm_received(order)
    pipeline.confirm_received(order)
    assert order.state == OrderState.CONFIRMED
    proofs = db.scalars(select(Evidence).where(Evidence.kind == EvidenceKind.BUYER_CONFIRMATION))
    assert len(list(proofs)) == 1


def test_BC_02_unpaid_order_cannot_be_confirmed(db, pipeline):
    order = make_order(db, make_seller(db), state=OrderState.CREATED)
    with pytest.raises(BuyerActionError):
        pipeline.confirm_received(order)


# ---------------------------------------------------------------- BC-03 not here


def test_BC_03_not_here_opens_a_case_with_the_reason(db, pipeline):
    order, _ = _delivered(db)
    report = pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")
    case = db.get(Case, report.case_id)
    assert case.reason == "not_received"
    assert case.reported_by == "buyer"
    assert db.get(Order, order.id).state == OrderState.CASE_OPEN


def test_BC_03_uk_delivered_scan_without_proof_auto_refunds_under_limit(db, pipeline):
    order, _ = _delivered(db, amount_minor=4000)
    report = pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")
    assert report.failure_point == FailurePoint.CARRIER_LOST
    assert report.route == CaseState.AUTO_REFUND
    assert report.rule_id == "uk_possession_v1"
    assert report.refund_amount_minor == 4000


def test_BC_03_over_the_limit_waits_for_the_seller(db, pipeline):
    order, _ = _delivered(db, amount_minor=6000)  # Sam's £60 jacket
    report = pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")
    assert report.route == CaseState.AWAITING_SELLER


def test_BC_03_second_report_while_a_case_is_open_is_refused(db, pipeline):
    order, _ = _delivered(db)
    pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")
    with pytest.raises(BuyerActionError):
        pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")


def test_BC_03_not_here_before_shipping_is_refused(db, pipeline):
    order = make_order(db, make_seller(db), state=OrderState.PAID)
    with pytest.raises(BuyerActionError):
        pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")


def test_FE_06_empty_box_claim_contradicting_weight_and_photo_is_held(db, pipeline):
    order, _ = _delivered(db)
    report = pipeline.report(order, ClaimReason.EMPTY_BOX, reported_by="buyer")
    assert report.failure_point == FailurePoint.CLAIM_CONTRADICTS_EVIDENCE
    assert report.route == CaseState.HOLD_FOR_REVIEW  # never an automatic denial


def test_FE_04_locker_pickup_proves_possession_and_goes_to_the_seller(db, pipeline):
    order, _ = _delivered(db, method="locker")
    db.add(Evidence(order_id=order.id, kind=EvidenceKind.LOCKER_CODE, uri=""))
    db.flush()
    report = pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")
    assert report.failure_point == FailurePoint.STOLEN_AFTER_DELIVERY
    assert report.route == CaseState.AWAITING_SELLER


# ---------------------------------------------------------------- explanation + receipt


def test_EX_01_decision_explanation_names_the_rule(db, pipeline):
    order, _ = _delivered(db)
    report = pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")
    decision = db.get(Decision, report.decision_id)
    assert "uk_possession_v1" in decision.explanation
    assert report.explanation == decision.explanation
    assert decision.model_outputs["explainer"]["source"] == "template"


def test_PR_01_every_decision_gets_a_verifiable_receipt(db, pipeline, signer):
    order, _ = _delivered(db)
    report = pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")
    [receipt] = db.scalars(select(Receipt).where(Receipt.case_id == report.case_id)).all()
    assert receipt.kind == "decision"
    assert receipt.payload["decision_id"] == report.decision_id
    assert receipt.payload["rule_id"] == "uk_possession_v1"
    assert verify_chain(db, signer.public_key_b64, case_id=report.case_id).status == "ok"


# ---------------------------------------------------------------- signal assembly


def test_FE_02_signals_come_from_stored_evidence(db, settings, clock):
    order, _ = _delivered(db)
    signals, proofs = assemble_signals(db, order, ClaimReason.NOT_RECEIVED, clock.now(), settings)
    assert signals.delivered_scan is True
    assert signals.address_check == AddressCheck.VALID
    assert signals.packing_check == PackingCheck.MATCH
    assert signals.label_weight_matches is True  # 900 g label vs 900 g packed
    assert ProofKind.DELIVERED_SCAN in proofs
    assert signals.buyer_photo_damage is None  # missing, never assumed


def test_FE_07_missing_packing_photo_stays_missing(db, settings, clock):
    order = make_order(db, make_seller(db))
    make_shipment(db, order)
    signals, _ = assemble_signals(db, order, ClaimReason.NOT_RECEIVED, clock.now(), settings)
    assert signals.packing_check == PackingCheck.MISSING
    assert signals.label_weight_matches is None


def test_FE_02_quiet_tracking_becomes_stale(db, settings, clock):
    order = make_order(db, make_seller(db), state=OrderState.IN_TRANSIT)
    shipment = make_shipment(db, order)
    db.add(
        TrackingEvent(
            shipment_id=shipment.id,
            status="TRANSIT",
            occurred_at=clock.now() - timedelta(hours=settings.stale_tracking_hours + 1),
        )
    )
    db.flush()
    signals, _ = assemble_signals(db, order, ClaimReason.NOT_RECEIVED, clock.now(), settings)
    assert signals.tracking_stale is True
    assert signals.delivered_scan is False


def test_FE_01_carrier_address_issue_is_read_from_tracking(db, settings, clock):
    order = make_order(db, make_seller(db), state=OrderState.EXCEPTION)
    shipment = make_shipment(db, order)
    db.add(
        TrackingEvent(
            shipment_id=shipment.id,
            status="FAILURE",
            occurred_at=clock.now(),
            raw={"tracking_status": {"substatus": {"code": "address_issue"}}},
        )
    )
    db.flush()
    signals, _ = assemble_signals(db, order, ClaimReason.NOT_RECEIVED, clock.now(), settings)
    assert signals.carrier_address_issue is True


def test_FE_03_buyer_damage_photo_is_a_signal(db, settings, clock):
    order, _ = _delivered(db)
    db.add(
        Evidence(
            order_id=order.id, kind=EvidenceKind.BUYER_PHOTO, ai_result={"damage_visible": True}
        )
    )
    db.flush()
    signals, _ = assemble_signals(db, order, ClaimReason.DAMAGED, clock.now(), settings)
    assert signals.buyer_photo_damage is True
