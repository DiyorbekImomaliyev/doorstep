"""Test-data builders. They insert rows directly (setup only); app code must use state.py."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.orders.enums import ApproverKind, CaseState, Fix, OrderState
from app.orders.models import (
    Approval,
    AuditLog,
    Case,
    Decision,
    MoneyAction,
    Order,
    Seller,
    Shipment,
)

GB_ADDRESS: dict[str, Any] = {
    "name": "Sam",
    "line1": "Flat 2, 10 High Street",
    "line2": "",
    "city": "London",
    "postal_code": "N1 9GU",
    "country": "GB",
}


def make_seller(
    db: Session,
    *,
    region: str = "GB",
    auto_approve_limits: dict[str, int] | None = None,
    daily_refund_caps: dict[str, int] | None = None,
) -> Seller:
    seller = Seller(
        name="Maria",
        region=region,
        auto_approve_limits=auto_approve_limits or {},
        daily_refund_caps=daily_refund_caps or {},
    )
    db.add(seller)
    db.flush()
    return seller


_order_seq = 0


def make_order(
    db: Session,
    seller: Seller,
    *,
    state: OrderState = OrderState.DELIVERED,
    amount_minor: int = 6000,
    currency: str = "GBP",
    buyer_country: str = "GB",
    payer_id: str = "PAYER-1",
    capture_id: str | None = "auto",
    capture_status: str | None = "COMPLETED",
    paypal_order_id: str | None = "auto",
) -> Order:
    global _order_seq
    _order_seq += 1
    order = Order(
        seller_id=seller.id,
        paypal_order_id=f"PPO-{_order_seq}" if paypal_order_id == "auto" else paypal_order_id,
        capture_id=f"CAP-{_order_seq}" if capture_id == "auto" else capture_id,
        capture_status=capture_status,
        amount_minor=amount_minor,
        currency=currency,
        buyer_country=buyer_country,
        payer_id=payer_id,
        shipping_address=dict(GB_ADDRESS),
        state=state.value,
    )
    db.add(order)
    db.flush()
    return order


def make_case(
    db: Session,
    order: Order,
    *,
    state: CaseState = CaseState.GATHERING_EVIDENCE,
    opened_at: datetime | None = None,
    reason: str = "not_received",
) -> Case:
    """Also puts the order in CASE_OPEN unless the case is finished (then RESOLVED)."""
    case = Case(
        order_id=order.id,
        seller_id=order.seller_id,
        payer_id=order.payer_id,
        reported_by="buyer",
        reason=reason,
        state=state.value,
    )
    if opened_at is not None:
        case.opened_at = opened_at
    finished = state in (CaseState.SETTLED, CaseState.CLOSED)
    order.state = (OrderState.RESOLVED if finished else OrderState.CASE_OPEN).value
    db.add(case)
    db.flush()
    return case


def make_shipment(
    db: Session, order: Order, *, tracking_number: str = "SHIPPO_TRANSIT", carrier: str = "shippo"
) -> Shipment:
    shipment = Shipment(
        order_id=order.id,
        carrier=carrier,
        tracking_number=tracking_number,
        method="home",
        label_weight_g=900,
        delivery_address=dict(GB_ADDRESS),
    )
    db.add(shipment)
    db.flush()
    return shipment


def add_decision(
    db: Session, case: Case, *, fix: Fix = Fix.RESHIP, route: str = "AWAITING_SELLER"
) -> Decision:
    decision = Decision(
        case_id=case.id, route=route, fix=fix.value, rule_id="uk_possession_v1", rule_version="test"
    )
    db.add(decision)
    db.flush()
    return decision


def add_approval(
    db: Session, case: Case, decision: Decision, kind: ApproverKind = ApproverKind.SELLER
) -> Approval:
    approval = Approval(
        case_id=case.id, decision_id=decision.id, approver_kind=kind.value, approver_id="seller-1"
    )
    db.add(approval)
    db.flush()
    return approval


def add_money_action(
    db: Session,
    case: Case,
    decision: Decision,
    *,
    status: str,
    amount_minor: int = 1000,
    created_at: datetime | None = None,
    currency: str | None = None,
) -> MoneyAction:
    order = db.get(Order, case.order_id)
    assert order is not None
    ma = MoneyAction(
        order_id=order.id,
        case_id=case.id,
        decision_id=decision.id,
        seller_id=order.seller_id,
        kind="refund",
        amount_minor=amount_minor,
        currency=currency or order.currency,
        status=status,
    )
    if created_at is not None:
        ma.created_at = created_at
    db.add(ma)
    db.flush()
    return ma


def audit_rows(db: Session, entity_id: str) -> list[AuditLog]:
    return list(db.scalars(select(AuditLog).where(AuditLog.entity_id == entity_id)))


def money_actions(db: Session, **filters: Any) -> list[MoneyAction]:
    stmt = select(MoneyAction)
    for key, value in filters.items():
        stmt = stmt.where(getattr(MoneyAction, key) == value)
    return list(db.scalars(stmt))
