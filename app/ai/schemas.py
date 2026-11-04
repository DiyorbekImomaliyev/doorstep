"""Every model output schema (hard rule 3: every LLM call returns a Pydantic model)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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
