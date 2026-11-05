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
    BUYER_SEAL_SCAN = "buyer_seal_scan"  # the buyer scanned the seal inside the parcel (ADR-035)


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


class MoneyActionStatus(StrEnum):
    QUEUED = "queued"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
