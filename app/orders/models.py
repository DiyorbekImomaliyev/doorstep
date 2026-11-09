"""Domain tables: orders, shipments, cases, decisions, approvals, money actions, disputes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime, new_id
from app.orders.enums import BuyerType, CaseState, OrderState


def _now() -> datetime:
    return datetime.now(UTC)


class Seller(Base):
    __tablename__ = "sellers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scratch_tag: Mapped[str | None] = mapped_column(String(36), index=True)  # PR-06 judge data
    name: Mapped[str] = mapped_column(String(200), default="")
    region: Mapped[str] = mapped_column(String(2))
    # Per-currency overrides in minor units, e.g. {"GBP": 2000}.
    # Empty -> defaults from rules/money_limits_v1.yaml.
    auto_approve_limits: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    daily_refund_caps: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    policy_text: Mapped[str] = mapped_column(Text, default="")


class SellerRule(Base):
    """AP-02..05: a seller's autopilot rule, suggested from their own approvals and turned on by
    them. One row per (seller, failure point, currency); status on | off | dismissed."""

    __tablename__ = "seller_rules"
    __table_args__ = (UniqueConstraint("seller_id", "failure_point", "currency"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    seller_id: Mapped[str] = mapped_column(ForeignKey("sellers.id"), index=True)
    failure_point: Mapped[str] = mapped_column(String(32))
    currency: Mapped[str] = mapped_column(String(3))
    max_amount_minor: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12))
    precedents: Mapped[list[str]] = mapped_column(JSON, default=list)  # approved case ids
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    seller_id: Mapped[str] = mapped_column(ForeignKey("sellers.id"), index=True)
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
    # valid | invalid | overridden | unknown (app.cases.engine.AddressCheck), set at checkout
    address_check: Mapped[str] = mapped_column(String(16), default="unknown")
    address_issues: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    shipping_method: Mapped[str | None] = mapped_column(String(16))
    recommended_method: Mapped[str | None] = mapped_column(String(16))
    risk_score: Mapped[float | None] = mapped_column()
    item_description: Mapped[str] = mapped_column(String(200), default="")
    scratch_tag: Mapped[str | None] = mapped_column(String(36), index=True)  # PR-06 judge data
    state: Mapped[str] = mapped_column(String(32), default=OrderState.CREATED.value)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)


class Shipment(Base):
    __tablename__ = "shipments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    carrier: Mapped[str] = mapped_column(String(64))
    tracking_number: Mapped[str] = mapped_column(String(128), index=True)
    method: Mapped[str] = mapped_column(String(16))
    label_weight_g: Mapped[int | None] = mapped_column(Integer)
    delivery_address: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # TR-03: the choice offered to the buyer after a failed attempt (simulated rebook / locker).
    choice_offered: Mapped[list[str]] = mapped_column(JSON, default=list)
    choice_made: Mapped[str | None] = mapped_column(String(16))
    stale_flagged_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # TR-04
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)


class TrackingEvent(Base):
    __tablename__ = "tracking_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    shipment_id: Mapped[str] = mapped_column(ForeignKey("shipments.id"), index=True)
    status: Mapped[str] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    uri: Mapped[str] = mapped_column(String(500), default="")
    ai_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)


class Case(Base):
    __tablename__ = "cases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    seller_id: Mapped[str] = mapped_column(ForeignKey("sellers.id"), index=True)
    payer_id: Mapped[str | None] = mapped_column(String(64), index=True)
    reported_by: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), default=CaseState.OPEN.value)
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
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
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("decisions.id"), index=True)
    approver_kind: Mapped[str] = mapped_column(String(16))
    approver_id: Mapped[str] = mapped_column(String(64))
    rule_id: Mapped[str | None] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)


class MoneyAction(Base):
    __tablename__ = "money_actions"
    # PO-07: one refund per decision, even under concurrent approvals.
    __table_args__ = (
        UniqueConstraint("decision_id", "kind", name="uq_money_action_decision_kind"),
        # PO-13: one row per PayPal refund, so a webhook and the refund job can never both record
        # it. An index, not a constraint, so ensure_indexes adds it to an existing database; rows
        # without a refund id (NULL) are not compared.
        Index("uq_money_action_paypal_refund_id", "paypal_refund_id", unique=True),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("cases.id"), index=True)
    decision_id: Mapped[str | None] = mapped_column(ForeignKey("decisions.id"), index=True)
    seller_id: Mapped[str] = mapped_column(ForeignKey("sellers.id"), index=True)
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
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    paypal_dispute_id: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16))  # open | resolved
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)


class Recovery(Base):
    """A claim the seller can make against the carrier (RC-01). Never moves PayPal money."""

    __tablename__ = "recoveries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), unique=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    seller_id: Mapped[str] = mapped_column(ForeignKey("sellers.id"), index=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("decisions.id"), index=True)
    carrier: Mapped[str] = mapped_column(String(64))  # as on the shipment
    carrier_id: Mapped[str] = mapped_column(String(64))  # entry in carrier_claims_v1.yaml
    failure_point: Mapped[str] = mapped_column(String(32))
    claim_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(16))
    deadline_at: Mapped[datetime] = mapped_column(UTCDateTime())
    terms: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # ClaimTerms
    pack: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # ClaimPack
    recovered_minor: Mapped[int | None] = mapped_column(Integer)
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)


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
