"""Seller and reviewer actions on a decided case (one tap each). Every money decision they make
gets a signed receipt (PR-01). Money still moves only through app.policy."""

from __future__ import annotations

from typing import Literal

from sqlalchemy.orm import Session

from app.clock import Clock
from app.orders.enums import ApproverKind
from app.orders.models import Case, MoneyAction
from app.policy.rules import RuleBook
from app.policy.service import Approver, PolicyService, _latest_decision
from app.proof.receipts import ReceiptSigner, issue_receipt

Action = Literal["approve", "decline", "reship"]


class CaseActions:
    def __init__(
        self, session: Session, rulebook: RuleBook, clock: Clock, signer: ReceiptSigner
    ) -> None:
        self.session = session
        self.policy = PolicyService(session, rulebook, clock)
        self.clock = clock
        self.signer = signer

    def act(self, case: Case, action: Action, approver: Approver) -> MoneyAction | None:
        """PolicyStateError/PolicyError propagate (callers answer 409)."""
        money: MoneyAction | None = None
        if action == "approve":
            money = self.policy.approve(case, approver)
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


def seller_approver(seller_id: str) -> Approver:
    return Approver(kind=ApproverKind.SELLER, id=seller_id)
