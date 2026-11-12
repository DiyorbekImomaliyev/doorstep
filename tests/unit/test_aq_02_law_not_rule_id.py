"""AQ-02: text a buyer or a carrier reads names the law (from the rule's `source` in
rules/*.yaml), never the internal rule id. The seller summary keeps the id for the EX-01 guard
and the seller page shows it as the law's name. The model is a fake; no network."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.ai.explainer import ExplainContext, Explainer, law_name, plain_rule
from app.ai.schemas import ExplanationDraft
from app.cases.engine import FailurePoint
from app.orders.enums import CaseState
from tests.fakes_journey import FakeLLM
from tests.helpers import money

UK = yaml.safe_load(Path("rules/uk_possession_v1.yaml").read_text())
LAW = law_name(UK["source"])
RID, VER = UK["id"], str(UK["version"])


def _ctx(**overrides) -> ExplainContext:
    base = dict(
        order_ref="ORD12345",
        failure_point=FailurePoint.CARRIER_LOST,
        confidence="high",
        route=CaseState.AWAITING_SELLER,
        rule_id=RID,
        rule_version=VER,
        rule_source=UK["source"],
        liable_party="seller",
        refund=money(6000),
        reasons=["delivered scan but no proof of possession"],
        carrier="Royal Mail",
        tracking_number="SHIPPO_DELIVERED",
    )
    base.update(overrides)
    return ExplainContext(**base)


def test_AQ_02_the_law_name_is_read_from_the_rule_file() -> None:
    assert LAW and RID not in LAW and "http" not in LAW
    assert LAW == UK["source"].split(" — ")[0]


@pytest.mark.parametrize(
    "text",
    [
        f"Under rule {RID}, the seller will be asked to approve.",
        f"Rule: {RID} ({VER}), {LAW}.",
        f"Under rule {RID} (version {VER}, {LAW}), the seller is responsible.",
        f"Under the {LAW} ({RID}) the seller is responsible.",
        f"Under the {LAW} [{RID}], the goods stay at the seller's risk.",
        f"Under {RID} the seller carries the risk.",
    ],
)
def test_AQ_02_plain_rule_names_the_law_once_and_never_the_id(text: str) -> None:
    out = plain_rule(text, RID, VER, LAW)
    assert RID not in out and VER not in out
    assert out.count(LAW) == 1


def test_AQ_02_without_a_law_name_the_id_reads_as_the_rule() -> None:
    assert plain_rule(f"Under rule {RID} ({VER}), x.", RID, VER, "") == "Under the rule, x."
    assert plain_rule("Nothing to change.", RID, VER, LAW) == "Nothing to change."
    assert plain_rule("No rule here.", None, None, LAW) == "No rule here."


def test_AQ_02_buyer_message_and_claim_letter_name_the_law_not_the_id() -> None:
    draft = ExplanationDraft(
        summary=f"The carrier lost it. {LAW} ({RID}) puts the risk on you; approve the refund.",
        buyer_message=f"Under rule {RID}, the seller has been asked to approve a refund.",
        claim_letter=f"Under {RID} we claim for parcel SHIPPO_DELIVERED.",
        rule_id=RID,
    )
    llm = FakeLLM(draft)
    out = Explainer(llm).explain(_ctx())
    assert out.source == "model"
    assert RID in out.summary  # EX-01: the stored summary keeps the id; the page shows the law
    assert RID not in out.buyer_message and LAW in out.buyer_message
    assert out.claim_letter and RID not in out.claim_letter and LAW in out.claim_letter
    # The prompt names no rule: the law's name is read from the rule's source.
    assert RID not in llm.calls[0]["system"] and LAW not in llm.calls[0]["system"]


def test_AQ_02_a_draft_without_the_id_in_its_summary_is_still_blocked() -> None:
    draft = ExplanationDraft(
        summary=f"The carrier lost it. {LAW} puts the risk on you.",
        buyer_message="The seller has been asked to approve a refund.",
        rule_id=RID,
    )
    out = Explainer(FakeLLM(draft)).explain(_ctx())
    assert out.source == "template" and out.blocked_reason == "draft does not name the rule id"
