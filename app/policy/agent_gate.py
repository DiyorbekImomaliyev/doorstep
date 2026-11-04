"""The agent's only money tool (AT-02, AT-04): `request_refund` asks the policy what it decided.

The policy is the only path to a refund (hard rule 1). This gate creates nothing: under the
seller's auto-approve limit the policy has already queued the refund (with its idempotency key)
when it decided the case; anything else is reported back as escalated, held or on the dispute
path, so the agent stops and the seller or a reviewer decides.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.orders.enums import CaseState, MoneyActionKind
from app.orders.models import Case, MoneyAction


class RefundRequest(StrEnum):
    QUEUED = "refund_queued"
    ESCALATED = "escalated_to_seller"
    HELD = "held_for_review"
    DISPUTE = "dispute_path"
    NOT_REFUNDABLE = "not_refundable"


class RefundRequestOutcome(BaseModel):
    status: RefundRequest
    message: str
    money_action_id: str | None = None


_MESSAGES = {
    RefundRequest.ESCALATED: "Over the seller's auto-approve limit or needs the seller: "
    "escalated to the seller, who decides with one tap. No refund was created.",
    RefundRequest.HELD: "Held for human review (contradicting or missing evidence). "
    "No refund was created; nobody is denied automatically.",
    RefundRequest.DISPUTE: "A PayPal dispute is open: evidence goes through the Disputes API, "
    "not a refund outside it.",
    RefundRequest.NOT_REFUNDABLE: "This case cannot be refunded now.",
}

_STATE_OUTCOME = {
    CaseState.AWAITING_SELLER.value: RefundRequest.ESCALATED,
    CaseState.HOLD_FOR_REVIEW.value: RefundRequest.HELD,
    CaseState.DISPUTE_EVIDENCE.value: RefundRequest.DISPUTE,
}


def request_refund(session: Session, case_id: str) -> RefundRequestOutcome:
    case = session.get(Case, case_id)
    if case is None:
        return RefundRequestOutcome(
            status=RefundRequest.NOT_REFUNDABLE, message=_MESSAGES[RefundRequest.NOT_REFUNDABLE]
        )
    queued = session.scalars(
        select(MoneyAction).where(
            MoneyAction.case_id == case.id,
            MoneyAction.kind.in_(
                [MoneyActionKind.REFUND.value, MoneyActionKind.PARTIAL_REFUND.value]
            ),
        )
    ).first()
    if queued is not None:
        return RefundRequestOutcome(
            status=RefundRequest.QUEUED,
            message=f"Refund {queued.status.lower()} under the seller's rules "
            "(policy decision, idempotent PayPal request).",
            money_action_id=queued.id,
        )
    status = _STATE_OUTCOME.get(case.state, RefundRequest.NOT_REFUNDABLE)
    return RefundRequestOutcome(status=status, message=_MESSAGES[status])
