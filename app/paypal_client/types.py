from __future__ import annotations

from pydantic import BaseModel

from app.money import Money
from app.orders.enums import ShippingMethod


class ShippingOption(BaseModel):
    method: ShippingMethod
    label: str
    amount: Money


class CreatedOrder(BaseModel):
    order_id: str
    approve_url: str


class CaptureResult(BaseModel):
    status: str  # COMPLETED | PENDING | DECLINED | ...
    capture_id: str | None = None
    amount: Money | None = None


class RefundInfo(BaseModel):
    """One refund on a capture, as PayPal reports it (ADR-022)."""

    refund_id: str
    status: str
    amount: Money
    custom_id: str | None = None  # our money_action id when Doorstep sent the refund


class CaptureState(BaseModel):
    capture_id: str
    status: str
    amount: Money
    refunded_total: Money
    refunds: list[RefundInfo] = []


class RefundResult(BaseModel):
    refund_id: str
    status: str
    raw: dict[str, object] = {}


class DisputeEvidence(BaseModel):
    evidence_type: str  # e.g. PROOF_OF_FULFILLMENT
    notes: str
    carrier: str | None = None
    tracking_number: str | None = None
