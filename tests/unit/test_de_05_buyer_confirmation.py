"""DE-05: the evidence says the buyer confirmed receipt only when the buyer did it themselves."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.cases.dispute_evidence import build_submission
from app.cases.signals import EvidenceKind
from app.orders.enums import CaseState
from app.orders.models import Evidence
from tests.factories import add_decision, make_case, make_order, make_seller, make_shipment

AT = datetime(2026, 11, 12, 13, 28, tzinfo=UTC)


def _notes(db, source: str | None) -> str:
    order = make_order(db, make_seller(db))
    make_shipment(db, order, tracking_number="SHIPPO_DELIVERED")
    if source is not None:
        db.add(
            Evidence(
                order_id=order.id,
                kind=EvidenceKind.BUYER_CONFIRMATION.value,
                ai_result={"source": source},
                created_at=AT,
            )
        )
    case = make_case(db, order, state=CaseState.DISPUTE_EVIDENCE)
    decision = add_decision(db, case)
    db.flush()
    return build_submission(db, order, case, decision, "https://x/verify").notes


@pytest.mark.parametrize(
    ("source", "said"),
    [
        ("buyer", "the buyer confirmed receipt on Doorstep's order page on 2026-11-12 13:28 UTC"),
        ("buyer_seal", "by scanning the seal packed inside the parcel on 2026-11-12 13:28 UTC"),
    ],
)
def test_DE_05_the_buyers_own_confirmation_is_in_the_evidence(db, source, said):
    assert said in _notes(db, source)


@pytest.mark.parametrize("source", ["seller_browser", "demo-seed", None])
def test_DE_05_no_confirmation_line_unless_the_buyer_made_it(db, source):
    assert "Buyer confirmation" not in _notes(db, source)
