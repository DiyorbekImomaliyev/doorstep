"""Every model output schema (hard rule 3: every LLM call returns a Pydantic model)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator


class ExplanationDraft(BaseModel):
    """What the explainer model must return (EX-01, EX-04)."""

    summary: str = Field(min_length=1, max_length=1200)
    buyer_message: str = Field(min_length=1, max_length=1200)
    claim_letter: str | None = Field(default=None, max_length=3000)
    rule_id: str = Field(min_length=1, max_length=64)


class PackingCheckResult(BaseModel):
    """Packing check (PK-01)."""

    matches: bool
    item: str = Field(max_length=200)
    colour: str = Field(max_length=60)
    size_seen: str = Field(max_length=40)
    reasons: list[str] = Field(default_factory=list, max_length=6)
    confidence: float = Field(ge=0.0, le=1.0)
    mismatch_attribute: Literal["item", "colour", "size", "none"] = "none"


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


# ---------------------------------------------------------------- G3 ask the grid

GridValue = Annotated[str, Field(min_length=1, max_length=40)]
GridSortColumn = Literal["money", "risk", "proof", "updated", "status"]


class GridMenu(BaseModel):
    """G3: what the Control Room grid can filter by, read in the browser from AG Grid's AI Toolkit
    schema and pruned to allowlisted columns: journey labels, next steps and delivery methods.
    Never an item, a buyer, an order reference or a postcode (rule 9)."""

    status: list[GridValue] = Field(default_factory=list, max_length=30)
    next: list[GridValue] = Field(default_factory=list, max_length=30)
    method: list[GridValue] = Field(default_factory=list, max_length=30)


class GridSort(BaseModel):
    col: GridSortColumn
    desc: bool = True


class GridPlan(BaseModel):
    """G3: how a grid question is read: which view, which filters, which order. Only values from
    the menu or fixed lists; the browser applies it as AG Grid state (filters and sort) and the
    seller sees the result as chips read back from the grid. It can't change or move anything."""

    answerable: bool
    view: Literal["needs_you", "in_transit", "cases", "all"] = "all"
    journey: list[GridValue] = Field(default_factory=list, max_length=6)
    next_step: list[GridValue] = Field(default_factory=list, max_length=6)
    delivery: list[Literal["home", "locker", "signature"]] = Field(
        default_factory=list, max_length=3
    )
    min_at_stake: float | None = Field(default=None, ge=0, le=100_000)
    max_proof: int | None = Field(default=None, ge=0, le=5)
    sort: list[GridSort] = Field(default_factory=list, max_length=2)
    show: list[Literal["next", "buyer", "updated"]] = Field(default_factory=list, max_length=3)
    # AQ-01: shown on one line beside the chips, so the model is asked for 12 words at most; a
    # longer note is still valid (the line ends in an ellipsis, the full text is its tooltip).
    note: str = Field(
        default="", max_length=200, description="At most 12 plain words; one line in the UI"
    )

    def fits(self, menu: GridMenu) -> bool:
        """Every journey and next-step value is one the grid offered."""
        return set(self.journey) <= set(menu.status) and set(self.next_step) <= set(menu.next)


# ---------------------------------------------------------------- PC policy check


class PolicyIssueDraft(BaseModel):
    """One issue the model proposes; code verifies it before the seller sees it (PC-02)."""

    quote: str = Field(max_length=600)
    fact_ids: list[str] = Field(default_factory=list, max_length=8)
    problem: str = Field(max_length=500)
    suggestion: str = Field(max_length=600)


class PolicyCheckDraft(BaseModel):
    """PC-02: the model's reading of the seller's policy; an empty list means it found none."""

    issues: list[PolicyIssueDraft] = Field(default_factory=list, max_length=6)
