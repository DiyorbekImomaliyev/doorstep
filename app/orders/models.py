"""Domain tables (SPEC section 8). Approvals and disputes are added by ADR-001/ADR-011."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime, new_id
from app.orders.enums import BuyerType, CaseState, OrderState


def _now() -> datetime:
    return datetime.now(UTC)


class Seller(Base):
    __tablename__ = "sellers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), default="")
    region: Mapped[str] = mapped_column(String(2))
    # Per-currency overrides in minor units, e.g. {"GBP": 2000}.
    # Empty -> defaults from rules/money_limits_v1.yaml.
    auto_approve_limits: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    daily_refund_caps: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    policy_text: Mapped[str] = mapped_column(Text, default="")


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    seller_id: Mapped[str] = mapped_column(ForeignKey("sellers.id"))
    paypal_order_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    capture_id: Mapped[str | None] = mapped_column(String(64))
    capture_status: Mapped[str | None] = mapped_column(String(32))
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    buyer_country: Mapped[str] = mapped_column(String(2))
    buyer_type: Mapped[str] = mapped_column(String(16), default=BuyerType.CONSUMER.value)
    payer_id: Mapped[str | None] = mapped_column(String(64))
    shipping_address: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    address_override: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[str] = mapped_column(String(32), default=OrderState.CREATED.value)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)


class Shipment(Base):
    __tablename__ = "shipments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"))
    carrier: Mapped[str] = mapped_column(String(64))
    tracking_number: Mapped[str] = mapped_column(String(128), index=True)
    method: Mapped[str] = mapped_column(String(16))
    label_weight_g: Mapped[int | None] = mapped_column(Integer)
    delivery_address: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class TrackingEvent(Base):
    __tablename__ = "tracking_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    shipment_id: Mapped[str] = mapped_column(ForeignKey("shipments.id"))
    status: Mapped[str] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"))
    kind: Mapped[str] = mapped_column(String(32))
    uri: Mapped[str] = mapped_column(String(500), default="")
    ai_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)


class Case(Base):
    __tablename__ = "cases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"))
    seller_id: Mapped[str] = mapped_column(ForeignKey("sellers.id"))
    payer_id: Mapped[str | None] = mapped_column(String(64), index=True)
    reported_by: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), default=CaseState.OPEN.value)
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"))
    failure_point: Mapped[str | None] = mapped_column(String(48))
    confidence: Mapped[str | None] = mapped_column(String(8))
    route: Mapped[str] = mapped_column(String(32))
    fix: Mapped[str | None] = mapped_column(String(16))
    refund_amount_minor: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String(3))
    rule_id: Mapped[str | None] = mapped_column(String(64))
    rule_version: Mapped[str | None] = mapped_column(String(32))
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    reason_codes: Mapped[list[str]] = mapped_column(JSON, default=list)  # app.policy.reasons
    explanation: Mapped[str] = mapped_column(Text, default="")
    signals: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    model_outputs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence_fingerprint: Mapped[str | None] = mapped_column(String(64))
    capture_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"))
    decision_id: Mapped[str] = mapped_column(ForeignKey("decisions.id"))
    approver_kind: Mapped[str] = mapped_column(String(16))
    approver_id: Mapped[str] = mapped_column(String(64))
    rule_id: Mapped[str | None] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)


class MoneyAction(Base):
    __tablename__ = "money_actions"
    # PO-07: one refund per decision, even under concurrent approvals.
    __table_args__ = (
        UniqueConstraint("decision_id", "kind", name="uq_money_action_decision_kind"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"))
    case_id: Mapped[str | None] = mapped_column(ForeignKey("cases.id"))
    decision_id: Mapped[str | None] = mapped_column(ForeignKey("decisions.id"))
    seller_id: Mapped[str] = mapped_column(ForeignKey("sellers.id"))
    kind: Mapped[str] = mapped_column(String(16))
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    request_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(16))
    external: Mapped[bool] = mapped_column(Boolean, default=False)
    paypal_status: Mapped[str | None] = mapped_column(String(32))
    paypal_refund_id: Mapped[str | None] = mapped_column(String(64))
    paypal_response: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Dispute(Base):
    __tablename__ = "disputes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"))
    paypal_dispute_id: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16))  # open | resolved
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    before: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    after: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)
