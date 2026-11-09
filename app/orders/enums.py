from __future__ import annotations

from enum import StrEnum


class OrderState(StrEnum):
    CREATED = "CREATED"
    PAID = "PAID"
    PACKED = "PACKED"
    SHIPPED = "SHIPPED"
    IN_TRANSIT = "IN_TRANSIT"
    DELIVERED = "DELIVERED"
    PICKED_UP = "PICKED_UP"
    CONFIRMED = "CONFIRMED"
    EXCEPTION = "EXCEPTION"
    CASE_OPEN = "CASE_OPEN"
    RESOLVED = "RESOLVED"


class CaseState(StrEnum):
    OPEN = "OPEN"
    GATHERING_EVIDENCE = "GATHERING_EVIDENCE"
    DECIDED = "DECIDED"
    AUTO_REFUND = "AUTO_REFUND"
    AWAITING_SELLER = "AWAITING_SELLER"
    HOLD_FOR_REVIEW = "HOLD_FOR_REVIEW"
    DISPUTE_EVIDENCE = "DISPUTE_EVIDENCE"
    SETTLED = "SETTLED"
    CLOSED = "CLOSED"


class BuyerType(StrEnum):
    CONSUMER = "consumer"
    BUSINESS = "business"


class ShippingMethod(StrEnum):
    HOME = "home"
    LOCKER = "locker"
    SIGNATURE = "signature"


class ProofKind(StrEnum):
    SIGNATURE = "signature"
    LOCKER_PICKUP_CODE = "locker_pickup_code"
    PHOTO_AT_BUYER_ADDRESS = "photo_at_buyer_address"
    DELIVERED_SCAN = "delivered_scan"
    DELIVERY_PHOTO = "delivery_photo"
    BUYER_SEAL_SCAN = "buyer_seal_scan"  # the buyer scanned the seal inside the parcel


class Fix(StrEnum):
    REFUND = "refund"
    PARTIAL_REFUND = "partial_refund"
    RESHIP = "reship"


class ApproverKind(StrEnum):
    SYSTEM = "system"
    SELLER = "seller"
    REVIEWER = "reviewer"


class MoneyActionKind(StrEnum):
    REFUND = "refund"
    PARTIAL_REFUND = "partial_refund"
    EVIDENCE = "evidence"
    OFFER = "offer"  # PP-09: a refund offer inside an open PayPal dispute; moves no money itself


class RecoveryStatus(StrEnum):
    """Carrier recovery (RC-04): Doorstep prepares the claim, the seller submits it."""

    PREPARED = "PREPARED"
    SUBMITTED = "SUBMITTED"
    PAID = "PAID"
    DENIED = "DENIED"
    WITHDRAWN = "WITHDRAWN"
    EXPIRED = "EXPIRED"


class MoneyActionStatus(StrEnum):
    QUEUED = "queued"
    PENDING = "pending"  # PO-13: PayPal accepted the refund; the money has not landed yet
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
