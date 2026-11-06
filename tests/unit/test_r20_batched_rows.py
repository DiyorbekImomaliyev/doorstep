"""R20: the Control Room rows load in a fixed number of queries, whatever the number of orders.

The per-order version ran about ten queries per row (an N+1 pattern): 158 queries and 317 ms for a
16-order shop locally, growing with every order. The batch version must return exactly what the
per-order version returned, row for row, so these tests compare the two on the full demo shop.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import event, select

from app.api.views import evidence_completeness, evidence_scores, order_row, order_rows
from app.demo.seed import seed_scenario
from app.orders.models import Order
from app.proof.receipts import ReceiptSigner


@pytest.fixture
def shop(db, rulebook, clock, settings) -> list[Order]:
    summary = seed_scenario(
        db, rulebook=rulebook, clock=clock, settings=settings, signer=ReceiptSigner.generate()
    )
    db.commit()
    orders = list(db.scalars(select(Order).where(Order.seller_id == summary.seller_id)))
    assert len(orders) >= 8  # a shop with every kind of order, not a toy
    return orders


@contextmanager
def _statements(db) -> Iterator[list[str]]:
    seen: list[str] = []

    def count(conn, cursor, statement, *_):
        seen.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", count)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", count)


def test_R20_batched_rows_equal_the_rows_built_one_order_at_a_time(db, shop):
    batched = order_rows(db, shop)
    single = [order_row(db, o) for o in shop]
    assert batched == single
    # The shop really covers the row's branches: cases, refunds, evidence and none of these.
    assert any(r.case_id for r in batched) and any(not r.case_id for r in batched)
    assert any(r.refund_status for r in batched)
    assert len({r.evidence.score for r in batched}) > 1


def test_R20_rows_never_borrow_another_orders_data(db, shop):
    rows = {r.order_id: r for r in order_rows(db, shop)}
    for order in shop:
        assert rows[order.id] == order_rows(db, [order])[0]


def _fresh(db, orders: list[Order]) -> list[Order]:
    """The orders loaded in one query, as the endpoint loads them, with nothing cached."""
    db.expire_all()
    ids = [o.id for o in orders]
    by_id = {o.id: o for o in db.scalars(select(Order).where(Order.id.in_(ids)))}
    return [by_id[i] for i in ids]


def test_R20_the_query_count_does_not_grow_with_the_orders(db, shop):
    first = _fresh(db, shop[:1])
    with _statements(db) as one:
        order_rows(db, first)
    everything = _fresh(db, shop)
    with _statements(db) as many:
        order_rows(db, everything)
    assert len(many) <= len(one) + 3  # + the shop's history, once, when a row shows a risk
    assert len(many) < len(shop)


def test_R20_evidence_scores_match_one_at_a_time(db, shop):
    scores = evidence_scores(db, shop)
    assert scores == {o.id: evidence_completeness(db, o) for o in shop}


def test_R20_an_empty_list_makes_no_rows(db):
    assert order_rows(db, []) == []
    assert evidence_scores(db, []) == {}


def test_R20_orders_made_at_the_same_moment_keep_one_order(
    db, shop, session_factory, settings, clock
):
    """40 of 320 orders in a merged demo shop shared a timestamp; the grid must not reshuffle."""
    from app.api.deps import Services
    from app.security.links import LinkSigner
    from tests.fakes_journey import RecordingPayPal
    from tests.unit.test_api_journey import SECRET, _client

    paypal = RecordingPayPal()
    services = Services(
        links=LinkSigner(SECRET, clock),
        receipts=ReceiptSigner.generate(),
        orders=paypal,
        approved=paypal,
    )
    for order in shop:
        order.created_at = shop[0].created_at  # every order made at the same moment
    db.commit()
    api = _client(session_factory, settings, clock, services)
    token = services.links.issue(shop[0].seller_id, scope="seller")
    ids = [row["order_id"] for row in api.get(f"/api/seller/{token}/orders").json()]
    assert len(ids) == len(shop)
    assert ids == sorted(ids)
