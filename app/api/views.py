"""Read models for the screens: buyer tracker, Control Room rows, case page.

Views show what a person needs to act, nothing more. Addresses are masked to the postcode
district in seller views. Status words never overclaim (PR-08): a simulated
refund is labelled simulated, a receipt chain that was not checked says not_run.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.ai.explainer import money_text, source_name
from app.ai.risk import RiskFeatures, load_model
from app.cases.signals import EvidenceKind
from app.checkout.risk import seller_case_rate, seller_order_count
from app.geo import postcode_district  # re-exported: shown in the Control Room
from app.money import MINOR_UNIT_EXPONENT, Money, parse_currency
from app.orders.enums import CaseState, MoneyActionKind, MoneyActionStatus, OrderState
from app.orders.models import (
    AuditLog,
    Case,
    Decision,
    Dispute,
    Evidence,
    MoneyAction,
    Order,
    Recovery,
    Seller,
    Shipment,
    TrackingEvent,
)
from app.policy.offers import OFFER_MAYBE_MADE, offer_amount
from app.policy.service import refund_in_flight
from app.proof.models import Receipt
from app.proof.seal import AFTER_HANDOVER, BEFORE_HANDOVER, SELLER_BROWSER
from app.proof.seller_protection import ACTION as SELLER_PROTECTION_ACTION
from app.proof.seller_protection import latest_reports
from app.worker.simulated import SIMULATED_STATUS, is_simulated

_OPEN_CASE = [s.value for s in CaseState if s not in (CaseState.SETTLED, CaseState.CLOSED)]
_IN_FLIGHT = {
    OrderState.SHIPPED.value,
    OrderState.IN_TRANSIT.value,
    OrderState.EXCEPTION.value,
}
_POSSESSION = {
    EvidenceKind.SIGNATURE.value,
    EvidenceKind.LOCKER_CODE.value,
    EvidenceKind.DOORSTEP_PHOTO.value,
    EvidenceKind.BUYER_CONFIRMATION.value,
}

_CARRIER_DRIVEN = {"SHIPPED", "IN_TRANSIT", "EXCEPTION", "DELIVERED", "PICKED_UP"}

# Buyer-facing status words (plain language, no internal state names).
BUYER_STATUS = {
    "CREATED": "Waiting for payment",
    "PAID": "Paid, being packed",
    "PACKED": "Packed",
    "SHIPPED": "Shipped",
    "IN_TRANSIT": "On its way",
    "EXCEPTION": "Delivery problem",
    "DELIVERED": "Marked as delivered",
    "PICKED_UP": "Collected from locker",
    "CONFIRMED": "Received",
    "CASE_OPEN": "We're sorting it out",
    "RESOLVED": "Resolved",
}
# A capture that did not complete leaves the order CREATED; these words say why (PP-03).
BUYER_PAYMENT = {
    "FAILED": "Payment didn't go through",
    "DECLINED": "Payment didn't go through",
    "PENDING": "Payment processing",
}
SELLER_PAYMENT = {
    "FAILED": "Payment failed",
    "DECLINED": "Payment declined",
    "PENDING": "Payment pending at PayPal",
}


def _status(order: Order, words: dict[str, str], payment: dict[str, str]) -> str:
    if order.state == OrderState.CREATED.value and order.capture_status in payment:
        return payment[order.capture_status]
    return words.get(order.state, order.state)


SELLER_STATUS = {
    **BUYER_STATUS,
    "PAID": "Paid, ready to pack",
    "PACKED": "Packed, ready to ship",
    "CASE_OPEN": "Case open",
}


# ---------------------------------------------------------------- shared lookups


def latest_shipment(s: Session, order_id: str) -> Shipment | None:
    return s.scalars(
        select(Shipment).where(Shipment.order_id == order_id).order_by(Shipment.created_at.desc())
    ).first()


def latest_case(s: Session, order_id: str) -> Case | None:
    return s.scalars(
        select(Case).where(Case.order_id == order_id).order_by(Case.opened_at.desc())
    ).first()


def latest_decision(s: Session, case_id: str) -> Decision | None:
    return s.scalars(
        select(Decision).where(Decision.case_id == case_id).order_by(Decision.created_at.desc())
    ).first()


def money_status(ma: MoneyAction) -> str:
    """PR-08: a simulated refund never reads as a PayPal refund. PP-09: a dispute offer that was
    sent but not confirmed reads as "unconfirmed", not "failed": it may be live at PayPal."""
    if ma.paypal_status == SIMULATED_STATUS:
        return "simulated"
    if ma.status == MoneyActionStatus.FAILED.value and ma.paypal_status in OFFER_MAYBE_MADE:
        return "unconfirmed"
    return ma.status


_REFUND_KINDS = (MoneyActionKind.REFUND.value, MoneyActionKind.PARTIAL_REFUND.value)
_DEMO_REFUND = "This is a demo shop, so the refund is simulated: no real money moved."


def buyer_outcome(s: Session, order: Order, case: Case) -> str | None:
    """UI-01: what has happened to the buyer's money since the decision, in the buyer's words.

    None until the case has a refund of its own, so the decision's message stands until then.
    PR-08: a simulated refund (a demo order) says so and never claims PayPal sent it.
    """
    refund = s.scalars(
        select(MoneyAction)
        .where(
            MoneyAction.case_id == case.id,
            MoneyAction.kind.in_(_REFUND_KINDS),
            MoneyAction.external.is_(False),
        )
        .order_by(MoneyAction.created_at.desc())
    ).first()
    sent = refund is not None and refund.status == MoneyActionStatus.COMPLETED.value
    if case.state == CaseState.CLOSED.value and not sent:
        if _dispute_resolved(s, order.id):  # PO-14
            return "PayPal resolved your dispute. Its decision is in your PayPal account."
        return (  # PO-15: the seller declined, or a person closed it after checking
            "The seller checked your report and closed this case without a refund. If you "
            "disagree, you can open a PayPal dispute, and PayPal decides."
        )
    if refund is None:
        return None
    amount = money_text(
        Money(amount_minor=refund.amount_minor, currency=parse_currency(refund.currency))
    )
    simulated = is_simulated(order.capture_id)
    if refund.status == MoneyActionStatus.COMPLETED.value:
        when = refund.completed_at or refund.created_at
        day = f"{when.day} {when:%b}"
        if simulated:
            return f"Your refund of {amount} was sent on {day}. {_DEMO_REFUND}"
        return f"Your refund of {amount} was sent to your PayPal on {day}."
    if refund.status == MoneyActionStatus.PENDING.value:  # PO-13: PayPal confirms it later
        return (
            f"The seller sent your refund of {amount}. PayPal is still processing it; it reaches "
            "your PayPal when PayPal confirms it."
        )
    if refund.status == MoneyActionStatus.QUEUED.value:
        if simulated:
            return f"The seller approved your refund of {amount}. {_DEMO_REFUND}"
        return f"The seller approved your refund of {amount}. It is on its way to your PayPal."
    return "A person is checking the refund before it is sent. Nothing has been refused."


class EvidenceScore(BaseModel):
    score: float
    present: list[str]
    missing: list[str]


@dataclass(frozen=True)
class _Proof:
    """The proof rows of a list of orders, loaded in three queries for any number of orders."""

    evidence: dict[str, list[Evidence]]  # per order, oldest first
    shipment: dict[str, Shipment]  # the latest per order
    delivered: set[str]  # shipment ids with a DELIVERED scan


def _latest_by[T](rows: Iterable[T], key: Callable[[T], str]) -> dict[str, T]:
    """The first row per key; rows arrive newest first, as the single-order queries read them."""
    latest: dict[str, T] = {}
    for row in rows:
        latest.setdefault(key(row), row)
    return latest


def _load_proof(s: Session, order_ids: Sequence[str]) -> _Proof:
    evidence: dict[str, list[Evidence]] = defaultdict(list)
    for e in s.scalars(
        select(Evidence).where(Evidence.order_id.in_(order_ids)).order_by(Evidence.created_at)
    ):
        evidence[e.order_id].append(e)
    shipment = _latest_by(
        s.scalars(
            select(Shipment)
            .where(Shipment.order_id.in_(order_ids))
            .order_by(Shipment.created_at.desc())
        ),
        lambda x: x.order_id,
    )
    delivered = set(
        s.scalars(
            select(TrackingEvent.shipment_id).where(
                TrackingEvent.shipment_id.in_([x.id for x in shipment.values()]),
                TrackingEvent.status == "DELIVERED",
            )
        )
    )
    return _Proof(evidence=dict(evidence), shipment=shipment, delivered=delivered)


def _evidence_score(order: Order, proof: _Proof) -> EvidenceScore:
    rows = proof.evidence.get(order.id, [])
    # SL-08: a confirmation from the seller's own browser is not the buyer's.
    kinds = {
        e.kind
        for e in rows
        if not (
            e.kind == EvidenceKind.BUYER_CONFIRMATION.value
            and (e.ai_result or {}).get("source") == SELLER_BROWSER
        )
    }
    shipment = proof.shipment.get(order.id)
    delivered = shipment is not None and shipment.id in proof.delivered
    counted = any(
        e.kind == EvidenceKind.SEAL_SCAN.value and e.ai_result.get("counted") for e in rows
    )
    checks = {
        "address checked": order.address_check in ("valid", "overridden"),
        "packing photo": EvidenceKind.PACKING_PHOTO.value in kinds,
        "tracking": shipment is not None,
        "delivered scan": delivered or order.state == OrderState.PICKED_UP.value,
        "possession proof": bool(kinds & _POSSESSION)
        or order.state == OrderState.PICKED_UP.value
        or counted,
    }
    present = [k for k, ok in checks.items() if ok]
    missing = [k for k, ok in checks.items() if not ok]
    return EvidenceScore(
        score=round(len(present) / len(checks), 2), present=present, missing=missing
    )


def evidence_scores(s: Session, orders: Sequence[Order]) -> dict[str, EvidenceScore]:
    """Evidence completeness for many orders in three queries (not three per order)."""
    proof = _load_proof(s, [o.id for o in orders])
    return {o.id: _evidence_score(o, proof) for o in orders}


def evidence_completeness(s: Session, order: Order) -> EvidenceScore:
    return evidence_scores(s, [order])[order.id]


# ---------------------------------------------------------------- timeline


class TimelineItem(BaseModel):
    at: datetime
    kind: str  # state | carrier | evidence | money | receipt | decision
    title: str
    detail: str = ""


_AGENT_TITLES = {
    "get_order_details": "read the PayPal order",
    "get_shipment_tracking": "read tracking on the PayPal payment",
    "list_disputes": "checked PayPal disputes",
    "get_dispute": "read the PayPal dispute",
    "request_refund": "asked the policy for the refund",
    "finish": "finished",
}


def rule_label(source: str) -> str:
    """The law or policy a rule encodes, short enough for a sentence: "Consumer Rights Act 2015
    s.29", "Directive 2011/83/EU Art. 20", "Seller written delivery policy"."""
    name = source_name(source)
    return re.split(r";|\s\(|,\s+via\s", name, maxsplit=1)[0].strip()


# A case state in the seller's words, for the timeline.
_CASE_TITLE = {
    CaseState.OPEN.value: "Problem reported",
    CaseState.GATHERING_EVIDENCE.value: "Gathering the evidence",
    CaseState.DECIDED.value: "Decided",
    CaseState.AUTO_REFUND.value: "Refunded by your rule",
    CaseState.AWAITING_SELLER.value: "Waiting for your decision",
    CaseState.HOLD_FOR_REVIEW.value: "Held for a person to check",
    CaseState.DISPUTE_EVIDENCE.value: "PayPal dispute: evidence needed",
    CaseState.SETTLED.value: "Settled",
    CaseState.CLOSED.value: "Closed",
}


def _state_title(entity: str, after: dict[str, Any], action: str) -> str:
    state = after.get("state")
    if entity == "order" and isinstance(state, str):
        return BUYER_STATUS.get(state, state)
    if entity == "case" and isinstance(state, str):
        return _CASE_TITLE.get(state, "Case " + state.replace("_", " ").lower())
    return action.replace(".", " ").replace("_", " ")


_SEAL_DETAIL = {  # SL-04, SL-07: why a seal scan did or did not count
    AFTER_HANDOVER: "counted as possession proof",
    BEFORE_HANDOVER: "not counted: scanned before the carrier had the parcel",
    SELLER_BROWSER: "not counted: scanned in your own browser",
}


def timeline(s: Session, order: Order, *, for_buyer: bool) -> list[TimelineItem]:
    items: list[TimelineItem] = []
    case_ids = list(s.scalars(select(Case.id).where(Case.order_id == order.id)))
    shipment_ids = list(s.scalars(select(Shipment.id).where(Shipment.order_id == order.id)))
    audit = s.scalars(
        select(AuditLog).where(
            or_(
                (AuditLog.entity_type == "order") & (AuditLog.entity_id == order.id),
                (AuditLog.entity_type == "case") & AuditLog.entity_id.in_(case_ids),
                (AuditLog.entity_type == "shipment") & AuditLog.entity_id.in_(shipment_ids),
            )
        )
    )
    has_carrier_events = bool(shipment_ids) and (
        s.scalar(
            select(TrackingEvent.id).where(TrackingEvent.shipment_id.in_(shipment_ids)).limit(1)
        )
        is not None
    )
    for row in audit:
        if row.action == SELLER_PROTECTION_ACTION:
            if not for_buyer:  # PP-11: PayPal's verdict, for the seller only
                items.append(
                    TimelineItem(
                        at=row.at,
                        kind="money",
                        title="PayPal Seller Protection",
                        detail=str(row.after.get("status", "")).replace("_", " ").lower(),
                    )
                )
            continue
        carrier_state = row.after.get("state") in _CARRIER_DRIVEN
        if row.entity_type == "order" and carrier_state and has_carrier_events:
            continue  # the carrier event itself is shown; skip the duplicate state row
        if row.entity_type == "order" and "state" in row.after:
            items.append(
                TimelineItem(
                    at=row.at, kind="state", title=_state_title("order", row.after, row.action)
                )
            )
        elif row.entity_type == "case" and "agent" in row.after:
            if not for_buyer:  # AT-05: the agent's steps, for the seller only
                items.append(
                    TimelineItem(
                        at=row.at,
                        kind="agent",
                        title=f"Agent: {_AGENT_TITLES.get(row.after['agent'], row.after['agent'])}",
                        detail=str(row.after.get("summary", "")),
                    )
                )
        elif row.entity_type == "case" and not for_buyer:
            items.append(
                TimelineItem(
                    at=row.at,
                    kind="state",
                    title=_state_title("case", row.after, row.action),
                    detail=row.action.replace("_", " "),
                )
            )
        elif row.entity_type == "shipment":
            items.append(
                TimelineItem(at=row.at, kind="carrier", title=row.action.replace(".", " "))
            )
    for ev in s.scalars(select(TrackingEvent).where(TrackingEvent.shipment_id.in_(shipment_ids))):
        # Real Shippo events carry "substatus": null (and may omit tracking_status): read safely.
        status = ev.raw.get("tracking_status") if isinstance(ev.raw, dict) else None
        sub = status.get("substatus") if isinstance(status, dict) else None
        code = sub.get("code", "") if isinstance(sub, dict) else ""
        items.append(
            TimelineItem(
                at=ev.occurred_at,
                kind="carrier",
                title=f"Carrier: {ev.status.replace('_', ' ').lower()}",
                detail=str(code).replace("_", " "),
            )
        )
    for e in s.scalars(select(Evidence).where(Evidence.order_id == order.id)):
        if for_buyer and e.kind == EvidenceKind.PACKING_PHOTO.value:
            continue
        detail = ""
        if e.kind == EvidenceKind.SEAL_SCAN.value and not for_buyer:
            detail = _SEAL_DETAIL.get(str(e.ai_result.get("reason", "")), "")
        items.append(
            TimelineItem(
                at=e.created_at, kind="evidence", title=e.kind.replace("_", " "), detail=detail
            )
        )
    if not for_buyer:
        for ma in s.scalars(select(MoneyAction).where(MoneyAction.order_id == order.id)):
            items.append(
                TimelineItem(
                    at=ma.completed_at or ma.created_at,
                    kind="money",
                    title=f"{ma.kind.replace('_', ' ')} {money_status(ma)}",
                    detail=ma.paypal_refund_id or "",
                )
            )
        for r in s.scalars(select(Receipt).where(Receipt.case_id.in_(case_ids))):
            items.append(
                TimelineItem(
                    at=r.created_at,
                    kind="receipt",
                    title=f"Signed receipt #{r.seq} ({r.kind})",
                    detail=r.hash[:16],
                )
            )
    items.sort(key=lambda i: i.at)
    return items


# ---------------------------------------------------------------- buyer tracker


class BuyerView(BaseModel):
    order_ref: str
    item: str
    amount_minor: int
    currency: str
    status: str
    state: str
    capture_status: str | None = None
    method: str | None
    tracking_number: str | None
    carrier: str | None
    choice_offered: list[str]
    choice_made: str | None
    can_confirm: bool
    can_report: bool
    address_check: str
    address_issues: list[dict[str, Any]]
    # The buyer's own address, only while they can still fix it (before capture).
    address: dict[str, Any] | None = None
    seller_name: str = ""
    case_open: bool
    case_message: str | None
    timeline: list[TimelineItem]
    # Where the parcel got to before any case: drives the progress bar, so a "Not here" report
    # never shows the parcel as received.
    progress_state: str = ""
    # RT-01: the rule file's buyer_rights text, once the buyer has reported a problem.
    rights: str = ""


_CASE_STATES = {OrderState.CASE_OPEN.value, OrderState.RESOLVED.value}


def progress_state(s: Session, order: Order) -> str:
    if order.state not in _CASE_STATES:
        return order.state
    opened = s.scalars(
        select(AuditLog)
        .where(AuditLog.entity_type == "order", AuditLog.entity_id == order.id)
        .order_by(AuditLog.at)
    ).all()
    for row in opened:
        if row.after.get("state") == OrderState.CASE_OPEN.value:
            return str(row.before.get("state", ""))
    return ""


def buyer_view(s: Session, order: Order) -> BuyerView:
    shipment = latest_shipment(s, order.id)
    case = latest_case(s, order.id)
    decision = latest_decision(s, case.id) if case else None
    message = None
    if decision is not None:
        explainer = decision.model_outputs.get("explainer") or {}
        message = explainer.get("buyer_message")
    if case is not None:
        message = buyer_outcome(s, order, case) or message
    reportable = {"IN_TRANSIT", "EXCEPTION", "DELIVERED", "PICKED_UP", "CONFIRMED"}
    return BuyerView(
        order_ref=order.id[:8].upper(),
        item=order.item_description,
        amount_minor=order.amount_minor,
        currency=order.currency,
        status=_status(order, BUYER_STATUS, BUYER_PAYMENT),
        capture_status=order.capture_status,
        state=order.state,
        progress_state=progress_state(s, order),
        method=order.shipping_method,
        tracking_number=shipment.tracking_number if shipment else None,
        carrier=shipment.carrier if shipment else None,
        choice_offered=list(shipment.choice_offered)
        if shipment and not shipment.choice_made
        else [],
        choice_made=shipment.choice_made if shipment else None,
        can_confirm=order.state in _IN_FLIGHT | {"DELIVERED", "PICKED_UP"},
        can_report=order.state in reportable,
        address_check=order.address_check,
        address_issues=list(order.address_issues or []),
        address=dict(order.shipping_address)
        if order.state == OrderState.CREATED.value and order.address_check == "invalid"
        else None,
        seller_name=seller.name if (seller := s.get(Seller, order.seller_id)) else "",
        case_open=case is not None and case.state in _OPEN_CASE,
        case_message=message,
        timeline=timeline(s, order, for_buyer=True),
    )


# ---------------------------------------------------------------- Control Room


class OrderRow(BaseModel):
    order_id: str
    order_ref: str
    item: str
    buyer_area: str
    buyer_country: str
    amount_minor: int
    currency: str
    method: str | None
    recommended_method: str | None
    risk: float | None
    state: str
    status: str
    address_check: str
    evidence: EvidenceScore
    case_id: str | None
    case_state: str | None
    failure_point: str | None
    confidence: str | None
    route: str | None
    rule_id: str | None
    money_at_stake_minor: int
    refund_status: str | None
    needs_action: bool
    last_update: datetime
    simulated: bool
    # Latest packing photo check (PK): match | mismatch | check, or None before any photo.
    packing_verdict: str | None = None
    packing_message: str | None = None
    packing_cleared: bool = False
    # RK: live delivery risk for the chosen method and address outcome, before delivery only.
    delivery_risk: float | None = None
    risk_reasons: list[str] = []
    # PP-11: PayPal's Seller Protection verdict on the capture; information only.
    seller_protection: str | None = None


_RISK_STATES = {
    OrderState.CREATED.value,
    OrderState.PAID.value,
    OrderState.PACKED.value,
    OrderState.SHIPPED.value,
    OrderState.IN_TRANSIT.value,
    OrderState.EXCEPTION.value,
}


def _seller_history(s: Session, seller_id: str) -> tuple[float, int]:
    return min(seller_case_rate(s, seller_id), 1.0), seller_order_count(s, seller_id)


def delivery_risk(
    s: Session, order: Order, history: tuple[float, int] | None = None
) -> tuple[float | None, list[str]]:
    """The trained model's view of this parcel as sent (method, address outcome, shop history).
    None once the parcel is delivered, or when the model is unavailable. `history` is the
    seller's (case rate, order count), passed in when a list shares one seller."""
    model = load_model()
    if model is None or order.state not in _RISK_STATES:
        return None, []
    case_rate, seller_orders = (
        history if history is not None else _seller_history(s, order.seller_id)
    )
    method = order.shipping_method if order.shipping_method in ("locker", "signature") else "home"
    score = model.score(
        RiskFeatures(
            order_value_major=order.amount_minor
            / 10 ** MINOR_UNIT_EXPONENT[parse_currency(order.currency)],
            buyer_country=order.buyer_country,
            seller_case_rate=case_rate,
            seller_orders=seller_orders,
            method=method,  # type: ignore[arg-type]
            address_overridden=order.address_check == "overridden",
        )
    )
    return score.p_failure, [r.text for r in score.top_reasons]


@dataclass(frozen=True)
class _RowData:
    """Everything the order rows need, loaded once per list: a fixed number of queries whatever
    the number of orders (the per-order version ran about ten per row)."""

    case: dict[str, Case]  # the latest per order
    decision: dict[str, Decision]  # the latest per case
    money: dict[str, MoneyAction]  # the latest refund per order (evidence writes excluded)
    evidence_sent: set[str]  # case ids with a dispute-evidence write
    proof: _Proof
    last_at: dict[str, datetime]  # the latest audit time per order id and case id
    protection: dict[str, dict[str, Any]]  # PayPal's latest Seller Protection report per order
    history: dict[str, tuple[float, int]]  # per seller with a risk to show: (case rate, orders)


def _load_rows(s: Session, orders: Sequence[Order]) -> _RowData:
    ids = [o.id for o in orders]
    case = _latest_by(
        s.scalars(select(Case).where(Case.order_id.in_(ids)).order_by(Case.opened_at.desc())),
        lambda c: c.order_id,
    )
    case_ids = [c.id for c in case.values()]
    decision = _latest_by(
        s.scalars(
            select(Decision)
            .where(Decision.case_id.in_(case_ids))
            .order_by(Decision.created_at.desc())
        ),
        lambda d: d.case_id,
    )
    # A case's money writes carry its order's id, so one filter on order_id finds them all.
    actions = list(
        s.scalars(
            select(MoneyAction)
            .where(MoneyAction.order_id.in_(ids))
            .order_by(MoneyAction.created_at.desc())
        )
    )
    evidence = MoneyActionKind.EVIDENCE.value
    not_refunds = (evidence, MoneyActionKind.OFFER.value)  # neither one moves money itself
    money = _latest_by((a for a in actions if a.kind not in not_refunds), lambda a: a.order_id)
    sent = {a.case_id for a in actions if a.kind == evidence and a.case_id is not None}
    # The shop's history feeds the risk model only, so it is counted only when a row shows a risk.
    at_risk = {o.seller_id for o in orders if o.state in _RISK_STATES}
    last_at: dict[str, datetime] = {
        entity_id: at
        for entity_id, at in s.execute(
            select(AuditLog.entity_id, func.max(AuditLog.at))
            .where(AuditLog.entity_id.in_(ids + case_ids))
            .group_by(AuditLog.entity_id)
        )
    }
    return _RowData(
        case=case,
        decision=decision,
        money=money,
        evidence_sent=sent,
        proof=_load_proof(s, ids),
        last_at=last_at,
        protection=latest_reports(s, ids),
        history={sid: _seller_history(s, sid) for sid in at_risk} if load_model() else {},
    )


def _last_update(order: Order, case: Case | None, data: _RowData) -> datetime:
    times = [data.last_at.get(order.id), data.last_at.get(case.id) if case else None]
    return max((t for t in times if t is not None), default=order.created_at)


def _row(order: Order, data: _RowData, s: Session) -> OrderRow:
    case = data.case.get(order.id)
    decision = data.decision.get(case.id) if case else None
    ma = data.money.get(order.id)
    packing = next(
        (
            e
            for e in reversed(data.proof.evidence.get(order.id, []))
            if e.kind == EvidenceKind.PACKING_PHOTO
        ),
        None,
    )
    packing_result = packing.ai_result if packing is not None else {}
    open_case = case is not None and case.state in _OPEN_CASE
    stake = order.amount_minor if open_case or order.state in _IN_FLIGHT else 0
    risk_now, risk_reasons = delivery_risk(s, order, data.history.get(order.seller_id))
    # DE-01: an open PayPal dispute waits for the seller's evidence (PayPal sets a deadline).
    awaits_evidence = (
        case is not None
        and case.state == CaseState.DISPUTE_EVIDENCE.value
        and case.id not in data.evidence_sent
    )
    report = data.protection.get(order.id)
    return OrderRow(
        order_id=order.id,
        order_ref=order.id[:8].upper(),
        item=order.item_description,
        buyer_area=postcode_district(str(order.shipping_address.get("postal_code", ""))),
        buyer_country=order.buyer_country,
        amount_minor=order.amount_minor,
        currency=order.currency,
        method=order.shipping_method,
        recommended_method=order.recommended_method,
        risk=order.risk_score,
        state=order.state,
        status=_status(order, SELLER_STATUS, SELLER_PAYMENT),
        address_check=order.address_check,
        evidence=_evidence_score(order, data.proof),
        case_id=case.id if case else None,
        case_state=case.state if case else None,
        failure_point=decision.failure_point if decision else None,
        confidence=decision.confidence if decision else None,
        route=decision.route if decision else None,
        rule_id=decision.rule_id if decision else None,
        money_at_stake_minor=stake,
        refund_status=money_status(ma) if ma else None,
        needs_action=bool(
            case
            and case.state in (CaseState.AWAITING_SELLER.value, CaseState.HOLD_FOR_REVIEW.value)
        )
        or order.state in (OrderState.PAID.value, OrderState.PACKED.value)
        or awaits_evidence,
        last_update=_last_update(order, case, data),
        simulated=is_simulated(order.capture_id),
        packing_verdict=packing_result.get("verdict"),
        packing_message=packing_result.get("message"),
        packing_cleared=bool(packing_result.get("cleared", False)),
        delivery_risk=risk_now,
        risk_reasons=risk_reasons,
        seller_protection=str(report["status"]) if report else None,
    )


def order_rows(s: Session, orders: Sequence[Order]) -> list[OrderRow]:
    """The Control Room rows for a list of orders, in about ten queries for any number of rows."""
    data = _load_rows(s, orders)
    return [_row(order, data, s) for order in orders]


def order_row(s: Session, order: Order) -> OrderRow:
    return order_rows(s, [order])[0]


# ---------------------------------------------------------------- case page


class DecisionView(BaseModel):
    decision_id: str
    failure_point: str | None
    confidence: str | None
    route: str
    fix: str | None
    refund_amount_minor: int | None
    currency: str | None
    rule_id: str | None
    rule_version: str | None
    # The law or policy the rule encodes, in plain words ("Consumer Rights Act 2015 s.29").
    rule_name: str | None = None
    reasons: list[str]
    reason_codes: list[str]
    explanation: str
    buyer_message: str | None
    claim_letter: str | None
    explainer_source: str | None
    missing_signals: list[str]


class MoneyView(BaseModel):
    kind: str
    amount_minor: int
    currency: str
    status: str
    refund_id: str | None


class ClaimSentenceView(BaseModel):
    text: str
    fact_ids: list[str]


class FactView(BaseModel):
    id: str
    kind: str
    text: str


class RecoveryView(BaseModel):
    """RC-01..05: the carrier claim the seller can submit, and its status."""

    recovery_id: str
    case_id: str
    status: str
    carrier: str
    carrier_name: str
    carrier_known: bool  # False: the default entry; the seller checks the carrier's terms
    failure_point: str
    claim_minor: int
    currency: str
    cover_minor: int | None
    deadline_at: datetime
    window_days: int
    terms_source: str
    terms_url: str
    terms_note: str
    terms_rule: str
    sentences: list[ClaimSentenceView]
    facts: list[FactView]
    pack_source: str
    recovered_minor: int | None
    submitted_at: datetime | None
    resolved_at: datetime | None
    actions: list[str]


_RECOVERY_ACTIONS = {
    "PREPARED": ["submitted", "withdraw"],
    "SUBMITTED": ["paid", "refused"],
}


def recovery_view(r: Recovery) -> RecoveryView:
    terms = r.terms or {}
    pack = r.pack or {}
    return RecoveryView(
        recovery_id=r.id,
        case_id=r.case_id,
        status=r.status,
        carrier=r.carrier,
        carrier_name=str(terms.get("carrier_name") or r.carrier),
        carrier_known=not terms.get("is_default", True),
        failure_point=r.failure_point,
        claim_minor=r.claim_minor,
        currency=r.currency,
        cover_minor=terms.get("cover_minor"),
        deadline_at=r.deadline_at,
        window_days=int(terms.get("window_days") or 0),
        terms_source=str(terms.get("source") or ""),
        terms_url=str(terms.get("terms_url") or ""),
        terms_note=str(terms.get("note") or ""),
        terms_rule=f"{terms.get('rule_id', '')} {terms.get('rule_version', '')}".strip(),
        sentences=[ClaimSentenceView(**x) for x in pack.get("sentences", [])],
        facts=[FactView(**x) for x in pack.get("facts", [])],
        pack_source=str(pack.get("source") or "template"),
        recovered_minor=r.recovered_minor,
        submitted_at=r.submitted_at,
        resolved_at=r.resolved_at,
        actions=list(_RECOVERY_ACTIONS.get(r.status, [])),
    )


def case_recovery(s: Session, case_id: str) -> Recovery | None:
    return s.scalars(select(Recovery).where(Recovery.case_id == case_id)).first()


class OfferOption(BaseModel):
    """The refund offer the seller can make inside an open PayPal dispute (PP-09)."""

    amount_minor: int
    currency: str


class CaseView(BaseModel):
    case_id: str
    state: str
    reason: str
    reported_by: str
    opened_at: datetime
    order: OrderRow
    decision: DecisionView | None
    money: list[MoneyView]
    actions: list[str]
    receipts: int
    timeline: list[TimelineItem]
    disclaimer: str = "Information, not legal advice. The seller decides."
    scan_audit: dict[str, Any] | None = None  # SA-01: verdict and checks of the latest decision
    counterfactual: dict[str, Any] | None = None  # CF-01: which proof would have changed it
    recovery: RecoveryView | None = None  # RC-01: the carrier claim, if any
    offer: OfferOption | None = None  # PP-09: a refund the seller can offer inside the dispute
    dispute_resolved: bool = False  # PO-14: PayPal resolved the dispute this case waited on
    can_close: bool = False  # PO-15: a person may close this held case without a refund


def _dispute_resolved(s: Session, order_id: str) -> bool:
    return (
        s.scalar(
            select(Dispute.id).where(Dispute.order_id == order_id, Dispute.status == "resolved")
        )
        is not None
    )


def case_view(s: Session, case: Case, rule_names: dict[str, str] | None = None) -> CaseView:
    order = s.get(Order, case.order_id)
    assert order is not None
    d = latest_decision(s, case.id)
    decision = None
    if d is not None:
        explainer = d.model_outputs.get("explainer") or {}
        engine = d.signals.get("engine") or {}
        decision = DecisionView(
            decision_id=d.id,
            failure_point=d.failure_point,
            confidence=d.confidence,
            route=d.route,
            fix=d.fix,
            refund_amount_minor=d.refund_amount_minor,
            currency=d.currency,
            rule_id=d.rule_id,
            rule_version=d.rule_version,
            rule_name=(rule_names or {}).get(d.rule_id or ""),
            reasons=list(d.reasons),
            reason_codes=list(d.reason_codes),
            explanation=d.explanation,
            buyer_message=explainer.get("buyer_message"),
            claim_letter=explainer.get("claim_letter"),
            explainer_source=explainer.get("source"),
            missing_signals=list(engine.get("missing_signals") or []),
        )
    actions_rows = list(
        s.scalars(
            select(MoneyAction)
            .where(MoneyAction.case_id == case.id)
            .order_by(MoneyAction.created_at)
        )
    )
    money = [
        MoneyView(
            kind=ma.kind,
            amount_minor=ma.amount_minor,
            currency=ma.currency,
            status=money_status(ma),
            refund_id=ma.paypal_refund_id,
        )
        for ma in actions_rows
    ]
    actions: list[str] = []
    # UI-01: an approved refund owns the case until it lands, so nothing is offered twice.
    on_its_way = any(
        m.kind in _REFUND_KINDS
        and m.status in (MoneyActionStatus.QUEUED.value, MoneyActionStatus.PENDING.value)
        for m in actions_rows
    )
    if on_its_way:
        pass
    elif case.state == CaseState.AWAITING_SELLER.value:
        actions = ["approve", "decline"]
    elif case.state == CaseState.HOLD_FOR_REVIEW.value:
        # A blocked or failed refund cannot be approved again; a fresh decision can follow.
        stopped = d is not None and any(
            m.decision_id == d.id
            and m.status in (MoneyActionStatus.BLOCKED.value, MoneyActionStatus.FAILED.value)
            for m in actions_rows
        )
        actions = ["re-check"] if stopped else ["approve", "re-check"]
    elif case.state == CaseState.DISPUTE_EVIDENCE.value and not any(
        m.kind == MoneyActionKind.EVIDENCE.value for m in money
    ):
        actions = ["send-evidence"]  # DE-01: once per decision
    offer = None
    if (
        case.state == CaseState.DISPUTE_EVIDENCE.value
        and d is not None
        and not any(m.kind == MoneyActionKind.OFFER.value for m in actions_rows)
        and (amount := offer_amount(s, case)) is not None
    ):
        offer = OfferOption(amount_minor=amount.amount_minor, currency=amount.currency.value)
    receipts = len(list(s.scalars(select(Receipt.id).where(Receipt.case_id == case.id))))
    return CaseView(
        dispute_resolved=case.state == CaseState.CLOSED.value and _dispute_resolved(s, order.id),
        can_close=case.state == CaseState.HOLD_FOR_REVIEW.value
        and not refund_in_flight(s, case.id),
        case_id=case.id,
        state=case.state,
        reason=case.reason,
        reported_by=case.reported_by,
        opened_at=case.opened_at,
        order=order_row(s, order),
        decision=decision,
        money=money,
        actions=actions,
        receipts=receipts,
        timeline=timeline(s, order, for_buyer=False),
        scan_audit=(d.signals.get("scan_audit") if d is not None else None),
        counterfactual=(d.signals.get("counterfactual") if d is not None else None),
        recovery=recovery_view(rec) if (rec := case_recovery(s, case.id)) else None,
        offer=offer,
    )
