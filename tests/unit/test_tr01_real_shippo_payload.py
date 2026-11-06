"""TR-01 with Shippo's real test-mode payload (tests/fixtures/shippo/track_SHIPPO_DELIVERED.json).

Found live on Nov 6: the first real Shippo event on a live order carried "substatus": null, the
buyer page's history read it as a dict, and the page could not load. Every view that shows a
carrier event must accept what Shippo actually sends."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.api.views import buyer_view, order_row, timeline
from app.orders.enums import OrderState
from app.shipping.events import handle_shippo_track_updated
from app.worker.models import WebhookInbox
from tests.factories import make_order, make_seller, make_shipment

REAL = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "shippo" / "track_SHIPPO_DELIVERED.json").read_text()
)


def _deliver(db, data: dict, event_id: str = "evt-real-1"):
    order = make_order(db, make_seller(db), state=OrderState.SHIPPED)
    make_shipment(db, order, tracking_number="SHIPPO_DELIVERED", carrier="shippo")
    row = WebhookInbox(
        event_id=event_id,
        source="shippo",
        event_type="track_updated",
        payload={"event": "track_updated", "data": data},
    )
    db.add(row)
    db.flush()
    handle_shippo_track_updated(db, row)
    db.flush()
    return order


def test_TR_01_10_the_real_shippo_payload_has_a_null_substatus():
    assert REAL["tracking_status"]["status"] == "DELIVERED"
    assert REAL["tracking_status"].get("substatus") is None


def test_TR_01_11_a_real_shippo_event_moves_the_order_and_every_view_renders(db):
    order = _deliver(db, REAL)
    assert order.state == OrderState.DELIVERED
    view = buyer_view(db, order)
    assert view.status == "Marked as delivered" and view.can_report
    assert any(i.title == "Carrier: delivered" for i in view.timeline)
    assert order_row(db, order).status == "Marked as delivered"
    assert any(i.kind == "carrier" for i in timeline(db, order, for_buyer=False))


@pytest.mark.parametrize(
    "shape",
    [
        {"substatus": None},
        {"substatus": "not-a-dict"},
        {"substatus": {"code": "no_access"}},
        {"substatus": {}},
    ],
)
def test_TR_01_12_carrier_history_accepts_any_substatus_shape(db, shape):
    data = copy.deepcopy(REAL)
    data["tracking_status"].update(shape)
    order = _deliver(db, data, event_id=f"evt-{shape!r}")
    items = [i for i in buyer_view(db, order).timeline if i.kind == "carrier"]
    assert items and items[0].detail in ("", "no access")
