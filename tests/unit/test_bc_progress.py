"""BC-03 / E2E-02: a "Not here" report never shows the parcel as received, and the seeded
history reads in the order it happened."""

from __future__ import annotations

from sqlalchemy import select

from app.ai.explainer import Explainer
from app.api.views import buyer_view, timeline
from app.cases.engine import ClaimReason
from app.cases.pipeline import CasePipeline
from app.demo.seed import DEMO_TAG, _id, seed_scenario
from app.orders.enums import OrderState
from app.orders.models import Order
from app.proof.receipts import ReceiptSigner
from tests.factories import make_order, make_seller


def test_BC_03_progress_stops_where_the_parcel_got_to(db, rulebook, clock, settings):
    order = make_order(db, make_seller(db), state=OrderState.DELIVERED)
    pipeline = CasePipeline(db, rulebook, clock, settings, Explainer(), ReceiptSigner.generate())
    pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")
    view = buyer_view(db, order)
    assert view.state == OrderState.CASE_OPEN
    assert view.progress_state == OrderState.DELIVERED


def test_BC_03_progress_is_the_state_when_no_case(db):
    order = make_order(db, make_seller(db), state=OrderState.IN_TRANSIT)
    assert buyer_view(db, order).progress_state == OrderState.IN_TRANSIT


def test_EX_01_buyer_message_uses_the_same_order_reference(db, rulebook, clock, settings):
    order = make_order(db, make_seller(db), state=OrderState.DELIVERED)
    pipeline = CasePipeline(db, rulebook, clock, settings, Explainer(), ReceiptSigner.generate())
    pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")
    view = buyer_view(db, order)
    assert view.order_ref in (view.case_message or "")


def test_E2E_02_seeded_timeline_is_in_order(db, rulebook, clock, settings):
    seed_scenario(
        db, rulebook=rulebook, clock=clock, settings=settings, signer=ReceiptSigner.generate()
    )
    order = db.scalars(select(Order).where(Order.id == _id(DEMO_TAG, "sam-jacket"))).one()
    titles = [
        item.title for item in sorted(timeline(db, order, for_buyer=True), key=lambda i: i.at)
    ]
    paid = next(i for i, t in enumerate(titles) if t.startswith("Paid"))
    delivered = next(i for i, t in enumerate(titles) if "delivered" in t.lower())
    assert paid < delivered
