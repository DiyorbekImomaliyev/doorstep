"""Failure-point engine (SPEC section 7). Deterministic code; never calls a model (FE-10).
Weights and the tie-break priority come from tuning/engine_config.py at call time."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from app.orders.enums import ProofKind


class FailurePoint(StrEnum):
    BAD_ADDRESS = "BAD_ADDRESS"
    CARRIER_LOST = "CARRIER_LOST"
    DAMAGED_IN_TRANSIT = "DAMAGED_IN_TRANSIT"
    STOLEN_AFTER_DELIVERY = "STOLEN_AFTER_DELIVERY"
    WRONG_ITEM = "WRONG_ITEM"
    CLAIM_CONTRADICTS_EVIDENCE = "CLAIM_CONTRADICTS_EVIDENCE"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AddressCheck(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    OVERRIDDEN = "overridden"
    UNKNOWN = "unknown"


class PackingCheck(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    CHECK = "check"
    MISSING = "missing"


class ClaimReason(StrEnum):
    NOT_RECEIVED = "not_received"
    DAMAGED = "damaged"
    WRONG_ITEM = "wrong_item"
    EMPTY_BOX = "empty_box"
    MISSING_ITEM = "missing_item"


class Signals(BaseModel):
    claim_reason: ClaimReason
    address_check: AddressCheck = AddressCheck.UNKNOWN
    carrier_address_issue: bool = False
    delivered_scan: bool = False
    tracking_stale: bool = False
    proofs: frozenset[ProofKind] = frozenset()
    packing_check: PackingCheck = PackingCheck.MISSING
    packing_mismatch_cleared: bool = False
    buyer_photo_damage: bool | None = None
    label_weight_matches: bool | None = None


class EngineResult(BaseModel):
    failure_point: FailurePoint
    confidence: Confidence
    reasons: list[str]
    candidates: list[FailurePoint]
    missing_signals: list[str]
    contradiction: bool


def assess(signals: Signals) -> EngineResult:
    raise NotImplementedError
