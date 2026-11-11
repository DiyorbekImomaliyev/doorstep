"""CL-02 .. CL-07: the claims desk, one read-only view of every carrier claim and its story.

Seller link only, scoped like app/api/ledger.py: another seller's link sees nothing, a buyer or
forged link gets 404. Rule 9: no buyer name, address, email, payer id or buyer text. The claim pack
sentences and facts are the ones the case drawer already shows (districts only, code-built). Money
stays in integer minor units with its currency. Nothing here writes: the page records a claim's
outcome through the existing POST /api/seller/{token}/cases/{case_id}/recovery/{mark}.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.api.autopilot import seller_from_link
from app.api.deps import AppDeps, Services
from app.api.journey import Db
from app.api.views import ClaimSentenceView, FactView, recovery_view
from app.orders.enums import MoneyActionKind, MoneyActionStatus
from app.orders.models import AuditLog, Case, MoneyAction, Order, Recovery
from app.worker.simulated import is_simulated

Actor = Literal["you", "doorstep", "carrier", "demo"]
_REFUNDS = (MoneyActionKind.REFUND.value, MoneyActionKind.PARTIAL_REFUND.value)
_PREFIX = "recovery."


class DeskEvent(BaseModel):
    """One status change of a claim, from its audit row."""

    status: str  # PREPARED | SUBMITTED | PAID | DENIED | WITHDRAWN | EXPIRED
    at: datetime
    by: Actor
    note: str | None  # the code-written reason of an automatic change; never buyer text
    amount_minor: int | None  # what the carrier paid, on the PAID event


class DeskRow(BaseModel):
    recovery_id: str
    case_id: str
    order_id: str
    order_ref: str
    item: str
    carrier: str
    carrier_name: str
    carrier_known: bool
    failure_point: str
    claim_minor: int
    cover_minor: int | None
    item_minor: int
    currency: str
    status: str
    deadline_at: datetime
    window_days: int
    window_starts: str
    service_basis: str
    recovered_minor: int | None
    created_at: datetime
    submitted_at: datetime | None
    resolved_at: datetime | None
    terms_url: str
    terms_note: str
    terms_rule: str
    sentences: list[ClaimSentenceView]
    facts: list[FactView]
    pack_source: str
    case_state: str | None
    refunded_minor: int  # refunds to the buyer on this case that went out (or were simulated)
    simulated: bool  # a demo order: its refunds and carrier history are simulated
    actions: list[str]
    history: list[DeskEvent]


class ClaimsDesk(BaseModel):
    now: datetime  # the server's clock, so days left match the deadlines it stored
    rows: list[DeskRow]


def _actor(actor: str) -> Actor:
    if actor.startswith("seller:"):
        return "you"
    if actor == "carrier":
        return "carrier"
    if actor == "demo-seed":
        return "demo"
    return "doorstep"  # pipeline, sweep


def _event(row: AuditLog) -> DeskEvent:
    after: dict[str, Any] = row.after or {}
    status = str(after.get("status") or row.action.removeprefix(_PREFIX).upper())
    by = _actor(row.actor)
    reason = after.get("reason")
    return DeskEvent(
        status=status,
        at=row.at,
        by=by,
        note=str(reason) if by in ("doorstep", "carrier") and reason else None,
        amount_minor=after.get("recovered_minor") if status == "PAID" else None,
    )


def build_claims_desk_router(deps: AppDeps, services: Services) -> APIRouter:
    router = APIRouter()

    @router.get("/api/seller/{token}/claims/desk", response_model=ClaimsDesk)
    def claims_desk(token: str, s: Db) -> ClaimsDesk:
        seller = seller_from_link(s, services, token)
        recoveries = list(
            s.scalars(
                select(Recovery)
                .where(Recovery.seller_id == seller.id)
                .order_by(Recovery.deadline_at, Recovery.id)
            )
        )
        if not recoveries:
            return ClaimsDesk(now=deps.clock.now(), rows=[])
        ids = [r.id for r in recoveries]
        order_ids = list({r.order_id for r in recoveries})
        case_ids = list({r.case_id for r in recoveries})
        orders = {o.id: o for o in s.scalars(select(Order).where(Order.id.in_(order_ids)))}
        cases = {c.id: c for c in s.scalars(select(Case).where(Case.id.in_(case_ids)))}
        history: dict[str, list[DeskEvent]] = defaultdict(list)
        for row in s.scalars(
            select(AuditLog)
            .where(AuditLog.entity_type == "recovery", AuditLog.entity_id.in_(ids))
            .order_by(AuditLog.at, AuditLog.id)
        ):
            if row.action.startswith(_PREFIX):
                history[row.entity_id].append(_event(row))
        refunded: dict[str, int] = defaultdict(int)
        for m in s.scalars(
            select(MoneyAction).where(
                MoneyAction.case_id.in_(case_ids),
                MoneyAction.kind.in_(_REFUNDS),
                MoneyAction.status == MoneyActionStatus.COMPLETED.value,
            )
        ):
            refunded[str(m.case_id)] += m.amount_minor
        rows: list[DeskRow] = []
        for r in recoveries:
            v = recovery_view(r)
            order = orders.get(r.order_id)
            case = cases.get(r.case_id)
            terms: dict[str, Any] = r.terms or {}
            rows.append(
                DeskRow(
                    recovery_id=r.id,
                    case_id=r.case_id,
                    order_id=r.order_id,
                    order_ref=r.order_id[:8].upper(),
                    item=order.item_description if order else "",
                    carrier=r.carrier,
                    carrier_name=v.carrier_name,
                    carrier_known=v.carrier_known,
                    failure_point=r.failure_point,
                    claim_minor=r.claim_minor,
                    cover_minor=v.cover_minor,
                    item_minor=int(terms.get("item_minor") or (order.amount_minor if order else 0)),
                    currency=r.currency,
                    status=r.status,
                    deadline_at=r.deadline_at,
                    window_days=v.window_days,
                    window_starts=str(terms.get("window_starts") or ""),
                    service_basis=str(terms.get("service_basis") or ""),
                    recovered_minor=r.recovered_minor,
                    created_at=r.created_at,
                    submitted_at=r.submitted_at,
                    resolved_at=r.resolved_at,
                    terms_url=v.terms_url,
                    terms_note=v.terms_note,
                    terms_rule=v.terms_rule,
                    sentences=v.sentences,
                    facts=v.facts,
                    pack_source=v.pack_source,
                    case_state=case.state if case else None,
                    refunded_minor=refunded.get(r.case_id, 0),
                    simulated=is_simulated(order.capture_id) if order else False,
                    actions=v.actions,
                    history=history.get(r.id, []),
                )
            )
        return ClaimsDesk(now=deps.clock.now(), rows=rows)

    return router
