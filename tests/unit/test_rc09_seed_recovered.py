"""RC-09: the demo seed has one carrier claim already paid, so Recovered from carriers is not £0."""

from __future__ import annotations

from sqlalchemy import select

from app.cases.recovery import recovered_totals
from app.demo.seed import DEMO_TAG, _id, seed_scenario
from app.orders.models import AuditLog, Recovery
from app.proof.receipts import ReceiptSigner


def test_RC_09_seeded_shop_has_a_paid_carrier_claim(db, rulebook, clock, settings):
    seed_scenario(
        db, rulebook=rulebook, clock=clock, settings=settings, signer=ReceiptSigner.generate()
    )
    seller_id = _id(DEMO_TAG, "seller")
    totals = recovered_totals(db, seller_id)
    assert totals == {"GBP": 5800}  # the belt, paid in full (under the carrier's cover)
    paid = db.scalars(select(Recovery).where(Recovery.status == "PAID")).all()
    assert len(paid) == 1
    actions = {
        a.action for a in db.scalars(select(AuditLog).where(AuditLog.entity_id == paid[0].id))
    }
    assert {"recovery.prepared", "recovery.submitted", "recovery.paid"} <= actions
