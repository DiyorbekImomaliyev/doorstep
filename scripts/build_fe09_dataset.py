"""Build the FE-09 failure-point eval set (60 labelled cases + 20 hold-out).

Every label comes from the product's failure-point table (and the delivered-scan audit for
contradicted scans), written here as a scenario with a one-line reason. Labels are never taken
from the engine's output: run `make evals` to see how the engine scores against them.

Region (GB / US) changes who is liable, not the failure point, so each scenario appears in both.
The hold-out set uses different combinations of the same scenarios and is never used for tuning.

Run: uv run python scripts/build_fe09_dataset.py
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "evals" / "datasets" / "failure_cases.jsonl"
HOLDOUT = ROOT / "evals" / "datasets" / "holdout" / "failure_cases.jsonl"


@dataclass(frozen=True)
class Case:
    name: str
    label: str
    why: str  # the failure-point table row this label comes from
    signals: dict[str, Any] = field(default_factory=dict)


def nr(**kw: Any) -> dict[str, Any]:
    return {"claim_reason": "not_received", **kw}


# ------------------------------------------------------------------ the 30 labelled scenarios
MAIN: list[Case] = [
    # Carrier lost / misdelivered: no delivered scan, or a delivered scan with no possession proof.
    Case(
        "delivered scan, no proof",
        "CARRIER_LOST",
        "delivered scan but no possession proof",
        nr(address_check="valid", delivered_scan=True, packing_check="match"),
    ),
    Case(
        "delivered scan, no proof, no packing photo",
        "CARRIER_LOST",
        "delivered scan but no possession proof",
        nr(address_check="valid", delivered_scan=True),
    ),
    Case(
        "no scan, tracking stale",
        "CARRIER_LOST",
        "no delivered scan and tracking went stale",
        nr(address_check="valid", tracking_stale=True, packing_check="match"),
    ),
    Case(
        "no scan, stale, address unknown",
        "CARRIER_LOST",
        "no delivered scan, tracking stale",
        nr(tracking_stale=True),
    ),
    Case(
        "delivered scan, carrier photo only",
        "CARRIER_LOST",
        "a carrier delivery photo is not proof the buyer had it",
        nr(address_check="valid", delivered_scan=True, proofs=["delivery_photo"]),
    ),
    Case(
        "contradicted scan, no proof",
        "CARRIER_LOST",
        "7a.2: the carrier's own data contradicts its delivered scan (misdelivered)",
        nr(address_check="valid", delivered_scan=True, scan_contradicted=True),
    ),
    Case(
        "contradicted scan, packing matched",
        "CARRIER_LOST",
        "7a.2: contradicted scan points to misdelivery",
        nr(
            address_check="valid",
            delivered_scan=True,
            scan_contradicted=True,
            packing_check="match",
            label_weight_matches=True,
        ),
    ),
    Case(
        "empty box, weight lower than label",
        "CARRIER_LOST",
        "contents went missing after the seller's weighed handover",
        {
            "claim_reason": "empty_box",
            "address_check": "valid",
            "delivered_scan": True,
            "packing_check": "match",
            "label_weight_matches": False,
        },
    ),
    Case(
        "missing item, weight lower than label",
        "CARRIER_LOST",
        "part of the contents went missing in the carrier's hands",
        {
            "claim_reason": "missing_item",
            "address_check": "valid",
            "delivered_scan": True,
            "packing_check": "match",
            "label_weight_matches": False,
        },
    ),
    # Bad address: the check failed or was overridden; a carrier "address issue" event.
    Case(
        "address failed, carrier address issue",
        "BAD_ADDRESS",
        "address check failed and the carrier reported an address issue",
        nr(address_check="invalid", carrier_address_issue=True),
    ),
    Case(
        "buyer override, carrier address issue",
        "BAD_ADDRESS",
        "buyer kept a failing address; carrier reported an address issue",
        nr(address_check="overridden", carrier_address_issue=True, packing_check="match"),
    ),
    Case(
        "valid check, carrier address issue",
        "BAD_ADDRESS",
        "carrier reported an address issue",
        nr(address_check="valid", carrier_address_issue=True, tracking_stale=True),
    ),
    Case(
        "buyer override, delivered without proof",
        "BAD_ADDRESS",
        "buyer kept an address that failed the check",
        nr(address_check="overridden", delivered_scan=True),
    ),
    Case(
        "address failed, stale tracking",
        "BAD_ADDRESS",
        "address check failed; the parcel never arrived",
        nr(address_check="invalid", tracking_stale=True),
    ),
    # Stolen after safe delivery: possession proven (signature, locker code, seal, doorstep photo).
    Case(
        "signed for",
        "STOLEN_AFTER_DELIVERY",
        "signature proves possession",
        nr(address_check="valid", delivered_scan=True, proofs=["signature"]),
    ),
    Case(
        "locker code used",
        "STOLEN_AFTER_DELIVERY",
        "locker pickup code proves possession",
        nr(address_check="valid", delivered_scan=True, proofs=["locker_pickup_code"]),
    ),
    Case(
        "seal scanned",
        "STOLEN_AFTER_DELIVERY",
        "a counted seal scan proves possession",
        nr(address_check="valid", delivered_scan=True, proofs=["buyer_seal_scan"]),
    ),
    Case(
        "photo at the buyer's address",
        "STOLEN_AFTER_DELIVERY",
        "a photo at the buyer's address counts as possession proof in the rule files",
        nr(address_check="valid", delivered_scan=True, proofs=["photo_at_buyer_address"]),
    ),
    Case(
        "signed and seal scanned",
        "STOLEN_AFTER_DELIVERY",
        "two possession proofs",
        nr(
            address_check="valid",
            delivered_scan=True,
            proofs=["signature", "buyer_seal_scan"],
            packing_check="match",
        ),
    ),
    # Damaged in transit: packing photo clean; buyer photo shows damage.
    Case(
        "damaged, clean packing, damage photo",
        "DAMAGED_IN_TRANSIT",
        "packing photo clean; buyer photo shows damage",
        {
            "claim_reason": "damaged",
            "address_check": "valid",
            "delivered_scan": True,
            "packing_check": "match",
            "buyer_photo_damage": True,
        },
    ),
    Case(
        "damaged, no packing photo, damage photo",
        "DAMAGED_IN_TRANSIT",
        "buyer photo shows damage",
        {"claim_reason": "damaged", "delivered_scan": True, "buyer_photo_damage": True},
    ),
    Case(
        "damaged, signed for, damage photo",
        "DAMAGED_IN_TRANSIT",
        "possession does not rule out damage in transit; buyer photo shows damage",
        {
            "claim_reason": "damaged",
            "delivered_scan": True,
            "proofs": ["signature"],
            "packing_check": "match",
            "buyer_photo_damage": True,
        },
    ),
    Case(
        "damaged, clean packing, no buyer photo yet",
        "DAMAGED_IN_TRANSIT",
        "damage claimed and nothing contradicts it",
        {"claim_reason": "damaged", "delivered_scan": True, "packing_check": "match"},
    ),
    # Wrong item packed: a packing mismatch the seller ignored.
    Case(
        "wrong item, mismatch ignored",
        "WRONG_ITEM",
        "packing photo mismatch ignored",
        {"claim_reason": "wrong_item", "delivered_scan": True, "packing_check": "mismatch"},
    ),
    Case(
        "wrong item, mismatch cleared by seller",
        "WRONG_ITEM",
        "the photo check flagged it and the buyer confirms it",
        {
            "claim_reason": "wrong_item",
            "delivered_scan": True,
            "packing_check": "mismatch",
            "packing_mismatch_cleared": True,
        },
    ),
    Case(
        "missing item, packing mismatch",
        "WRONG_ITEM",
        "packing photo did not match the order",
        {"claim_reason": "missing_item", "delivered_scan": True, "packing_check": "mismatch"},
    ),
    Case(
        "wrong item, seal scanned, mismatch",
        "WRONG_ITEM",
        "possession proven; the packing photo mismatch explains it",
        {
            "claim_reason": "wrong_item",
            "delivered_scan": True,
            "proofs": ["buyer_seal_scan"],
            "packing_check": "mismatch",
        },
    ),
    # Claim contradicts evidence: e.g. "empty box" but weight and photo match.
    Case(
        "empty box, weight and photo match",
        "CLAIM_CONTRADICTS_EVIDENCE",
        "'empty box' but weight and photo match",
        {
            "claim_reason": "empty_box",
            "delivered_scan": True,
            "packing_check": "match",
            "label_weight_matches": True,
        },
    ),
    Case(
        "damaged, buyer photo shows no damage",
        "CLAIM_CONTRADICTS_EVIDENCE",
        "damage claimed but the buyer's own photo shows none",
        {
            "claim_reason": "damaged",
            "delivered_scan": True,
            "packing_check": "match",
            "buyer_photo_damage": False,
        },
    ),
    Case(
        "wrong item, packing photo matched",
        "CLAIM_CONTRADICTS_EVIDENCE",
        "'wrong item' but the packing photo matched the order",
        {
            "claim_reason": "wrong_item",
            "delivered_scan": True,
            "packing_check": "match",
            "label_weight_matches": True,
        },
    ),
]

# ------------------------------------------------------------------ hold-out: other combinations
HOLD: list[Case] = [
    Case(
        "delivered, no proof, packing check unclear",
        "CARRIER_LOST",
        "delivered scan but no possession proof",
        nr(address_check="valid", delivered_scan=True, packing_check="check"),
    ),
    Case(
        "stale, packing matched, weight matched",
        "CARRIER_LOST",
        "no delivered scan, stale",
        nr(
            address_check="valid",
            tracking_stale=True,
            packing_check="match",
            label_weight_matches=True,
        ),
    ),
    Case(
        "contradicted scan, carrier photo at door",
        "CARRIER_LOST",
        "7a.2: contradicted scan; a carrier photo is not possession",
        nr(
            address_check="valid",
            delivered_scan=True,
            scan_contradicted=True,
            proofs=["delivery_photo"],
        ),
    ),
    Case(
        "empty box, weight lower, no packing photo",
        "CARRIER_LOST",
        "contents went missing in the carrier's hands",
        {"claim_reason": "empty_box", "delivered_scan": True, "label_weight_matches": False},
    ),
    Case(
        "override, stale tracking",
        "BAD_ADDRESS",
        "buyer kept a failing address",
        nr(address_check="overridden", tracking_stale=True),
    ),
    Case(
        "failed check, delivered without proof",
        "BAD_ADDRESS",
        "address check failed",
        nr(address_check="invalid", delivered_scan=True, packing_check="match"),
    ),
    Case(
        "unknown check, carrier address issue",
        "BAD_ADDRESS",
        "carrier reported an address issue",
        nr(carrier_address_issue=True),
    ),
    Case(
        "locker code, packing matched",
        "STOLEN_AFTER_DELIVERY",
        "locker pickup code",
        nr(
            address_check="valid",
            delivered_scan=True,
            proofs=["locker_pickup_code"],
            packing_check="match",
        ),
    ),
    Case(
        "seal scanned, no delivered scan",
        "STOLEN_AFTER_DELIVERY",
        "a counted seal scan proves possession even before a delivered scan",
        nr(address_check="valid", proofs=["buyer_seal_scan"]),
    ),
    Case(
        "signed, address unknown",
        "STOLEN_AFTER_DELIVERY",
        "signature proves possession",
        nr(delivered_scan=True, proofs=["signature"]),
    ),
    Case(
        "damaged, locker pickup, damage photo",
        "DAMAGED_IN_TRANSIT",
        "buyer photo shows damage",
        {
            "claim_reason": "damaged",
            "delivered_scan": True,
            "proofs": ["locker_pickup_code"],
            "buyer_photo_damage": True,
        },
    ),
    Case(
        "damaged, packing unclear, damage photo",
        "DAMAGED_IN_TRANSIT",
        "buyer photo shows damage",
        {
            "claim_reason": "damaged",
            "delivered_scan": True,
            "packing_check": "check",
            "buyer_photo_damage": True,
        },
    ),
    Case(
        "wrong item, no packing photo",
        "WRONG_ITEM",
        "nothing contradicts a wrong-item claim; packing is the seller's",
        {"claim_reason": "wrong_item", "delivered_scan": True},
    ),
    Case(
        "missing item, mismatch cleared",
        "WRONG_ITEM",
        "packing photo flagged a mismatch",
        {
            "claim_reason": "missing_item",
            "delivered_scan": True,
            "packing_check": "mismatch",
            "packing_mismatch_cleared": True,
        },
    ),
    Case(
        "missing item, weight and photo match",
        "CLAIM_CONTRADICTS_EVIDENCE",
        "'missing item' but weight and photo match",
        {
            "claim_reason": "missing_item",
            "delivered_scan": True,
            "packing_check": "match",
            "label_weight_matches": True,
        },
    ),
    Case(
        "empty box, signed for, weight matches",
        "CLAIM_CONTRADICTS_EVIDENCE",
        "'empty box' but the weight matched",
        {
            "claim_reason": "empty_box",
            "delivered_scan": True,
            "proofs": ["signature"],
            "packing_check": "match",
            "label_weight_matches": True,
        },
    ),
    Case(
        "damaged, no damage in photo, seal scanned",
        "CLAIM_CONTRADICTS_EVIDENCE",
        "damage claimed but the buyer's own photo shows none",
        {
            "claim_reason": "damaged",
            "delivered_scan": True,
            "proofs": ["buyer_seal_scan"],
            "buyer_photo_damage": False,
        },
    ),
    Case(
        "wrong item, match, seal scanned",
        "CLAIM_CONTRADICTS_EVIDENCE",
        "'wrong item' but the packing photo matched",
        {
            "claim_reason": "wrong_item",
            "delivered_scan": True,
            "proofs": ["buyer_seal_scan"],
            "packing_check": "match",
        },
    ),
    Case(
        "delivered, no proof, address unknown, weight matched",
        "CARRIER_LOST",
        "delivered scan but no possession proof",
        nr(delivered_scan=True, label_weight_matches=True),
    ),
    Case(
        "photo at address, packing matched",
        "STOLEN_AFTER_DELIVERY",
        "a photo at the buyer's address is possession proof",
        nr(
            address_check="valid",
            delivered_scan=True,
            proofs=["photo_at_buyer_address"],
            packing_check="match",
        ),
    ),
]


def rows(cases: list[Case], regions: tuple[str, ...], prefix: str) -> list[dict[str, Any]]:
    out = []
    for region in regions:
        for i, c in enumerate(cases, 1):
            out.append(
                {
                    "id": f"{prefix}-{region}-{i:02d}",
                    "region": region,
                    "name": c.name,
                    "label": c.label,
                    "why": c.why,
                    "signals": c.signals,
                }
            )
    return out


def write(path: Path, data: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in data))


def main() -> None:
    assert len(MAIN) == 30 and len(HOLD) == 20
    write(OUT, rows(MAIN, ("GB", "US"), "fe"))  # 60: 30 UK, 30 US
    hold = rows(HOLD[:10], ("GB",), "ho") + rows(HOLD[10:], ("US",), "ho")
    write(HOLDOUT, hold)  # 20, never used for tuning
    print(f"wrote {OUT.relative_to(ROOT)} (60) and {HOLDOUT.relative_to(ROOT)} (20)")


if __name__ == "__main__":
    main()
