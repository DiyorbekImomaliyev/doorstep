"""AP-05 on real Postgres: active rules come back in kind order, currency included.

On SQLite the unique index (seller_id, failure_point, currency) already returns rows in this
order, so the unit suite cannot see a missing currency sort. This test turns index scans off so
Postgres must scan and sort: only the ORDER BY can give the order then.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.cases.engine import FailurePoint
from app.orders.models import SellerRule
from app.policy.autopilot import ON, active_rules
from tests.factories import make_seller
from tests.fakes import FakeClock

pytestmark = pytest.mark.pg

LOST = FailurePoint.CARRIER_LOST
DAMAGED = FailurePoint.DAMAGED_IN_TRANSIT


def test_AP_05_active_rules_sort_by_currency_within_a_kind_on_postgres(pg_session_factory):
    clock = FakeClock()
    with pg_session_factory() as s:
        seller = make_seller(s)
        for fp, cur in [(DAMAGED, "USD"), (LOST, "USD"), (DAMAGED, "GBP"), (LOST, "GBP")]:
            s.add(
                SellerRule(
                    seller_id=seller.id,
                    failure_point=fp.value,
                    currency=cur,
                    max_amount_minor=6000,
                    status=ON,
                    updated_at=clock.now(),
                )
            )
            s.flush()  # inserted USD before GBP on purpose
        s.commit()
        s.execute(text("SET LOCAL enable_indexscan = off"))
        s.execute(text("SET LOCAL enable_bitmapscan = off"))
        s.execute(text("SET LOCAL enable_indexonlyscan = off"))
        kinds = [(r.failure_point, r.currency) for r in active_rules(s, seller.id)]
    assert kinds == [
        (LOST.value, "GBP"),
        (LOST.value, "USD"),
        (DAMAGED.value, "GBP"),
        (DAMAGED.value, "USD"),
    ]
