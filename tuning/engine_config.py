"""The ONLY file the autoresearch loop may edit (SPEC section 12).

Holds engine weights, confidence thresholds and FAILURE_POINT_PRIORITY (tie-break order).
Never holds money limits, caps or legal rules (RL-11); those live in rules/*.yaml.
Values are read at call time by app.cases.engine, so a change here takes effect immediately.
"""

from __future__ import annotations

# Tie-break: when several failure points match, the first one in this order wins (FE-07).
# The safest outcome comes first: a contradiction always goes to a human.
FAILURE_POINT_PRIORITY: tuple[str, ...] = (
    "CLAIM_CONTRADICTS_EVIDENCE",
    "STOLEN_AFTER_DELIVERY",
    "BAD_ADDRESS",
    "WRONG_ITEM",
    "DAMAGED_IN_TRANSIT",
    "CARRIER_LOST",
)

# Support weight of each matched signal, per failure point. A signal not listed adds nothing.
SIGNAL_WEIGHTS: dict[str, dict[str, float]] = {
    "BAD_ADDRESS": {
        "address_check_failed": 2.0,
        "address_overridden_by_buyer": 2.0,
        "carrier_address_issue": 2.0,
        "packing_match": 1.0,
    },
    "CARRIER_LOST": {
        "no_delivered_scan_and_stale": 2.0,
        "delivered_scan_without_possession_proof": 2.0,
        "packing_match": 1.0,
        "address_valid": 0.5,
    },
    "STOLEN_AFTER_DELIVERY": {
        "possession_proof": 3.0,
        "delivered_scan": 0.5,
    },
    "DAMAGED_IN_TRANSIT": {
        "buyer_photo_shows_damage": 2.0,
        "packing_match": 1.0,
    },
    "WRONG_ITEM": {
        "packing_mismatch_not_cleared": 3.0,
    },
    "CLAIM_CONTRADICTS_EVIDENCE": {
        "label_weight_matches": 1.5,
        "packing_match": 1.5,
        "packing_mismatch_cleared": 1.0,
        "buyer_photo_shows_no_damage": 2.0,
    },
}

# Signals each failure point needs; a missing one lowers confidence and never counts as a match.
RELEVANT_SIGNALS: dict[str, tuple[str, ...]] = {
    "BAD_ADDRESS": ("address_check",),
    "CARRIER_LOST": ("address_check", "packing_check"),
    "STOLEN_AFTER_DELIVERY": (),
    "DAMAGED_IN_TRANSIT": ("packing_check", "buyer_photo_damage"),
    "WRONG_ITEM": ("packing_check",),
    "CLAIM_CONTRADICTS_EVIDENCE": ("packing_check", "label_weight_matches"),
}

MISSING_SIGNAL_PENALTY: float = 1.0
AMBIGUITY_PENALTY: float = 0.5  # more than one failure point matched
CONFIDENCE_HIGH_MIN: float = 3.0
CONFIDENCE_MEDIUM_MIN: float = 2.0

# When no failure point matches, the engine names this one per claim reason, at low
# confidence, so the case always goes to a human.
FALLBACK_BY_CLAIM: dict[str, str] = {
    "not_received": "CARRIER_LOST",
    "damaged": "DAMAGED_IN_TRANSIT",
    "wrong_item": "WRONG_ITEM",
    "empty_box": "CARRIER_LOST",
    "missing_item": "CARRIER_LOST",
}
