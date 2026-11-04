"""Builds the failure-point engine's Signals from what Doorstep stored (SPEC section 7).

Every signal comes from a stored row: the checkout address check, carrier tracking events,
evidence (packing photo, locker code, signature, buyer photo) and the shipment's label weight.
A signal with no evidence stays unknown; it is never assumed true or false (FE-07).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cases.engine import AddressCheck, ClaimReason, PackingCheck, Signals
from app.config import Settings
from app.orders.enums import OrderState, ProofKind
from app.orders.models import Evidence, Order, Shipment, TrackingEvent

# Weight within this share of the packed weight counts as a match (label vs packing record).
WEIGHT_TOLERANCE = 0.10
_ADDRESS_SUBSTATUS = {"address_issue", "incorrect_address", "bad_address", "address_incorrect"}


class EvidenceKind(StrEnum):
    PACKING_PHOTO = "packing_photo"  # ai_result: {verdict, cleared?, expected_weight_g?}
    BUYER_PHOTO = "buyer_photo"  # ai_result: {damage_visible}
    SIGNATURE = "signature"
    LOCKER_CODE = "locker_code"
    DOORSTEP_PHOTO = "doorstep_photo"  # carrier photo at the buyer's address
    BUYER_CONFIRMATION = "buyer_confirmation"  # "Got it" (BC-02)


_PROOF_BY_KIND = {
    EvidenceKind.SIGNATURE.value: ProofKind.SIGNATURE,
    EvidenceKind.LOCKER_CODE.value: ProofKind.LOCKER_PICKUP_CODE,
    EvidenceKind.DOORSTEP_PHOTO.value: ProofKind.PHOTO_AT_BUYER_ADDRESS,
}


def _latest(evidence: list[Evidence], kind: EvidenceKind) -> Evidence | None:
    found = [e for e in evidence if e.kind == kind.value]
    return max(found, key=lambda e: e.created_at) if found else None


def _substatus(raw: dict[str, Any]) -> str:
    status = raw.get("tracking_status")
    status = status if isinstance(status, dict) else {}
    sub = status.get("substatus")
    return str(sub.get("code", "")).lower() if isinstance(sub, dict) else ""


def assemble_signals(
    session: Session, order: Order, claim: ClaimReason, now: datetime, settings: Settings
) -> tuple[Signals, frozenset[ProofKind]]:
    evidence = list(session.scalars(select(Evidence).where(Evidence.order_id == order.id)))
    shipment = session.scalars(
        select(Shipment).where(Shipment.order_id == order.id).order_by(Shipment.created_at)
    ).first()
    events: list[TrackingEvent] = []
    if shipment is not None:
        events = list(
            session.scalars(select(TrackingEvent).where(TrackingEvent.shipment_id == shipment.id))
        )

    proofs = {_PROOF_BY_KIND[e.kind] for e in evidence if e.kind in _PROOF_BY_KIND}
    if order.state == OrderState.PICKED_UP.value:  # picked up from a locker with the code
        proofs.add(ProofKind.LOCKER_PICKUP_CODE)
    delivered = any(e.status == "DELIVERED" for e in events)
    if delivered:
        proofs.add(ProofKind.DELIVERED_SCAN)

    stale = False
    if shipment is not None and not delivered:
        # The latest carrier event; with none yet, the time the shipment was registered.
        quiet_since = max(e.occurred_at for e in events) if events else shipment.created_at
        stale = shipment.stale_flagged_at is not None or quiet_since < now - timedelta(
            hours=settings.stale_tracking_hours
        )

    packing = _latest(evidence, EvidenceKind.PACKING_PHOTO)
    packing_result = packing.ai_result if packing is not None else {}
    verdict = str(packing_result.get("verdict", ""))
    packing_check = (
        PackingCheck(verdict)
        if verdict in {p.value for p in PackingCheck}
        else PackingCheck.MISSING
    )

    weight_matches: bool | None = None
    expected = packing_result.get("expected_weight_g")
    if shipment is not None and shipment.label_weight_g and isinstance(expected, int) and expected:
        weight_matches = abs(shipment.label_weight_g - expected) <= WEIGHT_TOLERANCE * expected

    buyer_photo = _latest(evidence, EvidenceKind.BUYER_PHOTO)
    damage = buyer_photo.ai_result.get("damage_visible") if buyer_photo is not None else None

    try:
        address_check = AddressCheck(order.address_check)
    except ValueError:
        address_check = AddressCheck.UNKNOWN
    signals = Signals(
        claim_reason=claim,
        address_check=address_check,
        carrier_address_issue=any(_substatus(e.raw) in _ADDRESS_SUBSTATUS for e in events),
        delivered_scan=delivered,
        tracking_stale=stale,
        proofs=frozenset(proofs),
        packing_check=packing_check,
        packing_mismatch_cleared=bool(packing_result.get("cleared", False)),
        buyer_photo_damage=damage if isinstance(damage, bool) else None,
        label_weight_matches=weight_matches,
    )
    return signals, frozenset(proofs)
