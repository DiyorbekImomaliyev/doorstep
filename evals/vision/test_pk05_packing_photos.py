"""PK-05: packing-check mismatch recall >= the configured target on the labelled photo set, with
correct items flagged no more often than the configured false-alarm limit.

Skips until the set has 40 labelled rows (evals/vision/manifest.yaml); the placeholder in
tests/unit/test_unbuilt_xfail.py stays until then. A row the model did not answer skips the
test rather than passing it.
"""

from __future__ import annotations

import pytest
import yaml

from evals.recording import Recorder, eval_model
from evals.vision.packing_eval import (
    MIN_ROWS,
    RECORDINGS,
    RESULTS,
    check_row,
    load_rows,
    summarise,
    write,
)

THRESHOLDS = RESULTS.parent.parent / "thresholds.yaml"


@pytest.mark.eval
def test_PK_05_mismatch_recall_on_the_labelled_photos():
    rows = load_rows()
    if len(rows) < MIN_ROWS:
        pytest.skip(f"the labelled photo set has {len(rows)} of {MIN_ROWS} rows")
    llm = Recorder(eval_model(), RECORDINGS, delay_s=6)
    out = summarise([check_row(r, llm) for r in rows])
    write(out)
    if out["not_run"]:
        pytest.skip(f"{out['not_run']} of {out['rows']} photos not answered by the eval model")
    limits = yaml.safe_load(THRESHOLDS.read_text(encoding="utf-8"))
    assert out["mismatch_recall"] >= limits["pk_05_mismatch_recall"], out
    assert out["false_alarms"] <= limits["pk_05_false_alarm_max"], out
