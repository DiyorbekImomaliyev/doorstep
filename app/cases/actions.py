"""Seller and reviewer actions on a decided case (one tap each). Every money decision they make
gets a signed receipt (PR-01). Money still moves only through app.policy."""

from __future__ import annotations

from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cases.dispute_evidence import build_submission
from app.clock import Clock
from app.orders.enums import ApproverKind
from app.orders.models import Approval, Case, MoneyAction, Order
from app.policy.evidence import existing_evidence, queue_dispute_evidence
from app.policy.rules import RuleBook
from app.policy.service import Approver, PolicyError, PolicyService, _latest_decision
from app.proof.receipts import ReceiptSigner, issue_receipt

Action = Literal["approve", "decline", "reship", "send-evidence"]


class CaseActions:
    def __init__(
        self, session: Session, rulebook: RuleBook, clock: Clock, signer: ReceiptSigner
    ) -> None:
        self.session = session
        self.policy = PolicyService(session, rulebook, clock)
        self.clock = clock
        self.signer = signer

    def act(
        self, case: Case, action: Action, approver: Approver, *, verify_url: str = ""
    ) -> MoneyAction | None:
        """PolicyStateError/PolicyError propagate (callers answer 409)."""
        money: MoneyAction | None = None
        if action == "send-evidence":
            return self.send_evidence(case, approver, verify_url=verify_url)
        if action == "approve":
            # Lock first, so a concurrent second tap reads the state the first one left
            # (design review of R31); approve() takes the same lock again.
            self.session.refresh(case, with_for_update=True)
            before = (case.state, self._approvals(case.id))
            money = self.policy.approve(case, approver)
            if (case.state, self._approvals(case.id)) == before:
                return money  # R31 (review S13): a repeated tap changed nothing; sign nothing
        elif action == "decline":
            self.policy.decline(case, approver)
        else:
            self.policy.settle_with_reship(case, approver)
        decision = _latest_decision(self.session, case.id)
        issue_receipt(
            self.session,
            self.signer,
            case_id=case.id,
            kind=action,
            payload={
                "decision_id": decision.id if decision else None,
                "approver_kind": approver.kind.value,
                "money_action_id": money.id if money else None,
                "amount_minor": money.amount_minor if money else None,
                "currency": money.currency if money else None,
                "case_state": case.state,
            },
            at=self.clock.now(),
        )
        return money

    def _approvals(self, case_id: str) -> int:
        return int(
            self.session.scalar(
                select(func.count()).select_from(Approval).where(Approval.case_id == case_id)
            )
            or 0
        )

    def send_evidence(self, case: Case, approver: Approver, *, verify_url: str) -> MoneyAction:
        """DE-01..04: facts-only evidence to the open PayPal dispute, through app.policy. A second
        tap returns the first submission and signs nothing new."""
        decision = _latest_decision(self.session, case.id)
        order = self.session.get(Order, case.order_id)
        if decision is None or order is None:
            raise PolicyError(f"case {case.id}: no decision recorded")
        already = existing_evidence(self.session, decision.id)
        if already is not None:
            return already
        submission = build_submission(self.session, order, case, decision, verify_url)
        money = queue_dispute_evidence(self.session, self.policy, case, approver, submission)
        issue_receipt(
            self.session,
            self.signer,
            case_id=case.id,
            kind="send-evidence",
            payload={
                "decision_id": decision.id,
                "approver_kind": approver.kind.value,
                "money_action_id": money.id,
                "request_id": money.request_id,
                "case_state": case.state,
            },
            at=self.clock.now(),
        )
        return money


def seller_approver(seller_id: str) -> Approver:
    return Approver(kind=ApproverKind.SELLER, id=seller_id)
