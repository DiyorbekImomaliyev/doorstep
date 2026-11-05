"""Failure-point engine (SPEC section 7). Deterministic code; never calls a model (FE-10).
Weights and the tie-break priority come from tuning/engine_config.py at call time."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from app.orders.enums import ProofKind
from tuning import engine_config


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


# Proofs that the buyer (or someone they chose) physically received the parcel.
POSSESSION_PROOFS = frozenset(
    {
        ProofKind.SIGNATURE,
        ProofKind.LOCKER_PICKUP_CODE,
        ProofKind.PHOTO_AT_BUYER_ADDRESS,
        ProofKind.BUYER_SEAL_SCAN,
    }
)

_SIGNAL_TEXT = {
    "address_check_failed": "address check failed",
    "address_overridden_by_buyer": "buyer kept an address that failed the check",
    "carrier_address_issue": "carrier reported an address problem",
    "address_valid": "address check passed",
    "no_delivered_scan_and_stale": "no delivered scan and tracking went stale",
    "delivered_scan_without_possession_proof": "delivered scan but no proof of possession",
    "delivered_scan": "carrier scanned the parcel as delivered",
    "possession_proof": "proof of possession (signature, locker code, doorstep photo or seal scan)",
    "buyer_photo_shows_damage": "buyer photo shows damage",
    "buyer_photo_shows_no_damage": "buyer photo shows no damage",
    "packing_match": "packing photo matched the order",
    "packing_mismatch_not_cleared": "packing photo mismatch was not cleared before shipping",
    "packing_mismatch_cleared": "packing mismatch was checked and cleared before shipping",
    "label_weight_matches": "label weight matches the packed item",
}


def _facts(s: Signals) -> dict[str, bool]:
    """Every boolean fact the rules use. Unknown inputs are never facts (FE-07)."""
    possession = bool(s.proofs & POSSESSION_PROOFS)
    return {
        "address_check_failed": s.address_check == AddressCheck.INVALID,
        "address_overridden_by_buyer": s.address_check == AddressCheck.OVERRIDDEN,
        "carrier_address_issue": s.carrier_address_issue,
        "address_valid": s.address_check == AddressCheck.VALID,
        "no_delivered_scan_and_stale": not s.delivered_scan and s.tracking_stale,
        "delivered_scan_without_possession_proof": s.delivered_scan and not possession,
        "delivered_scan": s.delivered_scan,
        "possession_proof": possession,
        "buyer_photo_shows_damage": s.buyer_photo_damage is True,
        "buyer_photo_shows_no_damage": s.buyer_photo_damage is False,
        "packing_match": s.packing_check == PackingCheck.MATCH,
        "packing_mismatch_not_cleared": (
            s.packing_check == PackingCheck.MISMATCH and not s.packing_mismatch_cleared
        ),
        "packing_mismatch_cleared": (
            s.packing_check == PackingCheck.MISMATCH and s.packing_mismatch_cleared
        ),
        "label_weight_matches": s.label_weight_matches is True,
    }


def _matches(s: Signals, f: dict[str, bool]) -> set[FailurePoint]:
    """The failure-point rules of SPEC section 7 (FE-01 .. FE-06)."""
    not_received = s.claim_reason == ClaimReason.NOT_RECEIVED
    found: set[FailurePoint] = set()
    if not_received and (
        f["address_check_failed"] or f["address_overridden_by_buyer"] or f["carrier_address_issue"]
    ):
        found.add(FailurePoint.BAD_ADDRESS)
    if not_received and (
        f["no_delivered_scan_and_stale"] or f["delivered_scan_without_possession_proof"]
    ):
        found.add(FailurePoint.CARRIER_LOST)
    if not_received and f["possession_proof"]:
        found.add(FailurePoint.STOLEN_AFTER_DELIVERY)
    if s.claim_reason == ClaimReason.DAMAGED and f["buyer_photo_shows_damage"]:
        found.add(FailurePoint.DAMAGED_IN_TRANSIT)
    if f["packing_mismatch_not_cleared"]:
        found.add(FailurePoint.WRONG_ITEM)
    contents_claim = s.claim_reason in (ClaimReason.EMPTY_BOX, ClaimReason.MISSING_ITEM)
    if (
        (contents_claim and f["label_weight_matches"] and f["packing_match"])
        or (
            s.claim_reason == ClaimReason.WRONG_ITEM
            and (f["packing_match"] or f["packing_mismatch_cleared"])
        )
        or (s.claim_reason == ClaimReason.DAMAGED and f["buyer_photo_shows_no_damage"])
    ):
        found.add(FailurePoint.CLAIM_CONTRADICTS_EVIDENCE)
    return found


def _missing(s: Signals) -> list[str]:
    missing = []
    if s.address_check == AddressCheck.UNKNOWN:
        missing.append("address_check")
    if s.packing_check in (PackingCheck.MISSING, PackingCheck.CHECK):
        missing.append("packing_check")
    if s.buyer_photo_damage is None:
        missing.append("buyer_photo_damage")
    if s.label_weight_matches is None:
        missing.append("label_weight_matches")
    return missing


def _confidence(score: float) -> Confidence:
    if score >= engine_config.CONFIDENCE_HIGH_MIN:
        return Confidence.HIGH
    if score >= engine_config.CONFIDENCE_MEDIUM_MIN:
        return Confidence.MEDIUM
    return Confidence.LOW


def assess(signals: Signals) -> EngineResult:
    """Name the most likely failure point. Pure function of the signals and the tuning config."""
    facts = _facts(signals)
    missing_all = _missing(signals)
    matched = _matches(signals, facts)
    priority = [FailurePoint(p) for p in engine_config.FAILURE_POINT_PRIORITY]
    rank = {fp: priority.index(fp) if fp in priority else len(priority) for fp in FailurePoint}
    reasons: list[str] = []

    if matched:
        candidates = sorted(matched, key=lambda fp: (rank[fp], fp.value))
        chosen = candidates[0]
        weights = engine_config.SIGNAL_WEIGHTS.get(chosen.value, {})
        supporting = [name for name in weights if facts.get(name)]
        relevant = engine_config.RELEVANT_SIGNALS.get(chosen.value, ())
        missing = [m for m in missing_all if m in relevant]
        score = sum(weights[name] for name in supporting)
        score -= engine_config.MISSING_SIGNAL_PENALTY * len(missing)
        if len(candidates) > 1:
            score -= engine_config.AMBIGUITY_PENALTY
            others = ", ".join(fp.value for fp in candidates[1:])
            reasons.append(f"also possible: {others} (tie broken by configured priority)")
        reasons[:0] = [_SIGNAL_TEXT[name] for name in supporting]
        confidence = _confidence(score)
    else:
        chosen = FailurePoint(engine_config.FALLBACK_BY_CLAIM[signals.claim_reason.value])
        candidates = [chosen]
        missing = missing_all
        confidence = Confidence.LOW
        reasons.append(f"no failure-point rule matched; default for a {signals.claim_reason} claim")

    if missing:
        reasons.append("missing signals: " + ", ".join(missing))
    if not reasons:
        reasons.append(f"rule for {chosen.value} matched")
    return EngineResult(
        failure_point=chosen,
        confidence=confidence,
        reasons=reasons,
        candidates=candidates,
        missing_signals=missing,
        contradiction=FailurePoint.CLAIM_CONTRADICTS_EVIDENCE in candidates,
    )
