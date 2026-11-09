"""PO-15: a person can close a held case without a refund, and the buyer is told how to go on.

Found by the Nov 7 review: a case held for review could only be approved or decided again, so a
seller who checked the evidence and found the parcel had arrived had no way to finish it. A person
(never the system: hard rule 7) can now close it, only while no refund is on its way. The buyer is
told the case was closed after a person checked it, and that a PayPal dispute is still open to
them; the same message follows a seller's decline.
"""

from __future__ import annotations

import pytest

from app.api.views import buyer_outcome, case_view
from app.cases.actions import CaseActions
from app.orders.enums import ApproverKind, CaseState, MoneyActionStatus
from app.orders.models import Order
from app.policy.service import Approver, PolicyError, PolicyService, PolicyStateError
from app.proof.models import Receipt
from app.proof.receipts import ReceiptSigner
from tests.factories import make_case, make_order, make_seller, money_actions
from tests.unit.test_decide_again import (  # noqa: F401 - fixtures
    REVIEWER,
    held,
    pipeline,
    run_refunds,
)

SYSTEM = Approver(kind=ApproverKind.SYSTEM, id="policy")


def test_PO_15_a_person_closes_a_held_case(db, rulebook, clock, held):  # noqa: F811
    PolicyService(db, rulebook, clock).close_held(held, REVIEWER)
    assert held.state == CaseState.CLOSED.value
    assert case_view(db, held).can_close is False


def test_PO_15_a_held_case_offers_close(db, held):  # noqa: F811
    assert case_view(db, held).can_close is True


def test_PO_15_the_system_never_closes_a_case(db, rulebook, clock, held):  # noqa: F811
    with pytest.raises(PolicyError, match="person"):
        PolicyService(db, rulebook, clock).close_held(held, SYSTEM)
    assert held.state == CaseState.HOLD_FOR_REVIEW.value


def test_PO_15_only_a_held_case_can_be_closed(db, rulebook, clock):
    case = make_case(db, make_order(db, make_seller(db)), state=CaseState.AWAITING_SELLER)
    with pytest.raises(PolicyStateError, match="cannot be closed"):
        PolicyService(db, rulebook, clock).close_held(case, REVIEWER)
    assert case_view(db, case).can_close is False


@pytest.mark.parametrize("status", [MoneyActionStatus.QUEUED, MoneyActionStatus.PENDING])
def test_PO_15_not_while_a_refund_is_on_its_way(db, rulebook, clock, held, status):  # noqa: F811
    [ma] = money_actions(db, case_id=held.id)
    ma.status = status.value
    db.flush()
    with pytest.raises(PolicyStateError, match="on its way"):
        PolicyService(db, rulebook, clock).close_held(held, REVIEWER)
    assert case_view(db, held).can_close is False


def test_PO_15_the_close_is_signed_and_the_buyer_is_told(db, rulebook, clock, held):  # noqa: F811
    CaseActions(db, rulebook, clock, ReceiptSigner.generate()).act(held, "close", REVIEWER)
    kinds = [r.kind for r in db.query(Receipt).filter(Receipt.case_id == held.id)]
    assert "close" in kinds
    order = db.get(Order, held.order_id)
    message = buyer_outcome(db, order, held)
    assert message is not None
    assert "closed" in message and "PayPal dispute" in message


def test_PO_15_a_declined_case_tells_the_buyer_the_same(db, rulebook, clock):
    order = make_order(db, make_seller(db))
    case = make_case(db, order, state=CaseState.AWAITING_SELLER)
    PolicyService(db, rulebook, clock).decline(case, REVIEWER)
    message = buyer_outcome(db, order, case)
    assert message is not None and "PayPal dispute" in message


def test_PO_15_the_audit_says_why_the_case_closed(db, rulebook, clock, held):  # noqa: F811
    from sqlalchemy import select

    from app.orders.models import AuditLog

    PolicyService(db, rulebook, clock).close_held(held, REVIEWER)
    actions = db.scalars(select(AuditLog.action).where(AuditLog.entity_id == held.id)).all()
    assert "transition: closed after review" in actions


def test_PO_15_another_cases_refund_does_not_stop_this_close(db, rulebook, clock, held):  # noqa: F811
    other = make_case(db, make_order(db, make_seller(db)), state=CaseState.AWAITING_SELLER)
    [ma] = money_actions(db, case_id=held.id)
    ma.case_id = other.id  # the only queued refund belongs to another case
    ma.status = MoneyActionStatus.QUEUED.value
    db.flush()
    assert case_view(db, held).can_close is True
    PolicyService(db, rulebook, clock).close_held(held, REVIEWER)
    assert held.state == CaseState.CLOSED.value


def test_PO_15_only_a_refund_on_its_way_stops_the_close(db, rulebook, clock, held):  # noqa: F811
    from app.orders.models import MoneyAction

    order = db.get(Order, held.order_id)
    db.add(
        MoneyAction(
            order_id=held.order_id,
            seller_id=order.seller_id,
            case_id=held.id,
            kind="evidence",  # queued, but not a refund
            amount_minor=0,
            currency="GBP",
            status=MoneyActionStatus.QUEUED.value,
        )
    )
    db.flush()
    PolicyService(db, rulebook, clock).close_held(held, REVIEWER)
    assert held.state == CaseState.CLOSED.value
