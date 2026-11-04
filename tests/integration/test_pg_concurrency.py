"""Concurrency guarantees that need real Postgres row locks and constraints.
Skipped unless DATABASE_URL points at Postgres; CI sets REQUIRE_PG=1 so they always run there."""

from __future__ import annotations

import itertools
import json
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.deps import AppDeps
from app.main import create_app
from app.orders.enums import ApproverKind, CaseState, OrderState
from app.orders.models import AuditLog, Case, MoneyAction, Order
from app.orders.state import IllegalTransition, transition_order
from app.policy.rules import load_rules
from app.policy.service import Approver, PolicyService
from app.worker.models import WebhookInbox
from tests.conftest import RULES_DIR
from tests.factories import make_case, make_order, make_seller
from tests.fakes import FakeClock, FakeVerifier
from tests.helpers import engine_result, facts_for

pytestmark = pytest.mark.pg


def test_SM_08_concurrent_order_transitions_serialised_no_lost_update(pg_session_factory):
    sf = pg_session_factory
    with sf() as s:
        order = make_order(s, make_seller(s), state=OrderState.IN_TRANSIT)
        s.commit()
        order_id = order.id
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def move(target: OrderState) -> None:
        with sf() as session:
            o = session.get(Order, order_id)
            barrier.wait()
            try:
                transition_order(session, o, target, actor=target.value)  # type: ignore[arg-type]
                session.commit()
            except IllegalTransition:
                session.rollback()
            except BaseException as exc:
                errors.append(exc)

    threads = [
        threading.Thread(target=move, args=(t,))
        for t in (OrderState.DELIVERED, OrderState.EXCEPTION)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    with sf() as s:
        final = s.get(Order, order_id).state  # type: ignore[union-attr]
        rows = list(
            s.scalars(select(AuditLog).where(AuditLog.entity_id == order_id).order_by(AuditLog.at))
        )
    assert rows, "no transition happened"
    assert rows[0].before == {"state": "IN_TRANSIT"}
    for prev, nxt in itertools.pairwise(rows):
        assert nxt.before == prev.after, "lost update: two transitions started from the same state"
    assert rows[-1].after == {"state": final}


def test_WH_03_concurrent_duplicate_deliveries_process_once(pg_session_factory, settings):
    deps = AppDeps(
        settings=settings,
        session_factory=pg_session_factory,
        paypal_verifier=FakeVerifier(),
        clock=FakeClock(),
        rules_dir=RULES_DIR,
    )
    app = create_app(deps)
    event = json.dumps(
        {"id": "WH-RACE", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {"id": "CAP-1"}}
    )
    barrier = threading.Barrier(2)
    statuses: list[int] = []

    def deliver() -> None:
        client = TestClient(app)
        barrier.wait()
        statuses.append(client.post("/webhooks/paypal", content=event).status_code)

    threads = [threading.Thread(target=deliver) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert statuses == [200, 200]
    with pg_session_factory() as s:
        assert s.scalar(select(func.count()).select_from(WebhookInbox)) == 1


def test_PO_07_concurrent_approvals_create_one_refund(pg_session_factory):
    sf = pg_session_factory
    rulebook = load_rules(RULES_DIR)
    clock = FakeClock()
    with sf() as s:
        order = make_order(s, make_seller(s), amount_minor=8000)
        case = make_case(s, order)
        PolicyService(s, rulebook, clock).decide(case, engine_result(), facts_for(order))
        s.commit()
        assert case.state == CaseState.AWAITING_SELLER
        case_id = case.id
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def approve() -> None:
        with sf() as session:
            c = session.get(Case, case_id)
            barrier.wait()
            try:
                PolicyService(session, rulebook, clock).approve(
                    c, Approver(kind=ApproverKind.SELLER, id="seller-1")
                )  # type: ignore[arg-type]
                session.commit()
            except BaseException as exc:
                errors.append(exc)

    threads = [threading.Thread(target=approve) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [], "a concurrent approval must be idempotent, not crash"
    with sf() as s:
        assert s.scalar(select(func.count()).select_from(MoneyAction)) == 1
