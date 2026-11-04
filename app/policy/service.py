"""Money decisions (SPEC section 7). The ONLY path to refunds (hard rule 1).

Order of checks (PO-13): open dispute -> hold conditions -> no rule set -> liability ->
daily cap -> auto-approve limit -> pre-refund re-check (at execution).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.cases.engine import EngineResult
from app.clock import Clock
from app.money import Money
from app.orders.enums import ApproverKind, BuyerType, CaseState, Fix, ProofKind
from app.orders.models import Case, MoneyAction, Order
from app.policy.ports import RefundGateway
from app.policy.rules import RuleBook
from app.worker.models import Job

REFUND_JOB = "paypal.refund"  # payload: {"money_action_id": ...}


class CaseFacts(BaseModel):
    buyer_country: str
    buyer_type: BuyerType = BuyerType.CONSUMER
    proofs: frozenset[ProofKind] = frozenset()
    claim_amount: Money
    preferred_fix: Fix = Fix.REFUND


class Approver(BaseModel):
    kind: ApproverKind
    id: str


class PolicyDecision(BaseModel):
    route: CaseState
    fix: Fix | None
    refund_amount: Money | None
    rule_id: str | None
    rule_version: str | None
    reasons: list[str]
    money_action_id: str | None = None


class PolicyService:
    def __init__(self, session: Session, rulebook: RuleBook, clock: Clock) -> None:
        self.session = session
        self.rulebook = rulebook
        self.clock = clock

    def decide(
        self,
        case: Case,
        result: EngineResult,
        facts: CaseFacts,
        *,
        model_outputs: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        """Case must be GATHERING_EVIDENCE. Records a Decision, moves the case to DECIDED and
        then to the route; AUTO_REFUND also records a system approval, a queued MoneyAction and
        a REFUND_JOB."""
        raise NotImplementedError

    def approve(self, case: Case, approver: Approver) -> MoneyAction | None:
        """Seller (AWAITING_SELLER) or reviewer (HOLD_FOR_REVIEW) approves the latest decision.
        Idempotent: approving twice yields one MoneyAction (PO-07)."""
        raise NotImplementedError

    def decline(self, case: Case, approver: Approver) -> None:
        """Seller declines: AWAITING_SELLER -> CLOSED, no money moves (PO-03)."""
        raise NotImplementedError

    def settle_with_reship(self, case: Case, approver: Approver) -> None:
        """Reship fix: records the approval and settles with no MoneyAction (PO-11)."""
        raise NotImplementedError

    def record_external_refund(
        self, order: Order, amount: Money, *, paypal_refund_id: str
    ) -> MoneyAction:
        """A refund made outside Doorstep (PAYMENT.CAPTURE.REFUNDED) (PO-06)."""
        raise NotImplementedError


class RefundJobHandler:
    """Executes REFUND_JOB: pre-refund re-check (dispute, capture state, evidence fingerprint),
    then refunds with the MoneyAction's request_id. Retries keep the same key (PO-12)."""

    def __init__(self, gateway: RefundGateway, rulebook: RuleBook, clock: Clock) -> None:
        self.gateway = gateway
        self.rulebook = rulebook
        self.clock = clock

    def run(self, session: Session, job: Job) -> None:
        raise NotImplementedError

    def on_failed(self, session: Session, job: Job, error: Exception) -> None:
        raise NotImplementedError
