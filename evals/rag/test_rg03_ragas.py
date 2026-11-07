"""RG-03: Ragas faithfulness >= the configured target, on 30 policy questions.

Replays recorded answers and verdicts (evals/rag/recordings.jsonl). A question with no
recording is asked on EVAL_LLM_PROVIDER when it has a key (never the paid key). A question the
model did not answer was not tested, so the test skips rather than passes.
"""

from __future__ import annotations

import json

import pytest
import yaml

from evals.rag.ragas_eval import RECORDINGS, RESULTS, judge_model, run
from evals.recording import Recorder, eval_model

THRESHOLDS = RESULTS.parent.parent / "thresholds.yaml"


@pytest.mark.eval
def test_RG_03_ragas_faithfulness():
    reader = Recorder(eval_model(), RECORDINGS, delay_s=6)  # no key: replay only
    out = run(reader, Recorder(judge_model(), RECORDINGS, delay_s=6))
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if out["not_run"]:
        pytest.skip(f"{out['not_run']} of {out['questions']} questions not answered by the model")
    target = yaml.safe_load(THRESHOLDS.read_text(encoding="utf-8"))["rg_03_faithfulness"]
    assert out["faithfulness"] >= target, out
