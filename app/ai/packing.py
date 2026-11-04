"""Packing check (SPEC section 10, PK-01..04): one photo against the order line.

The model only describes what it sees (PackingCheckResult). Code turns that into a verdict:
- confident and matching -> "match"
- confident, not matching, and naming the differing attribute -> "mismatch"
- anything else (low confidence, no attribute named, invalid output, no model) -> "check"
It never auto-passes: every failure path is "check", which asks the seller to look.
"""

from __future__ import annotations

import logging
from enum import StrEnum

from pydantic import BaseModel

from app.ai.explainer import StructuredLLM
from app.ai.llm import Image
from app.ai.schemas import PackingCheckResult
from tuning import engine_config

log = logging.getLogger("doorstep.ai.packing")


class Verdict(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    CHECK = "check"


class PackingVerdict(BaseModel):
    verdict: Verdict
    attribute: str | None = None  # item | colour | size, on a mismatch
    message: str
    result: PackingCheckResult | None = None
    provider: str | None = None


_ATTRIBUTE_TEXT = {"item": "item", "colour": "colour", "size": "size"}


def decide(result: PackingCheckResult, order_line: str) -> PackingVerdict:
    """Pure decision from the model's description; thresholds come from tuning config."""
    confident = result.confidence >= engine_config.PACKING_CONFIDENCE_MIN
    seen = f"{result.colour} {result.item}, size {result.size_seen or 'not visible'}".strip()
    if confident and result.matches:
        return PackingVerdict(
            verdict=Verdict.MATCH, message=f"Matches the order: {seen}.", result=result
        )
    if confident and not result.matches and result.mismatch_attribute in _ATTRIBUTE_TEXT:
        attribute = result.mismatch_attribute
        return PackingVerdict(
            verdict=Verdict.MISMATCH,
            attribute=attribute,
            message=(
                f"Check before shipping: the {_ATTRIBUTE_TEXT[attribute]} looks different. "
                f"Ordered: {order_line}. Seen: {seen}."
            ),
            result=result,
        )
    return PackingVerdict(
        verdict=Verdict.CHECK,
        message=(
            "The photo wasn't clear enough to be sure. Look at the item and confirm it matches."
        ),
        result=result,
    )


_FALLBACK = "The automatic check is unavailable right now. Look at the item and confirm it matches."


class PackingChecker:
    def __init__(self, llm: StructuredLLM | None) -> None:
        self.llm = llm

    def check(self, order_line: str, photo: Image) -> PackingVerdict:
        if self.llm is None:
            return PackingVerdict(verdict=Verdict.CHECK, message=_FALLBACK)
        user = (
            f"<order_line>{order_line[:200]}</order_line>\nCompare the photo with the order line."
        )
        try:
            result = self.llm.complete_structured(
                schema=PackingCheckResult,
                system=engine_config.PACKING_SYSTEM_PROMPT,
                user=user,
                temperature=0,
                images=[photo],
                purpose="packing_check",
            )
        except Exception as exc:  # invalid twice, provider down, no provider -> "check"
            log.warning("packing check failed (%s); asking the seller", type(exc).__name__)
            return PackingVerdict(verdict=Verdict.CHECK, message=_FALLBACK)
        assert isinstance(result, PackingCheckResult)
        verdict = decide(result, order_line)
        verdict.provider = getattr(self.llm, "last_provider", None)
        return verdict
