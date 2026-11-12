"""RG-03: Ragas faithfulness for the policy reader, on 30 questions over six seller policies.

The policy reader (app/ai/policy_reader.py) answers each question from the passages it
retrieves. Ragas 0.4 scores each answer with its own prompts and scoring:

- faithfulness, the share of the answer's statements the retrieved passages support (the gate);
- context recall, the share of the reference answer the passages cover (reported only).

Every model call Ragas makes goes through app/ai/llm.py (AdapterRagasLLM below), so the app's one
model adapter stays the only way to reach a model (RULES.md). The reader's answers and the
judge's verdicts are recorded once and replayed (evals/recording.py), on the eval provider only.

A question is scored only when the model wrote the answer. If the reader fell back to quoting
passages, or the judge did not answer, the question is "not run", never counted as faithful.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel
from ragas.llms.base import InstructorBaseRagasLLM
from ragas.metrics.collections import ContextRecall, Faithfulness

from app.ai.explainer import StructuredLLM
from app.ai.llm import GeminiProvider, LLMAdapter, LLMError
from app.ai.policy_reader import PolicyReader, corpus, retrieve
from app.policy.rules import RuleBook, load_rules
from app.runtime import ROOT
from evals.recording import Recorder, eval_model

HERE = Path(__file__).resolve().parent
EVALS = HERE.parent
DATASET = EVALS / "datasets" / "policy_questions.jsonl"
RECORDINGS = HERE / "recordings.jsonl"
RESULTS = EVALS / "results" / "rg_03.json"

M = TypeVar("M", bound=BaseModel)

_JUDGE_SYSTEM = (
    "You are a careful evaluator. Follow the instructions in the user message exactly and "
    "answer only in the requested structure."
)


class AdapterRagasLLM(InstructorBaseRagasLLM):
    """Ragas's structured LLM interface, answered by the app's model adapter."""

    def __init__(self, llm: StructuredLLM) -> None:
        self.llm = llm

    def generate(self, prompt: str, response_model: type[M]) -> M:
        out = self.llm.complete_structured(
            schema=response_model,
            system=_JUDGE_SYSTEM,
            user=prompt,
            temperature=0,
            purpose="eval-ragas",
        )
        if not isinstance(out, response_model):
            raise TypeError(f"expected {response_model.__name__}, got {type(out).__name__}")
        return out

    async def agenerate(self, prompt: str, response_model: type[M]) -> M:
        return self.generate(prompt, response_model)


def load_rows(path: Path = DATASET) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def score_row(
    row: dict[str, Any], reader: StructuredLLM | None, judge: StructuredLLM, rulebook: RuleBook
) -> dict[str, Any]:
    rule = rulebook.select(row["region"])
    hits = retrieve(row["question"], corpus(row["policy"], rule))
    answer = PolicyReader(reader).answer(row["question"], seller_policy=row["policy"], rule=rule)
    out: dict[str, Any] = {
        "id": row["id"],
        "question": row["question"],
        "answer": answer.answer,
        "mode": answer.mode,
        "retrieved": [p.id for p in hits],
        "cited": [p.id for p in answer.citations],
    }
    if answer.mode != "model":  # quoted passages or the rule file: the model did not answer
        return {**out, "status": "not_run", "why": f"reader answered by {answer.mode}"}
    contexts = [p.text for p in hits]
    ragas_llm = AdapterRagasLLM(judge)
    try:
        faithful = asyncio.run(
            Faithfulness(llm=ragas_llm).ascore(
                user_input=row["question"], response=answer.answer, retrieved_contexts=contexts
            )
        ).value
        recall = asyncio.run(
            ContextRecall(llm=ragas_llm).ascore(
                user_input=row["question"], retrieved_contexts=contexts, reference=row["reference"]
            )
        ).value
    except LLMError as exc:
        return {**out, "status": "not_run", "why": f"judge failed: {type(exc).__name__}"}
    if faithful is None or math.isnan(float(faithful)):
        return {**out, "status": "not_run", "why": "Ragas found no statements in the answer"}
    return {
        **out,
        "status": "scored",
        "faithfulness": round(float(faithful), 4),
        "context_recall": None if recall is None else round(float(recall), 4),
    }


def run(reader: StructuredLLM | None, judge: StructuredLLM) -> dict[str, Any]:
    """Scores every question; the summary counts only questions the model answered."""
    rulebook = load_rules(ROOT / "rules")
    rows = [score_row(r, reader, judge, rulebook) for r in load_rows()]
    scored = [r for r in rows if r["status"] == "scored"]
    recalls = [r["context_recall"] for r in scored if r["context_recall"] is not None]
    return {
        "criterion": "RG-03",
        "questions": len(rows),
        "scored": len(scored),
        "not_run": len(rows) - len(scored),
        "faithfulness": round(sum(r["faithfulness"] for r in scored) / len(scored), 4)
        if scored
        else None,
        "context_recall": round(sum(recalls) / len(recalls), 4) if recalls else None,
        "rows": rows,
    }


def judge_model() -> StructuredLLM | None:
    """The judge. RAGAS_JUDGE_MODEL, when set, names another Gemini model on the eval key, so the
    model that wrote an answer does not grade it. Otherwise the eval model judges."""
    reader = eval_model()  # refuses the paid key
    name = os.environ.get("RAGAS_JUDGE_MODEL", "").strip()
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not name or not key or os.environ.get("EVAL_LLM_PROVIDER", "gemini") != "gemini":
        return reader
    return LLMAdapter(GeminiProvider(key, name))


def main() -> int:
    """uv run --env-file .env python -m evals.rag.ragas_eval"""
    reader = Recorder(eval_model(), RECORDINGS, delay_s=6)  # no key: replay only
    out = run(reader, Recorder(judge_model(), RECORDINGS, delay_s=6))
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    keys = ("questions", "scored", "not_run", "faithfulness", "context_recall")
    print(" ".join(f"{k}={out[k]}" for k in keys))
    for row in out["rows"]:
        if row["status"] != "scored" or row["faithfulness"] < 1:
            print(f"  {row['id']}: {row['status']} {row.get('faithfulness', row.get('why'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
