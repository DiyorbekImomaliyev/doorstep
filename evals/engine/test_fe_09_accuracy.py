"""FE-09 — failure-point accuracy on the labelled 60-case set; hold-out reported separately.

The engine is deterministic (FE-10), so this eval needs no model calls. Dataset format
(JSON lines): {"id": ..., "region": "GB"|"US", "signals": {...Signals fields...},
"label": "<FailurePoint>"}.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from app.cases.engine import Signals, assess

EVALS = Path(__file__).resolve().parent.parent
DATASET = EVALS / "datasets" / "failure_cases.jsonl"
HOLDOUT = EVALS / "datasets" / "holdout" / "failure_cases.jsonl"
THRESHOLDS = EVALS / "thresholds.yaml"
RESULTS = EVALS / "results" / "fe_09.json"


def _accuracy(path: Path) -> tuple[float, int]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    hits = sum(assess(Signals(**r["signals"])).failure_point == r["label"] for r in rows)
    return hits / len(rows), len(rows)


@pytest.mark.eval
def test_FE_09_failure_point_accuracy_meets_target():
    target = yaml.safe_load(THRESHOLDS.read_text())["fe_09_failure_point_accuracy"]
    accuracy, n = _accuracy(DATASET)
    assert n == 60, "the labelled set has 60 cases (30 UK, 30 US)"
    result = {"accuracy": accuracy, "n": n, "target": target}
    if HOLDOUT.exists():
        result["holdout_accuracy"], result["holdout_n"] = _accuracy(HOLDOUT)
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(result, indent=2))
    assert accuracy >= target
