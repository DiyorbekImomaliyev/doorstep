"""EC-01: the seller sees what Doorstep costs this month and what it settled before a dispute.

Only measured counts and published figures: orders this month, problems settled with no PayPal
dispute on the order, PayPal's own dispute fee, and Doorstep's price from rules/economics/.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from app.ops.economics import load_economics, monthly_price, seller_economics
from app.orders.enums import CaseState
from app.orders.models import Dispute, Order
from tests.factories import make_case, make_order, make_seller

RULES = Path("rules")


@pytest.fixture
def econ():
    return load_economics(RULES)


def test_EC_01_the_rule_file_is_versioned_and_sourced(econ):
    assert econ.id == "economics_v1" and econ.version and econ.source
    assert econ.paypal_dispute_fee["GBP"].amount_minor == 1200
    assert econ.paypal_dispute_fee["USD"].amount_minor == 1500
    assert all(
        f.source.startswith("https://www.paypal.com/") for f in econ.paypal_dispute_fee.values()
    )


@pytest.mark.parametrize(
    ("orders", "gbp"), [(0, 0), (100, 0), (101, 8), (200, 800), (1000, 4900), (50_000, 4900)]
)
def test_EC_01_price_is_free_then_per_order_then_capped(econ, orders, gbp):
    assert monthly_price(econ, orders, "GBP") == gbp


def test_EC_01_this_month_counts_and_settled_before_dispute(db, clock, econ):
    seller = make_seller(db, region="GB")
    other = make_seller(db, region="GB")
    settled = make_case(db, make_order(db, seller), state=CaseState.SETTLED)
    make_case(db, make_order(db, seller), state=CaseState.AWAITING_SELLER)  # not settled yet
    disputed = make_order(db, seller)
    make_case(db, disputed, state=CaseState.SETTLED)
    db.add(Dispute(order_id=disputed.id, paypal_dispute_id="PP-D-1", status="resolved"))
    old = make_order(db, seller)
    old.created_at = clock.now() - timedelta(days=40)
    make_order(db, other)  # another seller's order never counts
    for order in db.query(Order).all():  # dated by the test clock, not the wall clock
        if order is not old:
            order.created_at = clock.now()
    db.flush()

    view = seller_economics(db, seller, econ, clock.now())
    assert view.currency == "GBP"
    assert view.orders_this_month == 3  # the 40-day-old order is last month
    assert view.free_orders_per_month == 100
    assert view.price_minor == 0
    assert view.settled_before_dispute == 1
    assert view.dispute_fee_minor == 1200
    assert view.fee_source.startswith("https://www.paypal.com/uk/")
    assert settled.state == CaseState.SETTLED.value


def test_EC_01_a_us_seller_sees_dollars(db, clock, econ):
    view = seller_economics(db, make_seller(db, region="US"), econ, clock.now())
    assert (view.currency, view.dispute_fee_minor, view.price_minor) == ("USD", 1500, 0)


def test_EC_01_the_seller_route_answers_with_the_view(db, clock, session_factory, settings):
    from fastapi.testclient import TestClient

    from app.api.deps import AppDeps, Services
    from app.main import create_app
    from app.proof.receipts import ReceiptSigner
    from app.runtime import ROOT
    from app.security.links import LinkSigner

    secret = "e" * 32  # a test-only signing secret
    links = LinkSigner(secret, clock)
    deps = AppDeps(
        settings=settings,
        session_factory=session_factory,
        paypal_verifier=None,
        clock=clock,
        rules_dir=ROOT / "rules",
        services=Services(
            links=links, receipts=ReceiptSigner.generate(), orders=None, approved=None
        ),
    )
    seller = make_seller(db, region="US")
    db.commit()
    api = TestClient(create_app(deps))
    body = api.get(f"/api/seller/{links.issue(seller.id, scope='seller')}/economics").json()
    assert body["currency"] == "USD" and body["dispute_fee_minor"] == 1500
    assert body["rule"].startswith("economics_v1")
    assert api.get("/api/seller/not-a-token/economics").status_code in (401, 403, 404)
