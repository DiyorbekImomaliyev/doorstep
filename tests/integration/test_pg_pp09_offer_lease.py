"""PP-09 on real Postgres: the offer job commits its attempt marker and lease mid-run, which
releases its row locks, so only the lease keeps a second worker off the job. These tests run two
job runners against one Postgres database. Skipped unless a Postgres test URL is set; CI sets
REQUIRE_PG=1 so they always run there."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.config import Settings
from app.money import Money
from app.orders.enums import CaseState
from app.orders.models import Dispute, MoneyAction
from app.paypal_client.errors import PayPalClientError, PayPalServerError
from app.paypal_client.types import DisputeDetail
from app.policy.offers import (
    ATTEMPTED,
    DISPUTE_OFFER_JOB,
    OFFER_LEASE_S,
    OFFERED,
    OfferJobHandler,
    queue_dispute_offer,
)
from app.policy.rules import load_rules
from app.policy.service import PolicyService
from app.worker.models import Job
from app.worker.runner import JobRunner
from tests.conftest import RULES_DIR
from tests.factories import make_case, make_order, make_seller
from tests.fakes import FakeClock
from tests.helpers import engine_result, facts_for

pytestmark = pytest.mark.pg

SETTINGS = Settings(max_attempts=3, backoff_base_s=10)
DISPUTE = "PP-D-PG"


class Crash(BaseException):
    """The worker process dies mid-call (not an Exception: nothing in the job catches it)."""


class Dispute1:
    def __init__(self, capture_id: str) -> None:
        self.capture_id = capture_id
        self.offered = False
        self.posts = 0
        self.during_post: Any = None
        self.post_error: BaseException | None = None

    def get_dispute(self, dispute_id: str) -> DisputeDetail:
        return DisputeDetail(
            dispute_id=dispute_id,
            status="WAITING_FOR_SELLER_RESPONSE",
            stage="INQUIRY",
            capture_ids=[self.capture_id],
            offer_types=["REFUND"],
            seller_offer_made=self.offered,
        )

    def make_dispute_offer(
        self, dispute_id: str, *, offer: Money, note: str, request_id: str
    ) -> None:
        del dispute_id, offer, note, request_id
        self.posts += 1
        if self.during_post is not None:
            self.during_post()
        if self.post_error is not None:
            error, self.post_error = self.post_error, None
            raise error
        self.offered = True


def _queued_offer(sf, clock) -> tuple[str, str]:
    rulebook = load_rules(RULES_DIR)
    with sf() as s:
        seller = make_seller(s)
        order = make_order(s, seller, amount_minor=4000)
        case = make_case(s, order)
        s.add(Dispute(order_id=order.id, paypal_dispute_id=DISPUTE, status="open"))
        s.flush()
        policy = PolicyService(s, rulebook, clock)
        policy.decide(case, engine_result(), facts_for(order))  # an open dispute: evidence route
        assert case.state == CaseState.DISPUTE_EVIDENCE
        ma = queue_dispute_offer(
            s,
            policy,
            case,
            _seller(seller.id),
            verify_url="https://doorstep.example/v",
        )
        s.commit()
        return ma.id, str(order.capture_id)


def _seller(seller_id: str):
    from app.cases.actions import seller_approver

    return seller_approver(seller_id)


def _row(sf, ma_id: str) -> tuple[str, str | None]:
    with sf() as s:
        ma = s.get(MoneyAction, ma_id)
        assert ma is not None
        return ma.status, ma.paypal_status


def test_PP_09_a_second_worker_cannot_run_the_job_while_the_post_is_in_flight(pg_session_factory):
    sf, clock = pg_session_factory, FakeClock()
    ma_id, capture = _queued_offer(sf, clock)
    paypal = Dispute1(capture)
    handlers = {DISPUTE_OFFER_JOB: OfferJobHandler(paypal, clock)}
    ran_by_b: list[int] = []

    def worker_b() -> None:  # runs while worker A is inside the POST
        ran_by_b.append(JobRunner(sf, handlers, clock, SETTINGS).run_due())

    paypal.during_post = worker_b
    assert JobRunner(sf, handlers, clock, SETTINGS).run_due() == 1
    assert ran_by_b == [0] and paypal.posts == 1
    assert _row(sf, ma_id) == ("completed", OFFERED)


def test_PP_09_a_crash_after_the_claim_leads_only_to_reads(pg_session_factory):
    sf, clock = pg_session_factory, FakeClock()
    ma_id, capture = _queued_offer(sf, clock)
    paypal = Dispute1(capture)
    paypal.post_error = Crash()
    paypal.offered = False
    handlers = {DISPUTE_OFFER_JOB: OfferJobHandler(paypal, clock)}
    with pytest.raises(Crash):
        JobRunner(sf, handlers, clock, SETTINGS).run_due()
    assert _row(sf, ma_id) == ("queued", ATTEMPTED)  # the marker was committed before the POST
    assert JobRunner(sf, handlers, clock, SETTINGS).run_due() == 0  # the lease holds
    clock.advance(OFFER_LEASE_S + 1)
    paypal.offered = True  # PayPal did make it before the crash
    assert JobRunner(sf, handlers, clock, SETTINGS).run_due() == 1
    assert paypal.posts == 1
    assert _row(sf, ma_id) == ("completed", OFFERED)


@pytest.mark.parametrize(
    ("error", "job_status", "row"),
    [
        (PayPalClientError(422, "UNPROCESSABLE_ENTITY"), "failed", ("failed", None)),
        (PayPalClientError(429, "RATE_LIMIT_REACHED"), "queued", ("queued", None)),
        (PayPalServerError(503), "done", ("failed", "OFFER_UNCONFIRMED")),
    ],
)
def test_PP_09_failures_after_the_claim_are_recorded_by_the_runner(
    pg_session_factory, error, job_status, row
):
    sf, clock = pg_session_factory, FakeClock()
    ma_id, capture = _queued_offer(sf, clock)
    paypal = Dispute1(capture)
    paypal.post_error = error
    handlers = {DISPUTE_OFFER_JOB: OfferJobHandler(paypal, clock)}
    JobRunner(sf, handlers, clock, SETTINGS).run_due()
    with sf() as s:
        job = s.scalars(select(Job).where(Job.kind == DISPUTE_OFFER_JOB)).one()
        assert job.status == job_status
        if job_status == "queued":  # handed back with the runner's backoff, not the lease
            assert job.run_at == clock.now() + timedelta(seconds=SETTINGS.backoff_base_s)
            assert job.attempts == 1
    assert _row(sf, ma_id) == row
    assert paypal.posts == 1
