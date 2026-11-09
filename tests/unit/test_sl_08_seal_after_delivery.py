"""SL-08: a seal scan proves possession only after the carrier's delivered scan.

Found by the Nov 7 review: a scan while the parcel was in transit counted as the buyer's possession,
and anyone holding the printed code (the seller printed it) could make one. A scan now proves
possession only when the carrier has scanned the parcel as delivered and the seal scan came after
that. A scan that does not count leaves the buyer's case to the other evidence, never against them.
Dispute evidence cites only what helps the seller's case: the seal after delivery, and the scan
audit only when the carrier's data agrees with itself.
"""

from __future__ import annotations

from datetime import timedelta

from app.cases.dispute_evidence import build_submission
from app.cases.engine import ClaimReason
from app.cases.signals import assemble_signals
from app.orders.enums import OrderState, ProofKind
from app.orders.models import Decision, TrackingEvent
from app.proof.seal import record_scan
from tests.factories import make_case, make_order, make_seller, make_shipment
from tests.unit.test_sl_seal import _settings


def _order(db, clock, events):
    order = make_order(db, make_seller(db), state=OrderState.IN_TRANSIT)
    shipment = make_shipment(db, order, tracking_number="SHIPPO_TRANSIT")
    for status, hours_ago in events:
        db.add(
            TrackingEvent(
                shipment_id=shipment.id,
                status=status,
                occurred_at=clock.now() - timedelta(hours=hours_ago),
                applied=True,
            )
        )
    db.flush()
    return order, shipment


def _proofs(db, clock, order):
    _, proofs = assemble_signals(db, order, ClaimReason.NOT_RECEIVED, clock.now(), _settings())
    return proofs


def test_SL_08_a_scan_while_in_transit_is_not_possession_proof(db, clock):
    order, _ = _order(db, clock, [("TRANSIT", 5)])
    scan = record_scan(db, order, clock.now())
    assert scan.ai_result["counted"] is True  # recorded as after handover (SL-04 is unchanged)
    assert ProofKind.BUYER_SEAL_SCAN not in _proofs(db, clock, order)


def test_SL_08_a_transit_scan_stays_unproven_after_the_parcel_is_delivered(db, clock):
    order, shipment = _order(db, clock, [("TRANSIT", 5)])
    record_scan(db, order, clock.now() - timedelta(hours=2))
    db.add(TrackingEvent(shipment_id=shipment.id, status="DELIVERED", occurred_at=clock.now()))
    db.flush()
    assert ProofKind.BUYER_SEAL_SCAN not in _proofs(db, clock, order)


def test_SL_08_a_scan_after_the_delivered_scan_is_possession_proof(db, clock):
    order, _ = _order(db, clock, [("TRANSIT", 5), ("DELIVERED", 3)])
    record_scan(db, order, clock.now())
    assert ProofKind.BUYER_SEAL_SCAN in _proofs(db, clock, order)


def _decision(db, order, audit_verdict):
    case = make_case(db, order)
    decision = Decision(
        case_id=case.id,
        route="DISPUTE_EVIDENCE",
        signals={"scan_audit": {"verdict": audit_verdict}},
    )
    db.add(decision)
    db.flush()
    return case, decision


def test_SL_08_evidence_cites_only_a_seal_scan_after_delivery(db, clock):
    order, shipment = _order(db, clock, [("TRANSIT", 5)])
    record_scan(db, order, clock.now() - timedelta(hours=2))
    db.add(TrackingEvent(shipment_id=shipment.id, status="DELIVERED", occurred_at=clock.now()))
    db.flush()
    case, decision = _decision(db, order, "consistent")
    notes = build_submission(db, order, case, decision, "https://x/verify").notes
    assert "Possession seal" not in notes


def test_SL_08_evidence_leaves_out_an_audit_that_contradicts_delivery(db, clock):
    order, _ = _order(db, clock, [("TRANSIT", 5), ("DELIVERED", 3)])
    case, decision = _decision(db, order, "contradicted")
    notes = build_submission(db, order, case, decision, "https://x/verify").notes
    assert "audit" not in notes.lower()
    assert "DELIVERED" in notes  # the carrier's own tracking is still there
