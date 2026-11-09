"""EC-02: the plan strip shows what Doorstep measurably did, beside sourced costs.

From the Nov 9 impact review:
- An address fixed before payment is the prevention lever with the best sources, but it was never
  counted: Insights looked for an address_check of "fixed", which no order ever has. A fix is now
  read from the audit trail: Doorstep's address check failed, and a later check of the address the
  buyer corrected passed. Both rows already exist, so past orders count too.
- "A dispute costs £12, win or lose" was wrong. PayPal waives the standard fee on a claim decided
  for the seller, covered by Seller Protection, settled before it became a claim, or worth less
  than twice the fee. The waiver threshold now comes from the rule file with its source.
- What a failed first delivery costs a seller is a published figure with its source, so the strip
  can say what this month's price is worth in failed deliveries. Nothing is called "saved".
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from app.checkout.fixes import fixed_addresses
from app.ops.economics import load_economics, seller_economics
from app.orders.models import AuditLog, Order
from tests.factories import make_order, make_seller

RULES = Path("rules")


@pytest.fixture
def econ():
    return load_economics(RULES)


def _checked(db, order: Order, *results: bool) -> None:
    for valid in results:
        db.add(
            AuditLog(
                actor="checkout",
                action="checkout.address_checked",
                entity_type="order",
                entity_id=order.id,
                before={},
                after={"valid": valid},
                at=order.created_at,
            )
        )
    db.flush()


def test_EC_02_a_failed_then_passed_check_is_a_fixed_address(db, clock):
    seller = make_seller(db, region="GB")
    fixed, first_time, still_bad, kept, unchecked = (make_order(db, seller) for _ in range(5))
    _checked(db, fixed, False, True)
    _checked(db, first_time, True)
    _checked(db, still_bad, False, False)
    _checked(db, kept, False)  # the buyer kept the address: no passing check follows
    ids = [o.id for o in (fixed, first_time, still_bad, kept, unchecked)]
    assert fixed_addresses(db, ids) == {fixed.id}
    assert fixed_addresses(db, []) == set()


def test_EC_02_the_month_counts_only_this_sellers_fixes(db, clock, econ):
    seller, other = make_seller(db, region="GB"), make_seller(db, region="GB")
    now = clock.now()
    this_month, last_month, theirs = (
        make_order(db, seller),
        make_order(db, seller),
        make_order(db, other),
    )
    for o in (this_month, theirs):
        o.created_at = now
    last_month.created_at = now - timedelta(days=40)
    for o in (this_month, last_month, theirs):
        _checked(db, o, False, True)
    assert seller_economics(db, seller, econ, now).addresses_fixed == 1


def test_EC_02_the_fee_waiver_and_failed_delivery_cost_are_sourced(econ):
    assert econ.dispute_fee_waived_below_multiple == 2
    assert econ.fee_terms_source["GBP"].startswith("https://")
    assert econ.fee_terms_source["USD"].startswith("https://")
    cost = econ.failed_delivery_cost
    assert (cost["GBP"].amount_minor, cost["USD"].amount_minor) == (1160, 1720)
    assert all(c.source.startswith("https://") for c in cost.values())


def test_EC_02_the_view_carries_the_waiver_and_the_break_even(db, clock, econ):
    seller = make_seller(db, region="GB")
    view = seller_economics(db, seller, econ, clock.now())
    assert view.fee_waived_below_minor == 2400  # twice the £12 standard fee
    assert view.failed_delivery_cost_minor == 1160
    assert view.break_even_failures == 0  # a free month needs nothing to pay for itself
    assert view.fee_terms_source.startswith("https://")


@pytest.mark.parametrize(("orders", "failures"), [(101, 1), (200, 1), (250, 2), (1000, 5)])
def test_EC_02_break_even_is_the_price_in_failed_deliveries(db, clock, econ, orders, failures):
    """900 billable orders at 8p is capped at £49, which is 4.2 failed deliveries: 5 cover it."""
    from app.ops.economics import break_even_failures

    assert break_even_failures(econ, orders, "GBP") == failures


def test_EC_02_insights_marks_a_fixed_address(db, clock, session_factory, settings):
    from fastapi.testclient import TestClient

    from app.api.deps import AppDeps, Services
    from app.main import create_app
    from app.proof.receipts import ReceiptSigner
    from app.runtime import ROOT
    from app.security.links import LinkSigner

    links = LinkSigner("e" * 32, clock)  # a test-only signing secret
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
    seller = make_seller(db, region="GB")
    fixed, plain = make_order(db, seller), make_order(db, seller)
    _checked(db, fixed, False, True)
    _checked(db, plain, True)
    db.commit()
    api = TestClient(create_app(deps))
    token = links.issue(seller.id, scope="seller")
    rows = {o["order_id"]: o for o in api.get(f"/api/seller/{token}/insights").json()["orders"]}
    assert rows[fixed.id]["address_check"] == "fixed"
    assert rows[plain.id]["address_check"] == plain.address_check
    econ = api.get(f"/api/seller/{token}/economics").json()
    assert econ["addresses_fixed"] == 1 and econ["break_even_failures"] == 0
