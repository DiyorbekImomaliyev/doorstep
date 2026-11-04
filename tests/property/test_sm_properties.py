"""SM-04 and SM-06 invariants under random operation sequences (Hypothesis)."""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import select

from app.orders.enums import ApproverKind, CaseState, Fix, MoneyActionStatus, OrderState
from app.orders.models import Approval, AuditLog, Case, Decision, MoneyAction
from app.orders.state import (
    CaseAlreadyOpen,
    IllegalTransition,
    open_case,
    transition_case,
    transition_order,
)
from app.policy.rules import load_rules
from app.policy.service import Approver, PolicyService
from tests.conftest import RULES_DIR, sqlite_session_factory
from tests.factories import (
    add_approval,
    add_decision,
    add_money_action,
    make_case,
    make_order,
    make_seller,
)
from tests.fakes import FakeClock
from tests.helpers import engine_result, facts_for

PROPERTY = settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])

order_ops = st.lists(
    st.one_of(
        st.tuples(st.just("order"), st.sampled_from(list(OrderState))),
        st.just(("open_case",)),
        st.just(("decide",)),
        st.just(("approve",)),
    ),
    max_size=30,
)


@PROPERTY
@given(ops=order_ops)
def test_SM_04_no_refund_without_paid_property(ops):
    db = sqlite_session_factory()()
    clock = FakeClock()
    rulebook = load_rules(RULES_DIR)
    seller = make_seller(db)
    order = make_order(
        db,
        seller,
        state=OrderState.CREATED,
        amount_minor=3000,
        capture_id=None,
        capture_status=None,
    )
    db.commit()
    case: Case | None = None
    for op in ops:
        try:
            if op[0] == "order":
                transition_order(db, order, op[1], actor="prop")
            elif op[0] == "open_case":
                case = open_case(
                    db, order, reported_by="buyer", reason="not_received", actor="prop"
                )
                transition_case(db, case, CaseState.GATHERING_EVIDENCE, actor="prop")
            elif op[0] == "decide" and case is not None:
                PolicyService(db, rulebook, clock).decide(case, engine_result(), facts_for(order))
            elif op[0] == "approve" and case is not None:
                PolicyService(db, rulebook, clock).approve(
                    case, Approver(kind=ApproverKind.SELLER, id="seller")
                )
            db.commit()
        except (IllegalTransition, CaseAlreadyOpen):
            db.rollback()
        refunds = db.scalars(select(MoneyAction).where(MoneyAction.order_id == order.id)).all()
        if refunds:
            reached_paid = db.scalars(select(AuditLog).where(AuditLog.entity_id == order.id)).all()
            assert any(r.after.get("state") == OrderState.PAID for r in reached_paid), (
                "refund money_action exists for an order that never reached PAID"
            )
    db.close()


case_ops = st.lists(
    st.one_of(
        st.tuples(st.just("to"), st.sampled_from(list(CaseState))),
        st.tuples(
            st.just("decision"), st.sampled_from([Fix.REFUND, Fix.PARTIAL_REFUND, Fix.RESHIP])
        ),
        st.just(("approval",)),
        st.tuples(
            st.just("money"),
            st.sampled_from([MoneyActionStatus.COMPLETED, MoneyActionStatus.FAILED]),
        ),
    ),
    max_size=30,
)


def _settled_is_justified(db, case: Case) -> bool:
    decisions = db.scalars(select(Decision).where(Decision.case_id == case.id)).all()
    for d in decisions:
        approved = db.scalars(select(Approval).where(Approval.decision_id == d.id)).first()
        if approved is None:
            continue
        if d.fix == Fix.RESHIP:
            return True
        paid = db.scalars(
            select(MoneyAction).where(
                MoneyAction.decision_id == d.id, MoneyAction.status == MoneyActionStatus.COMPLETED
            )
        ).first()
        if paid is not None:
            return True
    return False


@PROPERTY
@given(ops=case_ops)
def test_SM_06_settled_requires_decision_and_approval_property(ops):
    db = sqlite_session_factory()()
    order = make_order(db, make_seller(db))
    case = make_case(db, order, state=CaseState.OPEN)
    db.commit()
    decision: Decision | None = None
    for op in ops:
        try:
            if op[0] == "to":
                transition_case(db, case, op[1], actor="prop")
            elif op[0] == "decision":
                decision = add_decision(db, case, fix=op[1])
            elif op[0] == "approval" and decision is not None:
                add_approval(db, case, decision, ApproverKind.SELLER)
            elif op[0] == "money" and decision is not None:
                existing = db.scalars(
                    select(MoneyAction).where(MoneyAction.decision_id == decision.id)
                ).first()
                if existing is None:
                    add_money_action(db, case, decision, status=op[1])
            db.commit()
        except IllegalTransition:
            db.rollback()
        if case.state == CaseState.SETTLED:
            assert _settled_is_justified(db, case)
    db.close()
