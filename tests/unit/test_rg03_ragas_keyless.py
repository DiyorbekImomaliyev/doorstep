"""RG-03 without a model key: the Ragas eval's plumbing, with a stand-in reader and judge.

Real Ragas code builds the prompts and computes the scores. Only the model answers are
stand-ins, sent through the app's adapter interface. The live eval is
evals/rag/test_rg03_ragas.py.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel
from ragas.metrics.collections.context_recall.util import (
    ContextRecallClassification,
    ContextRecallOutput,
)
from ragas.metrics.collections.faithfulness.util import (
    NLIStatementOutput,
    StatementFaithfulnessAnswer,
    StatementGeneratorOutput,
)

from app.ai.llm import LLMUnavailable
from app.ai.schemas import PolicyAnswer
from evals.rag import ragas_eval
from evals.rag.ragas_eval import AdapterRagasLLM, load_rows, run, score_row
from evals.recording import PaidKeyRefused

ROWS = load_rows()


class Reader:
    """Answers by quoting the first retrieved passage and citing it."""

    def complete_structured(
        self, *, schema: type[BaseModel], system: str, user: str, **kwargs: Any
    ) -> BaseModel:
        start = user.index("<passage id='") + len("<passage id='")
        pid = user[start : user.index("'", start)]
        text = user[user.index(">", start) + 1 : user.index("</passage>", start)]
        return PolicyAnswer(answer=text, cited_passage_ids=[pid])


class Judge:
    """Two statements; the second is unsupported, so faithfulness is 0.5. Recall is 1."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete_structured(
        self, *, schema: type[BaseModel], system: str, user: str, **kwargs: Any
    ) -> BaseModel:
        self.calls.append(schema.__name__)
        if schema is StatementGeneratorOutput:
            return StatementGeneratorOutput(statements=["supported", "invented"])
        if schema is NLIStatementOutput:
            verdicts = [("supported", 1), ("invented", 0)]
            return NLIStatementOutput(
                statements=[
                    StatementFaithfulnessAnswer(statement=s, reason="r", verdict=v)
                    for s, v in verdicts
                ]
            )
        assert schema is ContextRecallOutput
        return ContextRecallOutput(
            classifications=[ContextRecallClassification(statement="s", reason="r", attributed=1)]
        )


def test_RG_03_set_has_30_questions_over_uk_and_us_policies():
    assert len(ROWS) == 30 and len({r["id"] for r in ROWS}) == 30
    assert {r["region"] for r in ROWS} == {"GB", "US"}
    assert all(r["question"] and r["reference"] and r["policy"] for r in ROWS)


def test_RG_03_ragas_scores_through_the_app_adapter():
    judge = Judge()
    out = run(Reader(), judge)
    assert (out["questions"], out["scored"], out["not_run"]) == (30, 30, 0)
    assert out["faithfulness"] == 0.5 and out["context_recall"] == 1.0
    assert set(judge.calls) == {
        "StatementGeneratorOutput",
        "NLIStatementOutput",
        "ContextRecallOutput",
    }


def test_RG_03_a_question_the_model_did_not_answer_is_not_run():
    from app.policy.rules import load_rules
    from app.runtime import ROOT

    rulebook = load_rules(ROOT / "rules")
    no_reader = score_row(ROWS[0], None, Judge(), rulebook)  # quoted passages, not the model
    assert no_reader["status"] == "not_run"

    class Down:
        def complete_structured(self, **kwargs: Any) -> BaseModel:
            raise LLMUnavailable("quota")

    no_judge = score_row(ROWS[0], Reader(), Down(), rulebook)
    assert no_judge["status"] == "not_run" and "judge failed" in no_judge["why"]


def test_RG_03_the_adapter_wrapper_checks_the_answer_type():
    class Wrong:
        def complete_structured(self, **kwargs: Any) -> BaseModel:
            return PolicyAnswer(answer="x", cited_passage_ids=["a"])

    with pytest.raises(TypeError):
        AdapterRagasLLM(Wrong()).generate("p", StatementGeneratorOutput)


def test_RG_03_never_judges_on_the_paid_key(monkeypatch):
    monkeypatch.setenv("EVAL_LLM_PROVIDER", "anthropic")
    with pytest.raises(PaidKeyRefused):
        ragas_eval.judge_model()


def test_RG_03_ragas_analytics_are_off():
    import os

    import evals.rag  # noqa: F401

    assert os.environ["RAGAS_DO_NOT_TRACK"] == "true"
