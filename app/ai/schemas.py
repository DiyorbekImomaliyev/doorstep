"""Every model output schema (hard rule 3: every LLM call returns a Pydantic model)."""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, Field, model_validator


class ExplanationDraft(BaseModel):
    """What the explainer model must return (EX-01, EX-04)."""

    summary: str = Field(min_length=1, max_length=1200)
    buyer_message: str = Field(min_length=1, max_length=1200)
    claim_letter: str | None = Field(default=None, max_length=3000)
    rule_id: str = Field(min_length=1, max_length=64)


class PackingCheckResult(BaseModel):
    """SPEC section 10 packing check (PK-01)."""

    matches: bool
    item: str = Field(max_length=200)
    colour: str = Field(max_length=60)
    size_seen: str = Field(max_length=40)
    reasons: list[str] = Field(default_factory=list, max_length=6)
    confidence: float = Field(ge=0.0, le=1.0)
    mismatch_attribute: Literal["item", "colour", "size", "none"] = "none"


class DamageCheckResult(BaseModel):
    """SPEC section 10 damage check."""

    damage_visible: bool
    type: str = Field(default="", max_length=60)
    severity: Literal["none", "minor", "major"] = "none"
    reasons: list[str] = Field(default_factory=list, max_length=6)


class PolicyAnswer(BaseModel):
    """RG-01: an answer with the ids of the passages it cites."""

    answer: str = Field(min_length=1, max_length=1500)
    cited_passage_ids: list[str] = Field(default_factory=list)


class AgentStep(BaseModel):
    """One planned step of the Doorstep agent (AT-02). Ids are never chosen by the model:
    code fills them from the case's own order; a dispute id must be one PayPal listed."""

    tool: Literal[
        "get_order_details",
        "get_shipment_tracking",
        "list_disputes",
        "get_dispute",
        "request_refund",
        "finish",
    ]
    reason: str = Field(max_length=200)
    dispute_id: str | None = Field(default=None, max_length=64)


class DraftSentence(BaseModel):
    """One sentence of a carrier claim and the fact ids it relies on (RC-03)."""

    text: str = Field(min_length=1, max_length=400)
    fact_ids: list[str] = Field(default_factory=list, max_length=8)


class RecoveryDraft(BaseModel):
    """What the claim-pack model must return: sentences that each cite facts (RC-03)."""

    sentences: list[DraftSentence] = Field(min_length=1, max_length=14)


# ---------------------------------------------------------------- AS-06 Ask Doorstep

InsightMetric = Literal[
    "net_loss",
    "paid_out",
    "recovered",
    "claimed",
    "recovery_rate",
    "case_count",
    "open_cases",
    "order_count",
    "sales",
    "case_rate",
    "prevention_rate",
    "evidence_avg",
    "transit_days",
]
InsightGroup = Literal[
    "none",
    "carrier",
    "delivery_method",
    "failure_point",
    "liable_party",
    "decision",
    "category",
    "month",
    "week",
]
InsightPeriod = Literal["all_time", "this_month", "last_30_days", "last_90_days", "this_year"]

# Which table each measure is counted on, and the groupings that table can be split by. The
# browser's Studio model (web/src/insights/data.ts) has the same tables and fields.
CASE_METRICS: frozenset[str] = frozenset(
    ("net_loss", "paid_out", "recovered", "claimed", "recovery_rate", "case_count", "open_cases")
)
ORDER_METRICS: frozenset[str] = frozenset(get_args(InsightMetric)) - CASE_METRICS
CASE_GROUPS: frozenset[str] = frozenset(
    (
        "none",
        "carrier",
        "delivery_method",
        "failure_point",
        "liable_party",
        "decision",
        "month",
        "week",
    )
)
ORDER_GROUPS: frozenset[str] = frozenset(
    ("none", "carrier", "delivery_method", "category", "month", "week")
)


def groups_for(metric: str) -> frozenset[str]:
    return CASE_GROUPS if metric in CASE_METRICS else ORDER_GROUPS


class InsightPlan(BaseModel):
    """AS-06: how Ask Doorstep reads a question: one measure, split one way, over one period.

    Only names from fixed lists; never a number. The browser builds the widget and computes every
    figure from the seller's own rows."""

    answerable: bool
    metric: InsightMetric = "net_loss"
    group_by: InsightGroup = "none"
    period: InsightPeriod = "all_time"
    rank: Literal["highest", "lowest"] = "highest"
    note: str = Field(default="", max_length=240)

    @model_validator(mode="after")
    def _grouping_fits_the_measure(self) -> InsightPlan:
        if self.answerable and self.group_by not in groups_for(self.metric):
            raise ValueError(f"{self.metric} cannot be split by {self.group_by}")
        return self
