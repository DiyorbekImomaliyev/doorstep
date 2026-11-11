"""NV-01, MN-01, DS-01: the seller's claims, money actions and PayPal disputes as list pages.

Read only, seller link only. Rule 9: no buyer name, address, email, payer id or buyer text; an item,
an order reference, amounts and PayPal's own ids. Money stays in integer minor units with its
currency; the page totals per currency and never adds two currencies together. No PayPal call is
made here: every figure comes from rows Doorstep already stored.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.autopilot import seller_from_link
from app.api.deps import AppDeps, Services
from app.api.journey import Db
from app.api.views import latest_decision, money_status
from app.orders.enums import MoneyActionKind
from app.orders.models import AuditLog, Case, Dispute, MoneyAction, Order, Recovery
from app.proof.seller_protection import latest_reports

_OUTCOME_PREFIX = "transition: dispute resolved: "


class ClaimRow(BaseModel):
    recovery_id: str
    case_id: str
    order_id: str
    order_ref: str
    item: str
    carrier: str
    carrier_name: str
    claim_minor: int
    cover_minor: int | None
    currency: str
    status: str
    deadline_at: datetime
    recovered_minor: int | None
    submitted_at: datetime | None
    window_days: int


class MoneyRow(BaseModel):
    action_id: str
    case_id: str | None
    order_id: str
    order_ref: str
    item: str
    kind: str
    amount_minor: int
    currency: str
    status: str  # completed | pending | failed | queued | blocked | simulated | unconfirmed
    paypal_refund_id: str | None
    request_id: str | None
    created_at: datetime
    external: bool


class DisputeRow(BaseModel):
    dispute_id: str
    paypal_dispute_id: str
    order_id: str
    case_id: str | None
    order_ref: str
    item: str
    reason: str | None
    amount_minor: int
    currency: str
    status: str  # open | resolved
    route: str  # evidence | offer | waiting
    protection: str | None
    outcome: str | None
    opened_at: datetime
    simulated: bool


def _orders(s: Session, seller_id: str) -> dict[str, Order]:
    return {o.id: o for o in s.scalars(select(Order).where(Order.seller_id == seller_id))}


def build_ledger_router(deps: AppDeps, services: Services) -> APIRouter:
    router = APIRouter()
    base = "/api/seller/{token}"

    @router.get(f"{base}/claims", response_model=list[ClaimRow])
    def claims(token: str, s: Db) -> list[ClaimRow]:
        seller = seller_from_link(s, services, token)
        orders = _orders(s, seller.id)
        rows = s.scalars(
            select(Recovery)
            .where(Recovery.seller_id == seller.id)
            .order_by(Recovery.deadline_at, Recovery.id)
        )
        out: list[ClaimRow] = []
        for r in rows:
            order = orders.get(r.order_id)
            terms: dict[str, Any] = r.terms or {}
            out.append(
                ClaimRow(
                    recovery_id=r.id,
                    case_id=r.case_id,
                    order_id=r.order_id,
                    order_ref=r.order_id[:8].upper(),
                    item=order.item_description if order else "",
                    carrier=r.carrier,
                    carrier_name=str(terms.get("carrier_name") or r.carrier),
                    claim_minor=r.claim_minor,
                    cover_minor=terms.get("cover_minor"),
                    currency=r.currency,
                    status=r.status,
                    deadline_at=r.deadline_at,
                    recovered_minor=r.recovered_minor,
                    submitted_at=r.submitted_at,
                    window_days=int(terms.get("window_days") or 0),
                )
            )
        return out

    @router.get(f"{base}/money", response_model=list[MoneyRow])
    def money(token: str, s: Db) -> list[MoneyRow]:
        seller = seller_from_link(s, services, token)
        orders = _orders(s, seller.id)
        rows = s.scalars(
            select(MoneyAction)
            .where(MoneyAction.seller_id == seller.id)
            .order_by(MoneyAction.created_at.desc(), MoneyAction.id)
        )
        out: list[MoneyRow] = []
        for m in rows:
            order = orders.get(m.order_id)
            out.append(
                MoneyRow(
                    action_id=m.id,
                    case_id=m.case_id,
                    order_id=m.order_id,
                    order_ref=m.order_id[:8].upper(),
                    item=order.item_description if order else "",
                    kind=m.kind,
                    amount_minor=m.amount_minor,
                    currency=m.currency,
                    status=money_status(m),  # PR-08: a simulated refund reads "simulated"
                    paypal_refund_id=m.paypal_refund_id,
                    request_id=m.request_id,
                    created_at=m.created_at,
                    external=m.external,
                )
            )
        return out

    @router.get(f"{base}/disputes", response_model=list[DisputeRow])
    def disputes(token: str, s: Db) -> list[DisputeRow]:
        seller = seller_from_link(s, services, token)
        orders = _orders(s, seller.id)
        if not orders:
            return []
        found = list(
            s.scalars(
                select(Dispute)
                .where(Dispute.order_id.in_(list(orders)))
                .order_by(Dispute.opened_at.desc(), Dispute.id)
            )
        )
        protection = latest_reports(s, list({d.order_id for d in found}))
        out: list[DisputeRow] = []
        for d in found:
            order = orders[d.order_id]
            case = s.scalars(
                select(Case).where(Case.order_id == order.id).order_by(Case.opened_at.desc())
            ).first()
            kinds = set(
                s.scalars(
                    select(MoneyAction.kind).where(
                        MoneyAction.order_id == order.id, MoneyAction.external.is_(False)
                    )
                )
            )
            route = "waiting"
            if MoneyActionKind.OFFER.value in kinds:
                route = "offer"
            elif MoneyActionKind.EVIDENCE.value in kinds:
                route = "evidence"
            outcome = None
            if d.status == "resolved" and case is not None:
                row = s.scalars(
                    select(AuditLog)
                    .where(
                        AuditLog.entity_type == "case",
                        AuditLog.entity_id == case.id,
                        AuditLog.action.like(f"{_OUTCOME_PREFIX}%"),
                    )
                    .order_by(AuditLog.at.desc())
                ).first()
                if row is not None:
                    outcome = row.action.removeprefix(_OUTCOME_PREFIX)
                if outcome is None:
                    outcome = "Resolved at PayPal"
            decision = latest_decision(s, case.id) if case else None
            report = protection.get(order.id)
            out.append(
                DisputeRow(
                    dispute_id=d.id,
                    paypal_dispute_id=d.paypal_dispute_id,
                    order_id=order.id,
                    case_id=case.id if case else None,
                    order_ref=order.id[:8].upper(),
                    item=order.item_description,
                    reason=(decision.failure_point if decision else None)
                    or (case.reason if case else None),
                    amount_minor=order.amount_minor,
                    currency=order.currency,
                    status=d.status,
                    route=route,
                    protection=str(report["status"]) if report else None,
                    outcome=outcome,
                    opened_at=d.opened_at,
                    simulated=d.paypal_dispute_id.startswith("SIM-"),
                )
            )
        return out

    return router
