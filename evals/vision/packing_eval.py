"""PK-05: the packing check on a labelled photo set (evals/vision/manifest.yaml).

Each row sends one photo and one order line through the app's packing check
(app/ai/packing.py), the same code the seller's packing page uses.

- Mismatch recall (the gate): the share of mismatch rows NOT passed as "match". "Check" counts as
  caught, because the seller is stopped either way, and the check never auto-passes.
- Also reported: the share of mismatches named as "mismatch", whether the attribute named is
  the labelled one, and how many match rows were stopped (false alarms).

A row the model did not answer was not tested. Answers are recorded once and replayed
(evals/recording.py), keyed by the photo's bytes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from app.ai.explainer import StructuredLLM
from app.ai.llm import Image
from app.ai.packing import PackingChecker, Verdict

HERE = Path(__file__).resolve().parent
PHOTOS = HERE / "photos"
MANIFEST = HERE / "manifest.yaml"
RECORDINGS = HERE / "recordings.jsonl"
RESULTS = HERE.parent / "results" / "pk_05.json"
MIN_ROWS = 40
MAX_BYTES = 4 * 1024 * 1024
_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


class ManifestError(ValueError):
    """The labelled set is malformed: fix the manifest, not the eval."""


def load_rows(path: Path = MANIFEST, photos: Path = PHOTOS) -> list[dict[str, Any]]:
    rows = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("rows") or []
    for i, row in enumerate(rows, 1):
        where = f"row {i} ({row.get('photo')})"
        if row.get("label") not in ("match", "mismatch"):
            raise ManifestError(f"{where}: label must be match or mismatch")
        if row["label"] == "mismatch" and row.get("attribute") not in ("item", "colour", "size"):
            raise ManifestError(f"{where}: a mismatch names its attribute: item, colour or size")
        if not str(row.get("order_line", "")).strip():
            raise ManifestError(f"{where}: order_line is empty")
        file = photos / str(row.get("photo", ""))
        if file.suffix.lower() not in _TYPES or not file.is_file():
            raise ManifestError(f"{where}: photo missing or not JPEG/PNG/WebP")
        if file.stat().st_size > MAX_BYTES:
            raise ManifestError(f"{where}: photo over 4 MB")
    return rows


def image(row: dict[str, Any], photos: Path = PHOTOS) -> Image:
    file = photos / row["photo"]
    return Image(media_type=_TYPES[file.suffix.lower()], data=file.read_bytes())


def check_row(row: dict[str, Any], llm: StructuredLLM | None, photos: Path = PHOTOS) -> dict:
    verdict = PackingChecker(llm).check(row["order_line"], image(row, photos))
    answered = verdict.result is not None  # None: no model, or the model failed -> fallback
    return {
        "photo": row["photo"],
        "order_line": row["order_line"],
        "label": row["label"],
        "attribute": row.get("attribute"),
        "verdict": verdict.verdict.value,
        "named": verdict.attribute,
        "status": "scored" if answered else "not_run",
        "provider": verdict.provider,
    }


def summarise(results: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in results if r["status"] == "scored"]
    bad = [r for r in scored if r["label"] == "mismatch"]
    good = [r for r in scored if r["label"] == "match"]
    caught = [r for r in bad if r["verdict"] != Verdict.MATCH]
    named = [r for r in bad if r["verdict"] == Verdict.MISMATCH]

    def share(part: list[Any], whole: list[Any]) -> float | None:
        return round(len(part) / len(whole), 4) if whole else None

    return {
        "criterion": "PK-05",
        "rows": len(results),
        "scored": len(scored),
        "not_run": len(results) - len(scored),
        "mismatch_recall": share(caught, bad),
        "named_as_mismatch": share(named, bad),
        "attribute_correct": share([r for r in named if r["named"] == r["attribute"]], named),
        "false_alarms": share([r for r in good if r["verdict"] != Verdict.MATCH], good),
        "results": results,
    }


def write(out: dict[str, Any]) -> None:
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
