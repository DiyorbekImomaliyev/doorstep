"""A receipt records a change, so a repeated tap signs nothing new.

approve() is idempotent on money (PO-07): a second tap returns the same money action. It used to
sign a second "approve" receipt anyway, so the public chain claimed two approvals of one refund.
"""

from __future__ import annotations

from sqlalchemy import select

from app.cases.actions import CaseActions
from app.orders.enums import ApproverKind, CaseState
from app.policy.service import Approver, PolicyService
from app.proof.models import Receipt
from app.proof.receipts import ReceiptSigner
from tests.factories import make_case, make_order, make_seller
from tests.helpers import engine_result, facts_for

SELLER = Approver(kind=ApproverKind.SELLER, id="seller-1")


def _awaiting(db, rulebook, clock):
    order = make_order(db, make_seller(db), amount_minor=8000)  # over the 5000 auto limit
    case = make_case(db, order)
    PolicyService(db, rulebook, clock).decide(case, engine_result(), facts_for(order))
    assert case.state == CaseState.AWAITING_SELLER
    return case


def _approve_receipts(db, case_id):
    return list(
        db.scalars(select(Receipt).where(Receipt.case_id == case_id, Receipt.kind == "approve"))
    )


def test_a_second_approve_signs_no_second_receipt(db, rulebook, clock):
    case = _awaiting(db, rulebook, clock)
    actions = CaseActions(db, rulebook, clock, ReceiptSigner.generate())
    first = actions.act(case, "approve", SELLER)
    second = actions.act(case, "approve", SELLER)
    assert first is not None and second is not None and first.id == second.id
    assert len(_approve_receipts(db, case.id)) == 1
