"""DI-07: a dispute the sweep finds at PayPal shows PayPal's reason, stage, reply-by date and amount
on the Disputes page, as a server PayPal cannot reach gets no webhook to read them from.
DI-06 (extended): an order with no carrier scan yet is read from the carrier before its case opens,
so the case and the Disputes page see the carrier's delivered scan."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import select

from app.cases.dispute_intake import READ_ACTION, latest_read, record_read, take_dispute
from app.orders.enums import CaseState, OrderState
from app.orders.models import AuditLog, Decision, Dispute, TrackingEvent
from app.paypal_client.reads import list_open_disputes_since
from app.worker.main import sweep_disputes
from tests.factories import make_order, make_seller, make_shipment
from tests.unit.test_di_dispute_intake import (
    NOT_RECEIVED,
    _Carrier,
    _cases,
    _ListingPayPal,
    _refresh,
    opener,  # noqa: F401 - fixture
    signer,  # noqa: F401 - fixture
)
from tests.unit.test_ds_desk import _desk, _disputed, _hook, api  # noqa: F401 - fixture

FACTS = {
    "reason": NOT_RECEIVED,
    "status": "WAITING_FOR_SELLER_RESPONSE",
    "dispute_life_cycle_stage": "INQUIRY",
    "dispute_channel": "INTERNAL",
    "seller_response_due_date": "2026-11-22T10:00:00.000Z",
    "dispute_amount": {"currency_code": "GBP", "value": "60.00"},
}


def test_DI_07_the_list_keeps_the_disputes_own_facts_and_no_buyer_data(clock):
    item = {
        "dispute_id": "PP-R-7",
        "status": "WAITING_FOR_SELLER_RESPONSE",
        "reason": NOT_RECEIVED,
        "dispute_amount": {"currency_code": "GBP", "value": "60.00"},
        "disputed_transactions": [{"buyer_transaction_id": "BUYER-TX"}],
    }
    detail = {
        **FACTS,
        "dispute_id": "PP-R-7",
        "disputed_transactions": [
            {"seller_transaction_id": "CAP-7", "buyer": {"name": "John Doe"}}
        ],
        "messages": [{"posted_by": "BUYER", "content": "buyer@example.com"}],
    }
    facts: dict[str, dict] = {}
    [found] = list_open_disputes_since(
        _ListingPayPal([item], {"PP-R-7": detail}),  # type: ignore[arg-type]
        clock.now(),
        facts,
    )
    assert found.capture_ids == ["CAP-7"] and facts == {"PP-R-7": FACTS}
    listed_only = {**item, "disputed_transactions": [{"seller_transaction_id": "CAP-8"}]}
    facts.clear()
    list_open_disputes_since(_ListingPayPal([listed_only]), clock.now(), facts)  # type: ignore[arg-type]
    assert facts["PP-R-7"] == {
        "reason": NOT_RECEIVED,
        "status": "WAITING_FOR_SELLER_RESPONSE",
        "dispute_amount": {"currency_code": "GBP", "value": "60.00"},
    }


def test_DI_07_a_read_is_kept_once_and_again_only_when_it_changes(db, clock):
    order = make_order(db, make_seller(db))
    db.add(Dispute(order_id=order.id, paypal_dispute_id="PP-R-8", status="open"))
    db.flush()
    assert not record_read(db, "PP-R-UNKNOWN", FACTS, clock=clock)
    assert not record_read(db, "PP-R-8", {}, clock=clock)
    assert record_read(db, "PP-R-8", FACTS, clock=clock)
    assert not record_read(db, "PP-R-8", dict(FACTS), clock=clock)
    clock.advance(60)
    moved = {**FACTS, "status": "WAITING_FOR_BUYER_RESPONSE"}
    assert record_read(db, "PP-R-8", moved, clock=clock)
    row = db.scalars(select(Dispute)).one()
    assert latest_read(db, row.id) == moved
    assert latest_read(db, "no-such-dispute") is None
    rows = db.scalars(select(AuditLog).where(AuditLog.action == READ_ACTION)).all()
    assert [r.actor for r in rows] == ["paypal", "paypal"]


def test_DI_07_the_sweep_keeps_what_paypal_says(db, session_factory, clock, settings, opener):  # noqa: F811
    order = make_order(db, make_seller(db), state=OrderState.CONFIRMED)
    capture = order.capture_id
    db.commit()
    item = {
        **FACTS,
        "dispute_id": "PP-R-9",
        "disputed_transactions": [{"seller_transaction_id": capture}],
    }
    rt = SimpleNamespace(
        session_factory=session_factory,
        clock=clock,
        settings=settings,
        paypal=_ListingPayPal([item]),
    )
    assert sweep_disputes(rt, opener) == 1  # type: ignore[arg-type]
    assert sweep_disputes(rt, opener) == 1  # type: ignore[arg-type]
    db.expire_all()
    row = db.scalars(select(Dispute)).one()
    assert latest_read(db, row.id) == FACTS
    assert len(db.scalars(select(AuditLog).where(AuditLog.action == READ_ACTION)).all()) == 1


def test_DI_07_the_desk_shows_a_read_and_takes_facts_in_time_order(db, clock, api):  # noqa: F811
    seller = make_seller(db)
    _disputed(db, seller, dispute_id="PP-R-10", amount_minor=6000, currency="GBP")
    _disputed(db, seller, dispute_id="PP-R-11", amount_minor=6000, currency="GBP")
    assert record_read(db, "PP-R-10", FACTS, clock=clock)
    _hook(db, clock, "CUSTOMER.DISPUTE.CREATED", {**FACTS, "dispute_id": "PP-R-11"}, minutes=-5)
    assert record_read(db, "PP-R-11", {**FACTS, "status": "UNDER_REVIEW"}, clock=clock)
    db.commit()
    rows = _desk(api, seller, clock)
    read_only = rows["PP-R-10"]
    assert read_only["paypal"]["reason"] == NOT_RECEIVED
    assert read_only["paypal"]["stage"] == "INQUIRY" and read_only["paypal"]["events"] == 0
    assert read_only["paypal"]["response_due_at"].startswith("2026-11-22T10:00")
    assert read_only["amount_minor"] == 6000 and read_only["amount_from"] == "paypal"
    later_read = rows["PP-R-11"]["paypal"]
    assert later_read["status"] == "UNDER_REVIEW" and later_read["events"] == 1


def test_DI_06_a_confirmed_order_with_no_scan_is_read_from_the_carrier_first(
    db,
    rulebook,
    clock,
    opener,  # noqa: F811
):
    order = make_order(db, make_seller(db), state=OrderState.CONFIRMED)
    make_shipment(db, order, tracking_number="SHIPPO_DELIVERED")
    carrier = _Carrier("DELIVERED")
    take_dispute(
        db,
        order,
        "PP-D-12",
        reason=NOT_RECEIVED,
        clock=clock,
        open_case=opener,
        refresh=_refresh(carrier, clock),
    )
    assert carrier.reads == [("shippo", "SHIPPO_DELIVERED")]
    [event] = db.scalars(select(TrackingEvent)).all()
    assert (event.status, event.applied) == ("DELIVERED", False)  # never moves the order back
    [case] = _cases(db, order)
    assert case.state == CaseState.DISPUTE_EVIDENCE.value
    decision = db.scalars(select(Decision).where(Decision.case_id == case.id)).one()
    assert "delivered_scan" in decision.signals["facts"]["proofs"]  # the case saw the scan


def test_DI_06_an_order_the_carrier_has_scanned_is_not_read_again(
    db,
    rulebook,
    clock,
    opener,  # noqa: F811
):
    order = make_order(db, make_seller(db), state=OrderState.CONFIRMED)
    shipment = make_shipment(db, order, tracking_number="SHIPPO_DELIVERED")
    db.add(
        TrackingEvent(
            shipment_id=shipment.id,
            status="DELIVERED",
            occurred_at=clock.now() - timedelta(days=1),
            raw={},
            applied=True,
        )
    )
    db.flush()
    carrier = _Carrier("DELIVERED")
    take_dispute(
        db,
        order,
        "PP-D-13",
        reason=NOT_RECEIVED,
        clock=clock,
        open_case=opener,
        refresh=_refresh(carrier, clock),
    )
    assert carrier.reads == [] and len(_cases(db, order)) == 1
