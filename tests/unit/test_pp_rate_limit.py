"""PP-04 / PO-12: a PayPal 429 (RATE_LIMIT_REACHED) is retried with backoff and the same
PayPal-Request-Id; other 4xx still fail at once (found by /paypal:doctor)."""

from __future__ import annotations

import pytest

from app.orders.enums import CaseState, MoneyActionStatus, OrderState
from app.paypal_client.errors import PayPalClientError, PayPalServerError
from app.policy.service import REFUND_JOB, PolicyService, RefundJobHandler
from app.worker.handlers import ADD_TRACKING, AddTrackingHandler
from app.worker.models import Job
from app.worker.runner import JobRunner, PermanentError, enqueue, is_permanent
from tests.factories import make_case, make_order, make_seller, make_shipment, money_actions
from tests.helpers import engine_result, facts_for, money, run_jobs


def _rate_limited() -> PayPalClientError:
    return PayPalClientError(429, "RATE_LIMIT_REACHED")


@pytest.mark.parametrize(
    ("error", "permanent"),
    [
        (_rate_limited(), False),
        (PayPalClientError(422, "UNPROCESSABLE_ENTITY"), True),
        (PayPalClientError(400, "INVALID_REQUEST"), True),
        (PayPalServerError(503), False),
        (PermanentError("no order"), True),
        (RuntimeError("boom"), False),
    ],
)
def test_PP_04_only_rate_limits_among_4xx_are_retryable(error, permanent):
    assert is_permanent(error) is permanent


def test_PO_12_rate_limited_refund_is_retried_with_the_same_key(
    db, session_factory, fake_paypal, rulebook, clock, settings
):
    order = make_order(db, make_seller(db), amount_minor=3000)
    fake_paypal.add_capture(order.paypal_order_id, order.capture_id, money(3000))
    case = make_case(db, order)
    db.commit()
    PolicyService(db, rulebook, clock).decide(case, engine_result(), facts_for(order))
    fake_paypal.fail_next_refunds(_rate_limited(), _rate_limited())
    db.commit()
    run_jobs(
        session_factory,
        {REFUND_JOB: RefundJobHandler(fake_paypal, rulebook, clock)},
        clock,
        settings,
        rounds=4,
        advance_s=10_000,
    )
    db.expire_all()
    assert len(fake_paypal.refund_calls) == 3
    assert len({rid for _, _, rid in fake_paypal.refund_calls}) == 1  # never a second refund
    assert fake_paypal.total_refunded(order.capture_id) == 3000
    [ma] = money_actions(db, case_id=case.id)
    assert ma.status == MoneyActionStatus.COMPLETED
    db.refresh(case)
    assert case.state == CaseState.SETTLED


def test_PP_04_rate_limited_tracking_is_retried_not_failed(
    db, session_factory, fake_paypal, clock, settings
):
    order = make_order(
        db, make_seller(db), state=OrderState.SHIPPED, paypal_order_id="PPO-R", capture_id="CAP-R"
    )
    shipment = make_shipment(db, order, tracking_number="SHIPPO_TRANSIT")
    job = enqueue(db, ADD_TRACKING, {"shipment_id": shipment.id}, clock=clock)
    db.commit()
    fake_paypal.tracking_failures.append(_rate_limited())
    runner = JobRunner(
        session_factory, {ADD_TRACKING: AddTrackingHandler(fake_paypal, clock)}, clock, settings
    )
    runner.run_due()
    db.expire_all()
    assert db.get(Job, job.id).status != "failed"  # type: ignore[union-attr]
    clock.advance(10_000)
    runner.run_due()
    db.expire_all()
    assert db.get(Job, job.id).status == "done"  # type: ignore[union-attr]
    assert len(fake_paypal.tracking_calls) == 2
