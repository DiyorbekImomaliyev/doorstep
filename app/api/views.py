"""Read models for the screens (SPEC section 5): buyer tracker, Control Room rows, case page.

Views show what a person needs to act, nothing more. Addresses are masked to the postcode
district in seller views (SPEC section 13). Status words never overclaim (PR-08): a simulated
refund is labelled simulated, a receipt chain that was not checked says not_run.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.ai.risk import RiskFeatures, load_model
from app.cases.signals import EvidenceKind
from app.checkout.risk import seller_case_rate, seller_order_count
from app.geo import postcode_district  # re-exported: shown in the Control Room
from app.money import MINOR_UNIT_EXPONENT, parse_currency
from app.orders.enums import CaseState, OrderState
from app.orders.models import (
    AuditLog,
    Case,
    Decision,
    Evidence,
    MoneyAction,
    Order,
    Recovery,
    Seller,
    Shipment,
    TrackingEvent,
)
from app.proof.models import Receipt
from app.proof.seal import counted_scan
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
    """PR-08: a simulated refund never reads as a PayPal refund."""
    if ma.paypal_status == SIMULATED_STATUS:
        return "simulated"
    return ma.status


class EvidenceScore(BaseModel):
    score: float
    present: list[str]
    missing: list[str]


def evidence_completeness(s: Session, order: Order) -> EvidenceScore:
    kinds = set(s.scalars(select(Evidence.kind).where(Evidence.order_id == order.id)))
    shipment = latest_shipment(s, order.id)
    delivered = False
    if shipment is not None:
        delivered = (
            s.scalar(
                select(TrackingEvent.id)
                .where(TrackingEvent.shipment_id == shipment.id)
                .where(TrackingEvent.status == "DELIVERED")
                .limit(1)
            )
            is not None
        )
    checks = {
        "address checked": order.address_check in ("valid", "overridden"),
        "packing photo": EvidenceKind.PACKING_PHOTO.value in kinds,
        "tracking": shipment is not None,
        "delivered scan": delivered or order.state == OrderState.PICKED_UP.value,
        "possession proof": bool(kinds & _POSSESSION)
        or order.state == OrderState.PICKED_UP.value
        or counted_scan(s, order.id) is not None,
    }
    present = [k for k, ok in checks.items() if ok]
    missing = [k for k, ok in checks.items() if not ok]
    return EvidenceScore(
        score=round(len(present) / len(checks), 2), present=present, missing=missing
    )


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


def _state_title(entity: str, after: dict[str, Any], action: str) -> str:
    state = after.get("state")
    if entity == "order" and isinstance(state, str):
        return BUYER_STATUS.get(state, state)
    if entity == "case" and isinstance(state, str):
        return "Case " + state.replace("_", " ").lower()
    return action.replace(".", " ").replace("_", " ")


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
        sub = ev.raw.get("tracking_status", {}) if isinstance(ev.raw, dict) else {}
        code = sub.get("substatus", {}).get("code", "") if isinstance(sub, dict) else ""
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
        items.append(TimelineItem(at=e.created_at, kind="evidence", title=e.kind.replace("_", " ")))
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
    reportable = {"IN_TRANSIT", "EXCEPTION", "DELIVERED", "PICKED_UP", "CONFIRMED"}
    return BuyerView(
        order_ref=order.id[:8].upper(),
        item=order.item_description,
        amount_minor=order.amount_minor,
        currency=order.currency,
        status=BUYER_STATUS.get(order.state, order.state),
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


_RISK_STATES = {
    OrderState.CREATED.value,
    OrderState.PAID.value,
    OrderState.PACKED.value,
    OrderState.SHIPPED.value,
    OrderState.IN_TRANSIT.value,
    OrderState.EXCEPTION.value,
}


def delivery_risk(s: Session, order: Order) -> tuple[float | None, list[str]]:
    """The trained model's view of this parcel as sent (method, address outcome, shop history).
    None once the parcel is delivered, or when the model is unavailable."""
    model = load_model()
    if model is None or order.state not in _RISK_STATES:
        return None, []
    method = order.shipping_method if order.shipping_method in ("locker", "signature") else "home"
    score = model.score(
        RiskFeatures(
            order_value_major=order.amount_minor
            / 10 ** MINOR_UNIT_EXPONENT[parse_currency(order.currency)],
            buyer_country=order.buyer_country,
            seller_case_rate=min(seller_case_rate(s, order.seller_id), 1.0),
            seller_orders=seller_order_count(s, order.seller_id),
            method=method,  # type: ignore[arg-type]
            address_overridden=order.address_check == "overridden",
        )
    )
    return score.p_failure, [r.text for r in score.top_reasons]


def order_row(s: Session, order: Order) -> OrderRow:
    case = latest_case(s, order.id)
    decision = latest_decision(s, case.id) if case else None
    ma = s.scalars(
        select(MoneyAction)
        .where(MoneyAction.order_id == order.id)
        .order_by(MoneyAction.created_at.desc())
    ).first()
    packing = s.scalars(
        select(Evidence)
        .where(Evidence.order_id == order.id, Evidence.kind == EvidenceKind.PACKING_PHOTO)
        .order_by(Evidence.created_at.desc())
    ).first()
    packing_result = packing.ai_result if packing is not None else {}
    open_case = case is not None and case.state in _OPEN_CASE
    stake = order.amount_minor if open_case or order.state in _IN_FLIGHT else 0
    last = s.scalar(
        select(AuditLog.at)
        .where(AuditLog.entity_id.in_([order.id, case.id if case else ""]))
        .order_by(AuditLog.at.desc())
        .limit(1)
    )
    risk_now, risk_reasons = delivery_risk(s, order)
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
        status=SELLER_STATUS.get(order.state, order.state),
        address_check=order.address_check,
        evidence=evidence_completeness(s, order),
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
        or order.state in (OrderState.PAID.value, OrderState.PACKED.value),
        last_update=last or order.created_at,
        simulated=is_simulated(order.capture_id),
        packing_verdict=packing_result.get("verdict"),
        packing_message=packing_result.get("message"),
        packing_cleared=bool(packing_result.get("cleared", False)),
        delivery_risk=risk_now,
        risk_reasons=risk_reasons,
    )


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
    recovery: RecoveryView | None = None  # RC-01: the carrier claim, if any


def case_view(s: Session, case: Case) -> CaseView:
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
            reasons=list(d.reasons),
            reason_codes=list(d.reason_codes),
            explanation=d.explanation,
            buyer_message=explainer.get("buyer_message"),
            claim_letter=explainer.get("claim_letter"),
            explainer_source=explainer.get("source"),
            missing_signals=list(engine.get("missing_signals") or []),
        )
    money = [
        MoneyView(
            kind=ma.kind,
            amount_minor=ma.amount_minor,
            currency=ma.currency,
            status=money_status(ma),
            refund_id=ma.paypal_refund_id,
        )
        for ma in s.scalars(
            select(MoneyAction)
            .where(MoneyAction.case_id == case.id)
            .order_by(MoneyAction.created_at)
        )
    ]
    actions: list[str] = []
    if case.state == CaseState.AWAITING_SELLER.value:
        actions = ["approve", "decline"]
    elif case.state == CaseState.HOLD_FOR_REVIEW.value:
        actions = ["approve"]
    receipts = len(list(s.scalars(select(Receipt.id).where(Receipt.case_id == case.id))))
    return CaseView(
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
        recovery=recovery_view(rec) if (rec := case_recovery(s, case.id)) else None,
    )
