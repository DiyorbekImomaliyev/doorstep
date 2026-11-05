"""Delivered-scan audit (SPEC section 7a.2, SA-01..05, ADR-036).

A carrier's "Delivered" scan usually wins every argument. This module checks that scan against
the carrier's own data: where it was made, when it was made, and what else the same carrier
marked delivered for the same seller at the same moment.

Deterministic code, never a model (SA-05). Thresholds are read from tuning/engine_config.py at
call time. A fact Doorstep does not have makes a check `unknown`, never a contradiction (SA-02);
Shippo's test carrier sends sample locations and times, so its location and batch checks are
always unknown. Only areas (postcode district or ZIP5) are used, never an address (hard rule 9).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cases.engine import POSSESSION_PROOFS
from app.geo import delivery_area
from app.orders.enums import ProofKind
from app.orders.models import Order, Shipment, TrackingEvent
from tuning import engine_config

TEST_CARRIER = "shippo"  # Shippo test mode: event locations and times are sample data
DELIVERED = "DELIVERED"
TRANSIT = "TRANSIT"


class Verdict(StrEnum):
    CONTRADICTED = "contradicted"  # a check found the carrier's own data against its scan
    CONSISTENT = "consistent"  # possession is proven (seal, signature, locker code, photo)
    UNVERIFIED = "unverified"  # a delivered scan and nothing that proves possession
    NO_SCAN = "no_scan"  # the carrier never said delivered


class CheckId(StrEnum):
    LOCATION = "location"
    SEQUENCE = "sequence"
    BATCH = "batch"
    REPORTED_SOON = "reported_soon"


class CheckResult(StrEnum):
    CONTRADICTS = "contradicts"
    SUPPORTS = "supports"
    UNKNOWN = "unknown"
    CONTEXT = "context"  # shown to people, never part of the verdict


class AuditCheck(BaseModel):
    id: CheckId
    result: CheckResult
    finding: str


class ScanAudit(BaseModel):
    verdict: Verdict
    checks: list[AuditCheck]

    @property
    def contradicted(self) -> bool:
        return self.verdict == Verdict.CONTRADICTED


class CarrierScan(BaseModel):
    model_config = ConfigDict(frozen=True)
    status: str
    occurred_at: datetime
    area: str | None = None  # where the carrier says the event happened


class SellerDelivery(BaseModel):
    """Another of the seller's parcels that the same carrier marked delivered."""

    model_config = ConfigDict(frozen=True)
    occurred_at: datetime
    area: str | None = None  # that parcel's destination area


class AuditInput(BaseModel):
    model_config = ConfigDict(frozen=True)
    carrier: str
    destination_area: str | None
    events: tuple[CarrierScan, ...] = ()
    seller_deliveries: tuple[SellerDelivery, ...] = ()
    possession_proven: bool = False
    reported_at: datetime


# ---------------------------------------------------------------- the checks (pure)


def _location(inp: AuditInput, scan: CarrierScan) -> AuditCheck:
    def result(r: CheckResult, finding: str) -> AuditCheck:
        return AuditCheck(id=CheckId.LOCATION, result=r, finding=finding)

    if inp.carrier == TEST_CARRIER:
        return result(CheckResult.UNKNOWN, "Shippo test carrier: event locations are sample data")
    if scan.area is None:
        return result(CheckResult.UNKNOWN, "the delivered scan has no location")
    if inp.destination_area is None:
        return result(CheckResult.UNKNOWN, "the order's postcode could not be read")
    if scan.area == inp.destination_area:
        return result(CheckResult.SUPPORTS, f"scanned in {scan.area}, the order's area")
    return result(
        CheckResult.CONTRADICTS,
        f"scanned as delivered in {scan.area}, but the parcel was going to {inp.destination_area}",
    )


def _sequence(inp: AuditInput, scan: CarrierScan) -> AuditCheck:
    def result(r: CheckResult, finding: str) -> AuditCheck:
        return AuditCheck(id=CheckId.SEQUENCE, result=r, finding=finding)

    skew = timedelta(seconds=engine_config.SCAN_AUDIT_FUTURE_SKEW_S)
    if scan.occurred_at > inp.reported_at + skew:
        return result(CheckResult.CONTRADICTS, "the delivered scan is dated after the report")
    transits = [e.occurred_at for e in inp.events if e.status == TRANSIT]
    if not transits:
        return result(CheckResult.UNKNOWN, "no transit scans to compare with")
    if min(transits) > scan.occurred_at:
        return result(CheckResult.CONTRADICTS, "marked delivered before its first transit scan")
    return result(CheckResult.SUPPORTS, "transit scans came before the delivered scan")


def _batch(inp: AuditInput, scan: CarrierScan) -> AuditCheck:
    def result(r: CheckResult, finding: str) -> AuditCheck:
        return AuditCheck(id=CheckId.BATCH, result=r, finding=finding)

    if inp.carrier == TEST_CARRIER:
        return result(CheckResult.UNKNOWN, "Shippo test carrier: scan times are sample data")
    window_s = engine_config.SCAN_AUDIT_BATCH_WINDOW_S
    window = timedelta(seconds=window_s)
    nearby = [d for d in inp.seller_deliveries if abs(d.occurred_at - scan.occurred_at) <= window]
    areas = {d.area for d in nearby} | {inp.destination_area}
    areas.discard(None)
    if len(areas) >= engine_config.SCAN_AUDIT_BATCH_MIN:
        return result(
            CheckResult.CONTRADICTS,
            f"{len(nearby) + 1} of this seller's parcels to {len(areas)} different areas were "
            f"marked delivered within {window_s} s of each other",
        )
    if not nearby:
        return result(
            CheckResult.SUPPORTS,
            f"no other parcel of this seller was marked delivered within {window_s} s",
        )
    others = "1 other parcel" if len(nearby) == 1 else f"{len(nearby)} other parcels"
    return result(
        CheckResult.SUPPORTS,
        f"{others} of this seller marked delivered within {window_s} s, too few areas for a batch",
    )


def _reported_soon(inp: AuditInput, scan: CarrierScan) -> AuditCheck:
    hours = (inp.reported_at - scan.occurred_at).total_seconds() / 3600
    if hours < 0:
        finding = "reported before the delivered scan's time"
    else:
        finding = f"reported {hours:.0f} h after the delivered scan"
        if hours <= engine_config.SCAN_AUDIT_REPORT_SOON_H:
            finding += f" (within {engine_config.SCAN_AUDIT_REPORT_SOON_H} h)"
    return AuditCheck(id=CheckId.REPORTED_SOON, result=CheckResult.CONTEXT, finding=finding)


def audit(inp: AuditInput) -> ScanAudit:
    """The scan audit for one order. A pure function of its input and the tuning config."""
    delivered = [e for e in inp.events if e.status == DELIVERED]
    if not delivered:
        return ScanAudit(
            verdict=Verdict.NO_SCAN,
            checks=[
                AuditCheck(id=c, result=CheckResult.UNKNOWN, finding="no delivered scan")
                for c in CheckId
            ],
        )
    scan = max(delivered, key=lambda e: e.occurred_at)  # the carrier's current claim
    checks = [
        _location(inp, scan),
        _sequence(inp, scan),
        _batch(inp, scan),
        _reported_soon(inp, scan),
    ]
    if any(c.result == CheckResult.CONTRADICTS for c in checks):
        verdict = Verdict.CONTRADICTED
    elif inp.possession_proven:
        verdict = Verdict.CONSISTENT
    else:
        verdict = Verdict.UNVERIFIED
    return ScanAudit(verdict=verdict, checks=checks)


# ---------------------------------------------------------------- stored rows -> input


def _destination(order: Order, shipment: Shipment | None) -> str | None:
    address = (shipment.delivery_address if shipment else None) or order.shipping_address or {}
    country = str(address.get("country") or order.buyer_country)
    return delivery_area(str(address.get("postal_code") or ""), country)


def _event_area(raw: dict[str, Any], default_country: str) -> str | None:
    status = raw.get("tracking_status")
    location = status.get("location") if isinstance(status, dict) else None
    if not isinstance(location, dict):
        return None
    country = str(location.get("country") or default_country)
    return delivery_area(str(location.get("zip") or ""), country)


def _seller_deliveries(
    session: Session, order: Order, shipment: Shipment, scan_at: datetime
) -> tuple[SellerDelivery, ...]:
    window = timedelta(seconds=engine_config.SCAN_AUDIT_BATCH_WINDOW_S)
    rows = session.execute(
        select(TrackingEvent.occurred_at, Shipment, Order)
        .join(Shipment, TrackingEvent.shipment_id == Shipment.id)
        .join(Order, Shipment.order_id == Order.id)
        .where(
            Order.seller_id == order.seller_id,
            Order.id != order.id,
            Shipment.carrier == shipment.carrier,
            TrackingEvent.status == DELIVERED,
            TrackingEvent.occurred_at >= scan_at - window,
            TrackingEvent.occurred_at <= scan_at + window,
        )
    ).all()
    return tuple(
        SellerDelivery(occurred_at=at, area=_destination(other, other_shipment))
        for at, other_shipment, other in rows
    )


def audit_order(
    session: Session, order: Order, proofs: frozenset[ProofKind], now: datetime
) -> ScanAudit:
    """Build the audit input from stored rows (first shipment, its events, the seller's other
    parcels with the same carrier) and audit it."""
    shipment = session.scalars(
        select(Shipment).where(Shipment.order_id == order.id).order_by(Shipment.created_at)
    ).first()
    country = order.buyer_country
    events: tuple[CarrierScan, ...] = ()
    nearby: tuple[SellerDelivery, ...] = ()
    if shipment is not None:
        stored = session.scalars(
            select(TrackingEvent).where(TrackingEvent.shipment_id == shipment.id)
        ).all()
        events = tuple(
            CarrierScan(
                status=e.status, occurred_at=e.occurred_at, area=_event_area(e.raw, country)
            )
            for e in stored
        )
        delivered = [e.occurred_at for e in events if e.status == DELIVERED]
        if delivered and shipment.carrier != TEST_CARRIER:
            nearby = _seller_deliveries(session, order, shipment, max(delivered))
    return audit(
        AuditInput(
            carrier=shipment.carrier if shipment else "",
            destination_area=_destination(order, shipment),
            events=events,
            seller_deliveries=nearby,
            possession_proven=bool(proofs & POSSESSION_PROOFS),
            reported_at=now,
        )
    )
