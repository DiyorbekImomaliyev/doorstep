"""AS-01: the seller's data as related tables for the AG Studio Insights dashboard.

Read only. Rule 9 (minimum personal data): a postcode district and a country, never an address,
a name, a payer id or the buyer's own words. Money stays in integer minor units with its ISO 4217
code; the browser divides for display and never adds two currencies together.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.views import evidence_completeness, latest_decision, postcode_district
from app.orders.models import Case, MoneyAction, Order, Recovery, Seller, Shipment, TrackingEvent
from app.worker.simulated import is_simulated


class InsightOrder(BaseModel):
    order_id: str
    order_ref: str
    created_at: datetime
    item: str
    amount_minor: int
    currency: str
    buyer_country: str
    buyer_district: str
    method: str | None
    recommended_method: str | None
    risk: float | None
    address_check: str
    state: str
    evidence_score: float
    simulated: bool


class InsightShipment(BaseModel):
    shipment_id: str
    order_id: str
    carrier: str
    method: str
    shipped_at: datetime
    delivered_at: datetime | None
    events: int


class InsightCase(BaseModel):
    case_id: str
    order_id: str
    opened_at: datetime
    reason: str
    state: str
    failure_point: str | None
    confidence: str | None
    route: str | None
    liable_party: str | None
    rule_id: str | None


class InsightMoney(BaseModel):
    action_id: str
    order_id: str
    case_id: str | None
    kind: str
    amount_minor: int
    currency: str
    status: str
    external: bool
    created_at: datetime


class InsightRecovery(BaseModel):
    recovery_id: str
    case_id: str
    order_id: str
    carrier: str
    failure_point: str
    claim_minor: int
    recovered_minor: int | None
    currency: str
    status: str
    deadline_at: datetime


class InsightsOut(BaseModel):
    seller_region: str
    orders: list[InsightOrder]
    shipments: list[InsightShipment]
    cases: list[InsightCase]
    money_actions: list[InsightMoney]
    recoveries: list[InsightRecovery]


def _liable_party(signals: dict[str, Any]) -> str | None:
    liability: dict[str, Any] = signals.get("liability") or {}
    party = liability.get("liable_party")
    return str(party) if party else None


def insights(s: Session, seller: Seller) -> InsightsOut:
    orders = list(s.scalars(select(Order).where(Order.seller_id == seller.id)))
    order_ids = [o.id for o in orders]
    shipments = list(s.scalars(select(Shipment).where(Shipment.order_id.in_(order_ids))))
    events: dict[str, list[TrackingEvent]] = {}
    for e in s.scalars(
        select(TrackingEvent).where(TrackingEvent.shipment_id.in_([x.id for x in shipments]))
    ):
        events.setdefault(e.shipment_id, []).append(e)
    cases = list(s.scalars(select(Case).where(Case.seller_id == seller.id)))
    money = s.scalars(select(MoneyAction).where(MoneyAction.seller_id == seller.id))
    recoveries = s.scalars(select(Recovery).where(Recovery.seller_id == seller.id))

    def delivered_at(shipment_id: str) -> datetime | None:
        times = [
            e.occurred_at
            for e in events.get(shipment_id, [])
            if e.status.upper() in ("DELIVERED", "PICKED_UP")
        ]
        return min(times) if times else None

    def case_row(c: Case) -> InsightCase:
        d = latest_decision(s, c.id)
        return InsightCase(
            case_id=c.id,
            order_id=c.order_id,
            opened_at=c.opened_at,
            reason=c.reason,
            state=c.state,
            failure_point=d.failure_point if d else None,
            confidence=d.confidence if d else None,
            route=d.route if d else None,
            liable_party=_liable_party(d.signals) if d else None,
            rule_id=d.rule_id if d else None,
        )

    return InsightsOut(
        seller_region=seller.region,
        orders=[
            InsightOrder(
                order_id=o.id,
                order_ref=o.id[:8].upper(),
                created_at=o.created_at,
                item=o.item_description,
                amount_minor=o.amount_minor,
                currency=o.currency,
                buyer_country=o.buyer_country,
                buyer_district=postcode_district(str(o.shipping_address.get("postal_code", ""))),
                method=o.shipping_method,
                recommended_method=o.recommended_method,
                risk=o.risk_score,
                address_check=o.address_check,
                state=o.state,
                evidence_score=evidence_completeness(s, o).score,
                simulated=is_simulated(o.capture_id),
            )
            for o in orders
        ],
        shipments=[
            InsightShipment(
                shipment_id=x.id,
                order_id=x.order_id,
                carrier=x.carrier,
                method=x.method,
                shipped_at=x.created_at,
                delivered_at=delivered_at(x.id),
                events=len(events.get(x.id, [])),
            )
            for x in shipments
        ],
        cases=[case_row(c) for c in cases],
        money_actions=[
            InsightMoney(
                action_id=m.id,
                order_id=m.order_id,
                case_id=m.case_id,
                kind=m.kind,
                amount_minor=m.amount_minor,
                currency=m.currency,
                status=m.status,
                external=m.external,
                created_at=m.created_at,
            )
            for m in money
        ],
        recoveries=[
            InsightRecovery(
                recovery_id=r.id,
                case_id=r.case_id,
                order_id=r.order_id,
                carrier=r.carrier,
                failure_point=r.failure_point,
                claim_minor=r.claim_minor,
                recovered_minor=r.recovered_minor,
                currency=r.currency,
                status=r.status,
                deadline_at=r.deadline_at,
            )
            for r in recoveries
        ],
    )
