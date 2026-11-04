"""PO-06 — total refunds never exceed the captured amount, for any sequence of decisions,
approvals, retries, re-reviews and refunds made outside Doorstep."""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.config import Settings
from app.orders.enums import ApproverKind, CaseState, Fix
from app.orders.state import IllegalTransition, transition_case
from app.paypal_client.errors import PayPalServerError
from app.policy.rules import load_rules
from app.policy.service import REFUND_JOB, Approver, PolicyService, RefundJobHandler
from tests.conftest import RULES_DIR, sqlite_session_factory
from tests.factories import make_case, make_order, make_seller
from tests.fakes import FakeClock, FakePayPal
from tests.helpers import engine_result, facts_for, money, run_jobs

ops = st.lists(
    st.one_of(
        st.tuples(
            st.just("decide"),
            st.integers(1, 150_000),
            st.sampled_from([Fix.REFUND, Fix.PARTIAL_REFUND]),
        ),
        st.sampled_from([ApproverKind.SELLER, ApproverKind.REVIEWER]).map(lambda k: ("approve", k)),
        st.just(("run_jobs",)),
        st.tuples(st.just("external"), st.integers(1, 60_000), st.booleans()),
        st.just(("regather",)),
        st.just(("flaky",)),
    ),
    max_size=25,
)


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(captured=st.integers(min_value=100, max_value=100_000), ops=ops)
def test_PO_06_total_refunds_never_exceed_capture(captured, ops):
    session_factory = sqlite_session_factory()
    db = session_factory()
    clock = FakeClock()
    cfg = Settings(max_attempts=3, backoff_base_s=10)
    rulebook = load_rules(RULES_DIR)
    paypal = FakePayPal()
    seller = make_seller(db)
    order = make_order(db, seller, amount_minor=captured)
    paypal.add_capture(order.paypal_order_id, order.capture_id, money(captured))
    case = make_case(db, order)
    db.commit()
    policy = PolicyService(db, rulebook, clock)
    handlers = {REFUND_JOB: RefundJobHandler(paypal, rulebook, clock)}

    for op in ops:
        try:
            if op[0] == "decide" and case.state == CaseState.GATHERING_EVIDENCE:
                policy.decide(case, engine_result(), facts_for(order, op[1], fix=op[2]))
            elif op[0] == "approve" and case.state in (
                CaseState.AWAITING_SELLER,
                CaseState.HOLD_FOR_REVIEW,
            ):
                policy.approve(case, Approver(kind=op[1], id="human"))
            elif op[0] == "run_jobs":
                db.commit()
                run_jobs(session_factory, handlers, clock, cfg, rounds=2, advance_s=10_000)
                db.expire_all()
            elif op[0] == "external":
                remaining = captured - paypal.total_refunded(order.capture_id)
                if 0 < op[1] <= remaining:  # PayPal itself would reject more
                    ref = paypal.external_refund(order.capture_id, op[1])
                    if op[2]:  # the PAYMENT.CAPTURE.REFUNDED webhook has arrived
                        policy.record_external_refund(order, money(op[1]), paypal_refund_id=ref)
            elif op[0] == "regather" and case.state == CaseState.HOLD_FOR_REVIEW:
                transition_case(db, case, CaseState.GATHERING_EVIDENCE, actor="reviewer")
            elif op[0] == "flaky":
                paypal.fail_next_refunds(PayPalServerError(503))
            db.commit()
        except IllegalTransition:
            db.rollback()
        assert paypal.total_refunded(order.capture_id) <= captured
    db.close()
