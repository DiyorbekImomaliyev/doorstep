"""CK-08: a buyer who approves in PayPal but never comes back is still served.

Found by the Nov 7 review: capture happened only when the buyer's browser returned from PayPal,
so a closed tab left an approved order uncaptured. PayPal sends CHECKOUT.ORDER.APPROVED (the live
sandbox run recorded it with the PayPal order id as the resource id). Now:

- The event schedules a follow-up job a few minutes later, so the buyer who does come back keeps
  the address step; the job does nothing if they did.
- A worker sweep schedules the same job for orders whose event never arrived.
- The job asks PayPal for the order. Only an APPROVED order goes on: Doorstep checks the address
  PayPal returned and captures through the outbox if it is deliverable. An address that needs the
  buyer is recorded and waits, as before; nothing is captured on it.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.checkout.address import AddressChecker
from app.checkout.ports import ApprovedOrder
from app.orders.enums import OrderState
from app.orders.models import AuditLog, Order
from app.paypal_client.approved import parse_approved_order
from app.paypal_client.errors import PayPalServerError
from app.worker.approved import (
    APPROVAL_GRACE,
    CompleteApprovedHandler,
    sweep_unreturned_approvals,
)
from app.worker.handlers import CAPTURE_ORDER
from app.worker.kinds import COMPLETE_APPROVED
from app.worker.models import Job
from app.worker.paypal_events import paypal_inbox_handlers
from tests.factories import make_order, make_seller
from tests.fakes_journey import SAM_FIXED, SAM_NO_FLAT, RecordingPayPal
from tests.unit.test_wh_paypal_events import _row


@pytest.fixture
def paypal() -> RecordingPayPal:
    return RecordingPayPal()


def _created(db, clock, *, minutes_ago: int = 0) -> Order:
    order = make_order(
        db,
        make_seller(db),
        state=OrderState.CREATED,
        capture_id=None,
        capture_status=None,
        paypal_order_id=f"PPO-CK08-{minutes_ago}",
    )
    order.created_at = clock.now() - timedelta(minutes=minutes_ago)
    db.flush()
    return order


def _jobs(db, kind: str) -> list[Job]:
    return list(db.scalars(select(Job).where(Job.kind == kind)))


def _approved_event(db, rulebook, clock, paypal_order_id: str) -> None:
    resource = {"id": paypal_order_id, "status": "APPROVED"}
    handler = paypal_inbox_handlers(rulebook, clock)[("paypal", "CHECKOUT.ORDER.APPROVED")]
    handler(db, _row("CHECKOUT.ORDER.APPROVED", resource))
    db.flush()


def _run(db, paypal, rulebook, clock, settings, order: Order) -> None:
    handler = CompleteApprovedHandler(paypal, paypal, AddressChecker(), rulebook, clock, settings)
    [job] = [j for j in _jobs(db, COMPLETE_APPROVED) if j.payload == {"order_id": order.id}][:1]
    handler.run(db, job)
    db.flush()


# ---------------------------------------------------------------- the PayPal event


def test_CK_08_the_approved_event_schedules_a_follow_up_after_a_grace_period(db, rulebook, clock):
    order = _created(db, clock)
    _approved_event(db, rulebook, clock, order.paypal_order_id)
    [job] = _jobs(db, COMPLETE_APPROVED)
    assert job.payload == {"order_id": order.id}
    assert job.run_at == clock.now() + APPROVAL_GRACE  # the returning buyer goes first
    assert _jobs(db, CAPTURE_ORDER) == []


def test_CK_08_a_repeated_event_schedules_one_follow_up(db, rulebook, clock):
    order = _created(db, clock)
    for _ in range(2):
        _approved_event(db, rulebook, clock, order.paypal_order_id)
    assert len(_jobs(db, COMPLETE_APPROVED)) == 1


def test_CK_08_an_event_for_an_order_doorstep_does_not_know_is_skipped(db, rulebook, clock):
    _approved_event(db, rulebook, clock, "PPO-SOMEONE-ELSE")
    assert _jobs(db, COMPLETE_APPROVED) == []


def test_CK_08_an_event_after_the_buyer_returned_schedules_nothing(db, rulebook, clock):
    order = _created(db, clock)
    order.address_check = "valid"  # the buyer came back and the address was checked
    db.flush()
    _approved_event(db, rulebook, clock, order.paypal_order_id)
    assert _jobs(db, COMPLETE_APPROVED) == []


# ---------------------------------------------------------------- the follow-up job


def test_CK_08_a_buyer_who_never_returned_is_captured_on_a_good_address(
    db, paypal, rulebook, clock, settings
):
    order = _created(db, clock)
    paypal.approve(order.paypal_order_id, SAM_FIXED)
    _approved_event(db, rulebook, clock, order.paypal_order_id)
    _run(db, paypal, rulebook, clock, settings, order)
    assert order.address_check == "valid"
    assert [j.payload for j in _jobs(db, CAPTURE_ORDER)] == [{"order_id": order.id}]
    actions = db.scalars(select(AuditLog.action).where(AuditLog.entity_id == order.id)).all()
    assert "checkout.completed_without_buyer" in actions


def test_CK_08_an_address_that_needs_the_buyer_waits_and_is_not_captured(
    db, paypal, rulebook, clock, settings
):
    order = _created(db, clock)
    paypal.approve(order.paypal_order_id, SAM_NO_FLAT)
    _approved_event(db, rulebook, clock, order.paypal_order_id)
    _run(db, paypal, rulebook, clock, settings, order)
    assert order.address_check == "invalid"
    assert _jobs(db, CAPTURE_ORDER) == []


def test_CK_08_the_job_does_nothing_if_the_buyer_came_back_meanwhile(
    db, paypal, rulebook, clock, settings
):
    order = _created(db, clock)
    paypal.approve(order.paypal_order_id, SAM_FIXED)
    _approved_event(db, rulebook, clock, order.paypal_order_id)
    order.address_check = "invalid"  # the buyer returned and is fixing the flat number
    db.flush()
    _run(db, paypal, rulebook, clock, settings, order)
    assert order.address_check == "invalid"
    assert _jobs(db, CAPTURE_ORDER) == []


class _NotApproved(RecordingPayPal):
    def __init__(self, status: str | None) -> None:
        super().__init__()
        self.status = status

    def get_approved(self, paypal_order_id: str) -> ApprovedOrder:
        if self.status is None:  # PayPal could not be read, or has no address yet
            raise PayPalServerError(None, "order has no shipping address")
        return ApprovedOrder(
            paypal_order_id=paypal_order_id, payer_id="P", address=SAM_FIXED, status=self.status
        )


@pytest.mark.parametrize("status", ["CREATED", "VOIDED", "PAYER_ACTION_REQUIRED", None])
def test_CK_08_only_an_order_paypal_shows_as_approved_goes_on(
    db, rulebook, clock, settings, status
):
    order = _created(db, clock, minutes_ago=30)
    sweep_unreturned_approvals(db, clock)
    _run(db, _NotApproved(status), rulebook, clock, settings, order)
    assert order.address_check == "unknown"
    assert _jobs(db, CAPTURE_ORDER) == []


def test_CK_08_the_approved_status_is_read_from_paypal():
    body = {
        "id": "PPO-1",
        "status": "APPROVED",
        "payer": {"payer_id": "P-1"},
        "purchase_units": [
            {"shipping": {"address": {"address_line_1": "1 High St", "country_code": "GB"}}}
        ],
    }
    assert parse_approved_order(body).status == "APPROVED"
    assert parse_approved_order(body | {"status": "CREATED"}).status == "CREATED"


# ---------------------------------------------------------------- the sweep


def test_CK_08_the_sweep_schedules_only_unreturned_orders_old_enough(db, clock):
    fresh = _created(db, clock, minutes_ago=2)  # the buyer may still be at PayPal
    stale = _created(db, clock, minutes_ago=30)
    ancient = _created(db, clock, minutes_ago=60 * 24 * 4)  # older than the sweep looks back
    returned = _created(db, clock, minutes_ago=31)
    returned.address_check = "valid"
    db.flush()
    assert sweep_unreturned_approvals(db, clock) == 1
    scheduled = {j.payload["order_id"] for j in _jobs(db, COMPLETE_APPROVED)}
    assert scheduled == {stale.id}
    assert not {fresh.id, ancient.id, returned.id} & scheduled


def test_CK_08_the_sweep_schedules_each_order_once(db, clock):
    _created(db, clock, minutes_ago=30)
    assert sweep_unreturned_approvals(db, clock) == 1
    assert sweep_unreturned_approvals(db, clock) == 0
    assert len(_jobs(db, COMPLETE_APPROVED)) == 1


def test_CK_08_the_worker_sweep_asks_paypal_only_when_paypal_is_configured(
    db, session_factory, clock, settings
):
    from types import SimpleNamespace

    from app.worker.main import sweep, sweep_approvals

    order = _created(db, clock, minutes_ago=30)
    db.commit()
    rt = SimpleNamespace(session_factory=session_factory, clock=clock, settings=settings)
    assert sweep_approvals(SimpleNamespace(**vars(rt), paypal=None)) == 0
    sweep(SimpleNamespace(**vars(rt), paypal=object()))  # the worker's own sweep
    db.expire_all()
    [job] = _jobs(db, COMPLETE_APPROVED)
    assert job.payload == {"order_id": order.id}
