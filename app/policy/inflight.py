"""Whether a refund of a case is on its way (PO-13, PO-15, AT-07).

A module with no PayPal imports, so the case agent's reactions can ask it without reaching any
PayPal write module (the import contracts forbid that, even indirectly).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.orders.enums import MoneyActionKind, MoneyActionStatus
from app.orders.models import MoneyAction

_REFUND_KINDS = (MoneyActionKind.REFUND.value, MoneyActionKind.PARTIAL_REFUND.value)


def refund_in_flight(session: Session, case_id: str) -> bool:
    """A refund of this case is queued or pending at PayPal (PO-13, PO-15)."""
    found = session.scalar(
        select(MoneyAction.id).where(
            MoneyAction.case_id == case_id,
            MoneyAction.kind.in_(_REFUND_KINDS),
            MoneyAction.status.in_(
                (MoneyActionStatus.QUEUED.value, MoneyActionStatus.PENDING.value)
            ),
        )
    )
    return found is not None
