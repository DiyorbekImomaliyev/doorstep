"""Evidence notes for a PayPal dispute (DE-02): facts only, built in code.

The notes hold the carrier and tracking number, the tracking events, the time of a counted seal
scan, the delivered-scan audit verdict and the link where anyone can verify Doorstep's signed
receipts. Never an address, never the buyer's own words, never an accusation (hard rules 7-9).
A seal that was not scanned is not mentioned: not scanning proves nothing.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.explainer import _ACCUSING
from app.orders.models import Case, Decision, Order, Shipment, TrackingEvent
from app.policy.carriers import normalise
from app.policy.evidence import NOTES_MAX, EvidenceSubmission
from app.proof.seal import proof_scan

# Carrier codes PayPal accepts in tracking_info; any other carrier is sent as OTHER.
PAYPAL_CARRIER = {"royal_mail": "ROYAL_MAIL", "usps": "USPS", "ups": "UPS", "fedex": "FEDEX"}
_MAX_EVENTS = 8


class UnsafeEvidence(ValueError):
    """The notes would contain accusing words: never sent (EX-02). Code-built, so not expected."""


def _when(at: datetime) -> str:
    return at.strftime("%Y-%m-%d %H:%M UTC")


def build_submission(
    session: Session, order: Order, case: Case, decision: Decision, verify_url: str
) -> EvidenceSubmission:
    shipment = session.scalars(
        select(Shipment).where(Shipment.order_id == order.id).order_by(Shipment.created_at)
    ).first()
    lines = [
        f"Doorstep evidence for order {order.id[:8].upper()}, from carrier tracking and "
        "Doorstep's signed records."
    ]
    if shipment is not None:
        lines.append(f"Carrier: {shipment.carrier}; tracking number: {shipment.tracking_number}.")
        events = session.scalars(
            select(TrackingEvent)
            .where(TrackingEvent.shipment_id == shipment.id)
            .order_by(TrackingEvent.occurred_at)
        ).all()
        if events:
            lines.append(
                "Tracking: "
                + "; ".join(f"{e.status} {_when(e.occurred_at)}" for e in events[-_MAX_EVENTS:])
                + "."
            )
    seal = proof_scan(session, order.id)  # SL-08: only a scan after the delivered scan
    if seal is not None:
        lines.append(
            f"Possession seal: the QR code packed inside the parcel was scanned on "
            f"{_when(seal.created_at)}, after the carrier's delivered scan."
        )
    # The audit goes in only when the carrier's data agrees with itself; the raw tracking above is
    # there either way, so nothing the carrier reported is held back.
    audit = decision.signals.get("scan_audit") or {}
    if audit.get("verdict") == "consistent":
        lines.append("Delivered-scan audit of the carrier's data: consistent.")
    lines.append(f"Doorstep's signed receipts for this case can be verified at {verify_url}.")
    notes = "\n".join(lines)[:NOTES_MAX]
    if _ACCUSING.search(notes):
        raise UnsafeEvidence("evidence notes contain accusing words")
    carrier = PAYPAL_CARRIER.get(normalise(shipment.carrier)) if shipment else None
    return EvidenceSubmission(
        notes=notes,
        carrier=carrier,
        tracking_number=shipment.tracking_number if shipment else None,
    )
