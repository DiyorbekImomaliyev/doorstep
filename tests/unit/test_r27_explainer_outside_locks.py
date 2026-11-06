"""R27 (review S7): no model call while the case's row locks are held.

report() locks the order (open_case) and, for the daily cap, the seller (R25). The explainer's
model call can take seconds, so the decision now commits with the template wording first and the
model's words replace it afterwards, outside any transaction. A model failure or a crash between
the two steps leaves a decided case with valid template text.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from app.ai.explainer import Explainer
from app.cases.engine import ClaimReason
from app.cases.pipeline import CasePipeline
from app.orders.models import Decision
from app.proof.receipts import ReceiptSigner
from tests.unit.test_ex_explainer import _draft
from tests.unit.test_sl_seal import _shipped


class ProbeLLM:
    """Records, at the moment of the call, whether the session still has a transaction open."""

    def __init__(self, session, answer: BaseModel | Exception) -> None:
        self.session, self.answer = session, answer
        self.in_transaction: list[bool] = []

    def complete_structured(self, *, schema: type[BaseModel], **kwargs: Any) -> BaseModel:
        self.in_transaction.append(self.session.in_transaction())
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _report(db, rulebook, clock, settings, llm):
    _, order, _ = _shipped(db, clock)
    db.commit()
    pipeline = CasePipeline(
        db, rulebook, clock, settings, Explainer(llm), ReceiptSigner.generate(), ai_claims=False
    )
    report = pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")
    db.commit()
    decision = db.scalars(select(Decision).where(Decision.case_id == report.case_id)).one()
    return report, decision


def test_R27_the_model_is_asked_after_the_decision_commits(db, rulebook, clock, settings):
    llm = ProbeLLM(db, _draft())
    report, decision = _report(db, rulebook, clock, settings, llm)
    assert llm.in_transaction == [False]
    assert decision.model_outputs["explainer"]["source"] == "model"
    assert decision.explanation == report.explanation == _draft().summary


def test_R27_a_failed_model_call_keeps_the_committed_template(db, rulebook, clock, settings):
    llm = ProbeLLM(db, RuntimeError("provider down"))
    report, decision = _report(db, rulebook, clock, settings, llm)
    assert llm.in_transaction == [False]
    assert decision.model_outputs["explainer"]["source"] == "template"
    assert decision.explanation == report.explanation and "uk_possession_v1" in report.explanation


@pytest.mark.parametrize("llm", [None])
def test_R27_without_a_model_the_report_is_one_transaction(db, rulebook, clock, settings, llm):
    """Template-only (the demo seed): nothing to wait for, so nothing commits early."""
    _, order, _ = _shipped(db, clock)
    db.commit()
    pipeline = CasePipeline(db, rulebook, clock, settings, Explainer(llm), ReceiptSigner.generate())
    report = pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")
    db.rollback()
    assert db.get(Decision, report.decision_id) is None
