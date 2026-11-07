"""Packing step: store the check as evidence and move the order.

A match moves PAID -> PACKED. A mismatch or "check" keeps the order PAID until the seller
clears it ("I checked it, ship anyway"), which is recorded and later read by the failure-point
engine (packing_mismatch_cleared). Only the photo's SHA-256 fingerprint is stored here.
"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.llm import Image
from app.ai.packing import PackingChecker, PackingVerdict, Verdict
from app.cases.signals import EvidenceKind
from app.clock import Clock
from app.orders.enums import OrderState
from app.orders.models import Evidence, Order
from app.orders.state import transition_order


class PackingError(ValueError):
    """The order is not in a state where packing can be checked or cleared."""


class PackingService:
    def __init__(self, session: Session, clock: Clock, checker: PackingChecker) -> None:
        self.s = session
        self.clock = clock
        self.checker = checker

    def check(self, order: Order, photo: Image, *, actor: str = "seller") -> PackingVerdict:
        if order.state not in (OrderState.PAID.value, OrderState.PACKED.value):
            raise PackingError("only a paid order that has not shipped can be packing-checked")
        verdict = self.checker.check(order.item_description or "the ordered item", photo)
        result: dict[str, Any] = {
            "verdict": verdict.verdict.value,
            "attribute": verdict.attribute,
            "message": verdict.message,
            "provider": verdict.provider,
            "cleared": False,
        }
        if verdict.result is not None:
            result["model"] = verdict.result.model_dump(mode="json")
        self.s.add(
            Evidence(
                order_id=order.id,
                kind=EvidenceKind.PACKING_PHOTO.value,
                uri="sha256:" + hashlib.sha256(photo.data).hexdigest(),
                ai_result=result,
                created_at=self.clock.now(),
            )
        )
        self.s.flush()
        if verdict.verdict == Verdict.MATCH and order.state == OrderState.PAID.value:
            transition_order(self.s, order, OrderState.PACKED, actor=actor, reason="packing match")
        return verdict

    def clear(self, order: Order, *, actor: str) -> None:
        latest = self.s.scalars(
            select(Evidence)
            .where(Evidence.order_id == order.id, Evidence.kind == EvidenceKind.PACKING_PHOTO)
            .order_by(Evidence.created_at.desc())
        ).first()
        if latest is None or latest.ai_result.get("verdict") == Verdict.MATCH.value:
            raise PackingError("there is no flagged packing check to clear")
        if order.state != OrderState.PAID.value:
            raise PackingError("only a paid order that has not shipped can be cleared")
        latest.ai_result = {**latest.ai_result, "cleared": True, "cleared_by": actor[:64]}
        transition_order(self.s, order, OrderState.PACKED, actor=actor, reason="packing cleared")
