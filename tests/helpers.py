"""Shared scenario helpers for policy, state and property tests."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy.orm import Session, sessionmaker

from app.cases.engine import Confidence, EngineResult, FailurePoint
from app.clock import Clock
from app.config import Settings
from app.money import Money
from app.orders.enums import Fix, ProofKind
from app.orders.models import Order
from app.policy.service import CaseFacts
from app.worker.runner import JobHandler, JobRunner


def money(minor: int, currency: str = "GBP") -> Money:
    return Money(amount_minor=minor, currency=currency)


def engine_result(
    fp: FailurePoint = FailurePoint.CARRIER_LOST,
    confidence: Confidence = Confidence.HIGH,
    *,
    contradiction: bool = False,
) -> EngineResult:
    return EngineResult(
        failure_point=fp,
        confidence=confidence,
        reasons=["test signal"],
        candidates=[fp],
        missing_signals=[],
        contradiction=contradiction,
    )


def facts_for(
    order: Order,
    amount_minor: int | None = None,
    *,
    proofs: frozenset[ProofKind] = frozenset(),
    fix: Fix = Fix.REFUND,
    country: str | None = None,
) -> CaseFacts:
    return CaseFacts(
        buyer_country=country or order.buyer_country,
        proofs=proofs,
        claim_amount=money(
            order.amount_minor if amount_minor is None else amount_minor, order.currency
        ),
        preferred_fix=fix,
    )


def run_jobs(
    session_factory: sessionmaker[Session],
    handlers: Mapping[str, JobHandler],
    clock: Clock,
    settings: Settings,
    *,
    rounds: int = 1,
    advance_s: float = 0,
) -> None:
    runner = JobRunner(session_factory, handlers, clock, settings)
    for _ in range(rounds):
        runner.run_due()
        if advance_s:
            clock.advance(advance_s)  # type: ignore[attr-defined]
