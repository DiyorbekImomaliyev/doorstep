"""SM — Order and case state machines (SPEC section 8 tables, ADR-001, ADR-005)."""

from __future__ import annotations

import pytest

from app.orders.enums import CaseState, Fix, MoneyActionStatus, OrderState
from app.orders.models import Case, Order, TrackingEvent
from app.orders.state import (
    CarrierEventOutcome,
    CarrierStatus,
    CaseAlreadyOpen,
    IllegalTransition,
    SettlementGuardError,
    apply_carrier_event,
    open_case,
    transition_case,
    transition_order,
)
from app.shipping.events import handle_shippo_track_updated
from app.worker.inbox import InboxProcessor
from app.worker.models import WebhookInbox
from tests.factories import (
    add_approval,
    add_decision,
    add_money_action,
    audit_rows,
    make_case,
    make_order,
    make_seller,
    make_shipment,
)
from tests.transition_tables import CASE_ALLOWED, ORDER_ALLOWED, pairs


def _order(db, state: OrderState) -> Order:
    order = make_order(db, make_seller(db), state=state)
    db.commit()
    return order


def _case(db, state: CaseState) -> Case:
    order = make_order(db, make_seller(db))
    case = make_case(db, order, state=state)
    db.commit()
    return case


def _make_settleable(db, case: Case) -> None:
    decision = add_decision(db, case, fix=Fix.RESHIP)
    add_approval(db, case, decision)
    db.commit()


# ---------------------------------------------------------------- SM-01 / SM-02 (orders)


@pytest.mark.parametrize(("frm", "to"), pairs(ORDER_ALLOWED, allowed=True))
def test_SM_01_allowed_order_transition_succeeds(db, frm, to):
    order = _order(db, frm)
    transition_order(db, order, to, actor="test")
    assert order.state == to


@pytest.mark.parametrize(("frm", "to"), pairs(ORDER_ALLOWED, allowed=False))
def test_SM_02_disallowed_order_transition_raises_and_changes_nothing(db, frm, to):
    order = _order(db, frm)
    with pytest.raises(IllegalTransition):
        transition_order(db, order, to, actor="test")
    assert order.state == frm
    assert audit_rows(db, order.id) == []


# ---------------------------------------------------------------- SM-05 / SM-02 (cases)


@pytest.mark.parametrize(("frm", "to"), pairs(CASE_ALLOWED, allowed=True))
def test_SM_05_allowed_case_transition_succeeds(db, frm, to):
    case = _case(db, frm)
    if to == CaseState.SETTLED:
        _make_settleable(db, case)
    transition_case(db, case, to, actor="test")
    assert case.state == to


@pytest.mark.parametrize(("frm", "to"), pairs(CASE_ALLOWED, allowed=False))
def test_SM_02_disallowed_case_transition_raises_and_changes_nothing(db, frm, to):
    case = _case(db, frm)
    _make_settleable(db, case)  # so SETTLED is refused only for being illegal
    with pytest.raises(IllegalTransition):
        transition_case(db, case, to, actor="test")
    assert case.state == frm
    assert audit_rows(db, case.id) == []


# ---------------------------------------------------------------- SM-03


def test_SM_03_order_transition_writes_audit_row(db):
    order = _order(db, OrderState.PAID)
    transition_order(db, order, OrderState.SHIPPED, actor="worker", reason="label bought")
    db.commit()
    rows = audit_rows(db, order.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.entity_type == "order"
    assert row.actor == "worker"
    assert row.before == {"state": "PAID"}
    assert row.after == {"state": "SHIPPED"}


def test_SM_03_case_transition_writes_audit_row(db):
    case = _case(db, CaseState.OPEN)
    transition_case(db, case, CaseState.GATHERING_EVIDENCE, actor="engine")
    db.commit()
    rows = [r for r in audit_rows(db, case.id) if r.entity_type == "case"]
    assert len(rows) == 1
    assert rows[0].before == {"state": "OPEN"}
    assert rows[0].after == {"state": "GATHERING_EVIDENCE"}


def test_SM_03_rollback_discards_transition_and_audit_row(db):
    order = _order(db, OrderState.PAID)
    order_id = order.id
    transition_order(db, order, OrderState.SHIPPED, actor="worker")
    db.rollback()
    db.expire_all()
    assert db.get(Order, order_id).state == OrderState.PAID
    assert audit_rows(db, order_id) == []


# ---------------------------------------------------------------- SM-06


def test_SM_06_settle_without_decision_raises(db):
    case = _case(db, CaseState.AWAITING_SELLER)
    with pytest.raises(SettlementGuardError):
        transition_case(db, case, CaseState.SETTLED, actor="test")
    assert case.state == CaseState.AWAITING_SELLER


def test_SM_06_settle_without_approval_raises(db):
    case = _case(db, CaseState.AWAITING_SELLER)
    add_decision(db, case, fix=Fix.RESHIP)
    db.commit()
    with pytest.raises(SettlementGuardError):
        transition_case(db, case, CaseState.SETTLED, actor="test")


def test_SM_06_settle_refund_without_completed_money_action_raises(db):
    case = _case(db, CaseState.AUTO_REFUND)
    decision = add_decision(db, case, fix=Fix.REFUND, route="AUTO_REFUND")
    add_approval(db, case, decision)
    add_money_action(db, case, decision, status=MoneyActionStatus.FAILED)
    db.commit()
    with pytest.raises(SettlementGuardError):
        transition_case(db, case, CaseState.SETTLED, actor="test")


def test_SM_06_settle_refund_with_completed_money_action_succeeds(db):
    case = _case(db, CaseState.AUTO_REFUND)
    decision = add_decision(db, case, fix=Fix.REFUND, route="AUTO_REFUND")
    add_approval(db, case, decision)
    add_money_action(db, case, decision, status=MoneyActionStatus.COMPLETED)
    db.commit()
    transition_case(db, case, CaseState.SETTLED, actor="test")
    assert case.state == CaseState.SETTLED


# ---------------------------------------------------------------- SM-07


def test_SM_07_opening_case_moves_order_to_case_open(db):
    order = _order(db, OrderState.DELIVERED)
    case = open_case(db, order, reported_by="buyer", reason="not_received", actor="buyer")
    assert case.state == CaseState.OPEN
    assert case.order_id == order.id
    assert order.state == OrderState.CASE_OPEN


def test_SM_07_second_open_case_on_same_order_raises(db):
    order = _order(db, OrderState.DELIVERED)
    open_case(db, order, reported_by="buyer", reason="not_received", actor="buyer")
    with pytest.raises(CaseAlreadyOpen):
        open_case(db, order, reported_by="buyer", reason="damaged", actor="buyer")


@pytest.mark.parametrize(
    ("frm", "to"),
    [
        (CaseState.AWAITING_SELLER, CaseState.CLOSED),
        (CaseState.AWAITING_SELLER, CaseState.SETTLED),
        (CaseState.HOLD_FOR_REVIEW, CaseState.CLOSED),
        (CaseState.DISPUTE_EVIDENCE, CaseState.SETTLED),
    ],
)
def test_SM_07_case_settled_or_closed_moves_order_to_resolved(db, frm, to):
    case = _case(db, frm)
    if to == CaseState.SETTLED:
        _make_settleable(db, case)
    transition_case(db, case, to, actor="test")
    assert db.get(Order, case.order_id).state == OrderState.RESOLVED


# ---------------------------------------------------------------- SM-09


def test_SM_09_repeat_carrier_event_is_noop(db):
    order = _order(db, OrderState.IN_TRANSIT)
    outcome = apply_carrier_event(db, order, CarrierStatus.TRANSIT)
    assert outcome == CarrierEventOutcome.NOOP
    assert order.state == OrderState.IN_TRANSIT
    assert all(r.after.get("state") != "IN_TRANSIT" for r in audit_rows(db, order.id))


def test_SM_09_backward_carrier_event_recorded_without_transition(db):
    order = _order(db, OrderState.DELIVERED)
    outcome = apply_carrier_event(db, order, CarrierStatus.TRANSIT)
    assert outcome == CarrierEventOutcome.IGNORED_OUT_OF_ORDER
    assert order.state == OrderState.DELIVERED


def test_SM_09_forward_carrier_event_applies(db):
    order = _order(db, OrderState.IN_TRANSIT)
    assert apply_carrier_event(db, order, CarrierStatus.DELIVERED) == CarrierEventOutcome.APPLIED
    assert order.state == OrderState.DELIVERED


def test_SM_09_out_of_order_shippo_event_does_not_fail_inbox_row(
    db, session_factory, clock, settings
):
    order = make_order(db, make_seller(db), state=OrderState.DELIVERED)
    shipment = make_shipment(db, order, tracking_number="SHIPPO_TRANSIT")
    row = WebhookInbox(
        event_id="shippo-late-transit",
        source="shippo",
        event_type="track_updated",
        payload={
            "event": "track_updated",
            "data": {
                "carrier": "shippo",
                "tracking_number": "SHIPPO_TRANSIT",
                "tracking_status": {"status": "TRANSIT", "status_date": "2026-11-03T10:00:00Z"},
            },
        },
    )
    db.add(row)
    db.commit()
    InboxProcessor(
        session_factory, {("shippo", "track_updated"): handle_shippo_track_updated}, clock, settings
    ).run_pending()
    db.expire_all()
    assert db.get(WebhookInbox, row.id).status == "processed"
    assert db.get(Order, order.id).state == OrderState.DELIVERED
    events = db.query(TrackingEvent).filter_by(shipment_id=shipment.id).all()
    assert len(events) == 1 and events[0].applied is False
