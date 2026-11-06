"""R25 and R27 on real Postgres: the guarantees need real row locks.

R25 (review S9): the daily refund cap holds when two decisions for one seller race.
R27 (review S7): while the explainer's model call runs, the order row is not locked.
Skipped unless a Postgres test URL is set; CI sets REQUIRE_PG=1 so they always run there.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.ai.explainer import Explainer
from app.cases.engine import ClaimReason
from app.cases.pipeline import CasePipeline
from app.orders.enums import CaseState
from app.orders.models import Case, MoneyAction, Order
from app.policy.rules import load_rules
from app.policy.service import PolicyService
from app.proof.receipts import ReceiptSigner
from tests.conftest import RULES_DIR
from tests.factories import make_case, make_order, make_seller
from tests.fakes import FakeClock
from tests.helpers import engine_result, facts_for
from tests.unit.test_ex_explainer import _draft
from tests.unit.test_sl_seal import _shipped

pytestmark = pytest.mark.pg


def test_R25_concurrent_decisions_cannot_both_pass_the_daily_cap(pg_session_factory):
    sf, rulebook, clock = pg_session_factory, load_rules(RULES_DIR), FakeClock()
    with sf() as s:
        seller = make_seller(s, daily_refund_caps={"GBP": 6000})
        cases = [make_case(s, make_order(s, seller, amount_minor=4000)) for _ in range(2)]
        s.commit()
        ids = [c.id for c in cases]

    first = sf()
    case_a = first.get(Case, ids[0])
    PolicyService(first, rulebook, clock).decide(
        case_a, engine_result(), facts_for(first.get(Order, case_a.order_id))
    )  # queued 4000 of the 6000 cap, not yet committed: the seller row stays locked
    routes: list[str] = []

    def second() -> None:
        with sf() as s:
            case_b = s.get(Case, ids[1])
            PolicyService(s, rulebook, clock).decide(
                case_b, engine_result(), facts_for(s.get(Order, case_b.order_id))
            )
            s.commit()
            routes.append(case_b.state)

    thread = threading.Thread(target=second)
    thread.start()
    thread.join(timeout=0.5)
    waited = thread.is_alive()
    try:
        first.commit()
    finally:
        first.close()  # an open transaction here would block the fixture's teardown forever
        thread.join(timeout=10)
    assert waited, "the second decision must wait for the first one's seller lock"
    assert routes == [CaseState.AWAITING_SELLER.value]  # 4000 + 4000 > 6000: the seller decides
    with sf() as s:
        assert len(list(s.scalars(select(MoneyAction)))) == 1


class LockProbe:
    """During the model call, try to lock the order row from another connection, without waiting."""

    def __init__(self, sf, order_id: str) -> None:
        self.sf, self.order_id = sf, order_id
        self.order_was_free: list[bool] = []

    def complete_structured(self, *, schema: type[BaseModel], **kwargs: Any) -> BaseModel:
        with self.sf() as other:
            try:
                other.execute(
                    select(Order.id).where(Order.id == self.order_id).with_for_update(nowait=True)
                )
                self.order_was_free.append(True)
            except OperationalError:
                self.order_was_free.append(False)
            other.rollback()
        return _draft()


def test_R27_the_order_row_is_free_while_the_model_runs(pg_session_factory, settings):
    sf, rulebook, clock = pg_session_factory, load_rules(RULES_DIR), FakeClock()
    with sf() as s:
        _, order, _ = _shipped(s, clock)
        s.commit()
        probe = LockProbe(sf, order.id)
        pipeline = CasePipeline(
            s,
            rulebook,
            clock,
            settings,
            Explainer(probe),
            ReceiptSigner.generate(),
            ai_claims=False,
        )
        pipeline.report(order, ClaimReason.NOT_RECEIVED, reported_by="buyer")
        s.commit()
    assert probe.order_was_free == [True]


def test_R31_a_concurrent_double_tap_signs_one_approve_receipt(pg_session_factory):
    from app.cases.actions import CaseActions
    from app.orders.enums import ApproverKind
    from app.policy.service import Approver
    from app.proof.models import Receipt

    sf, rulebook, clock = pg_session_factory, load_rules(RULES_DIR), FakeClock()
    signer = ReceiptSigner.generate()
    with sf() as s:
        order = make_order(s, make_seller(s), amount_minor=8000)
        case = make_case(s, order)
        PolicyService(s, rulebook, clock).decide(case, engine_result(), facts_for(order))
        s.commit()
        case_id = case.id
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def tap() -> None:
        with sf() as session:
            c = session.get(Case, case_id)
            barrier.wait()
            try:
                CaseActions(session, rulebook, clock, signer).act(
                    c, "approve", Approver(kind=ApproverKind.SELLER, id="seller-1")
                )
                session.commit()
            except BaseException as exc:
                errors.append(exc)

    threads = [threading.Thread(target=tap) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert errors == []
    with sf() as s:
        kinds = list(s.scalars(select(Receipt.kind).where(Receipt.case_id == case_id)))
    assert kinds.count("approve") == 1
