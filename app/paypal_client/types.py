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
    # PP-11: PayPal's Seller Protection verdict on this capture, when PayPal reports one.
    seller_protection: str | None = None  # ELIGIBLE | PARTIALLY_ELIGIBLE | NOT_ELIGIBLE
    protected_categories: list[str] = []


class RefundInfo(BaseModel):
    """One refund on a capture, as PayPal reports it."""

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


class DisputeSummary(BaseModel):
    """An unresolved PayPal dispute and the captures it names."""

    dispute_id: str  # "" when PayPal lists a dispute without an id: never matched to an order
    status: str  # PayPal's status, upper case; anything but RESOLVED counts as open
    capture_ids: list[str] = []  # disputed_transactions[].seller_transaction_id


class DisputeDetail(BaseModel):
    """One dispute as GET /v1/customer/disputes/{id} returns it: what make-offer needs (PP-09).

    Field paths are from PayPal's Disputes API reference: `status`, `dispute_life_cycle_stage`,
    `dispute_amount`, `disputed_transactions[].seller_transaction_id`,
    `allowed_response_options.make_offer.offer_types`, `offer.seller_offered_amount` and
    `offer.history[]`."""

    dispute_id: str
    status: str  # upper case; RESOLVED means closed
    stage: str  # INQUIRY | CHARGEBACK | PRE_ARBITRATION | ARBITRATION
    capture_ids: list[str] = []
    amount: Money | None = None  # what the buyer disputes; None when PayPal leaves it out
    offer_types: list[str] = []  # offer types PayPal allows now; empty = no offer allowed
    seller_offer_made: bool = False  # the seller already has an offer on this dispute
