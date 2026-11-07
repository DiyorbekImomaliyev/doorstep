"""The ONLY place order and case states change (hard rule 6). Illegal transitions raise.

Every transition:
1. takes a row lock on the entity (``SELECT ... FOR UPDATE`` on Postgres) and re-reads its state,
   so two concurrent transitions are serialised and the loser sees the winner's state (SM-08);
2. checks the transition table and raises ``IllegalTransition`` without changing anything (SM-02);
3. writes an ``audit_log`` row in the same DB transaction (SM-03). The caller commits.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.orders.enums import CaseState, Fix, MoneyActionStatus, OrderState
from app.orders.models import Approval, AuditLog, Case, Decision, MoneyAction, Order

log = logging.getLogger("doorstep.state")

O = OrderState  # noqa: E741 - table readability
C = CaseState

ORDER_TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    O.CREATED: frozenset({O.PAID}),
    O.PAID: frozenset({O.PACKED, O.SHIPPED}),
    O.PACKED: frozenset({O.SHIPPED}),
    O.SHIPPED: frozenset({O.IN_TRANSIT, O.DELIVERED, O.PICKED_UP, O.EXCEPTION}),
    O.IN_TRANSIT: frozenset({O.DELIVERED, O.PICKED_UP, O.EXCEPTION, O.CASE_OPEN}),
    O.EXCEPTION: frozenset({O.IN_TRANSIT, O.DELIVERED, O.PICKED_UP, O.CASE_OPEN}),
    O.DELIVERED: frozenset({O.CONFIRMED, O.CASE_OPEN}),
    O.PICKED_UP: frozenset({O.CONFIRMED, O.CASE_OPEN}),
    O.CONFIRMED: frozenset({O.CASE_OPEN}),
    O.CASE_OPEN: frozenset({O.RESOLVED}),
    O.RESOLVED: frozenset(),
}

CASE_TRANSITIONS: dict[CaseState, frozenset[CaseState]] = {
    C.OPEN: frozenset({C.GATHERING_EVIDENCE}),
    C.GATHERING_EVIDENCE: frozenset({C.DECIDED}),
    C.DECIDED: frozenset({C.AUTO_REFUND, C.AWAITING_SELLER, C.HOLD_FOR_REVIEW, C.DISPUTE_EVIDENCE}),
    C.AUTO_REFUND: frozenset({C.SETTLED, C.CLOSED, C.HOLD_FOR_REVIEW, C.DISPUTE_EVIDENCE}),
    C.AWAITING_SELLER: frozenset({C.SETTLED, C.CLOSED, C.HOLD_FOR_REVIEW, C.DISPUTE_EVIDENCE}),
    C.HOLD_FOR_REVIEW: frozenset({C.SETTLED, C.CLOSED, C.GATHERING_EVIDENCE}),
    C.DISPUTE_EVIDENCE: frozenset({C.SETTLED, C.CLOSED}),
    C.SETTLED: frozenset(),
    C.CLOSED: frozenset(),
}

FINISHED_CASE_STATES = frozenset({C.SETTLED, C.CLOSED})
_MONEY_FIXES = frozenset({Fix.REFUND.value, Fix.PARTIAL_REFUND.value})


class IllegalTransition(Exception):
    pass


class SettlementGuardError(IllegalTransition):
    """SM-06: SETTLED needs a decision, an approval and (if money moved) a completed refund."""


class CaseAlreadyOpen(Exception):
    pass


class CarrierStatus(StrEnum):
    PRE_TRANSIT = "PRE_TRANSIT"
    TRANSIT = "TRANSIT"
    DELIVERED = "DELIVERED"
    RETURNED = "RETURNED"
    FAILURE = "FAILURE"
    UNKNOWN = "UNKNOWN"


class CarrierEventOutcome(StrEnum):
    APPLIED = "applied"
    NOOP = "noop"
    IGNORED_OUT_OF_ORDER = "ignored_out_of_order"


# Carrier status -> the order state it implies. None: carries no state information.
CARRIER_TARGET: dict[CarrierStatus, OrderState | None] = {
    CarrierStatus.PRE_TRANSIT: O.SHIPPED,
    CarrierStatus.TRANSIT: O.IN_TRANSIT,
    CarrierStatus.DELIVERED: O.DELIVERED,
    CarrierStatus.FAILURE: O.EXCEPTION,
    CarrierStatus.RETURNED: O.EXCEPTION,
    CarrierStatus.UNKNOWN: None,
}


def _audit(
    session: Session,
    *,
    entity_type: str,
    entity_id: str,
    actor: str,
    action: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    session.add(
        AuditLog(
            actor=actor[:64],
            action=action[:64],
            entity_type=entity_type,
            entity_id=entity_id,
            before=before,
            after=after,
        )
    )


def _action(reason: str) -> str:
    return f"transition: {reason}" if reason else "transition"


def _lock(session: Session, entity: Order | Case) -> None:
    """Flush pending changes, then reload the row under a lock (no-op lock on SQLite)."""
    session.flush()
    session.refresh(entity, with_for_update=True)


def transition_order(
    session: Session, order: Order, to: OrderState, *, actor: str, reason: str = ""
) -> None:
    _lock(session, order)
    current = OrderState(order.state)
    if to not in ORDER_TRANSITIONS[current]:
        raise IllegalTransition(f"order {order.id}: {current} -> {to} is not allowed")
    order.state = to.value
    _audit(
        session,
        entity_type="order",
        entity_id=order.id,
        actor=actor,
        action=_action(reason),
        before={"state": current.value},
        after={"state": to.value},
    )
    session.flush()


def _settlement_justified(session: Session, case: Case) -> bool:
    """Some approved decision on this case is a reship, or its refund completed (SM-06)."""
    decisions = session.scalars(select(Decision).where(Decision.case_id == case.id)).all()
    for decision in decisions:
        approved = session.scalar(select(exists().where(Approval.decision_id == decision.id)))
        if not approved:
            continue
        if decision.fix not in _MONEY_FIXES:
            return True
        paid = session.scalar(
            select(
                exists().where(
                    MoneyAction.decision_id == decision.id,
                    MoneyAction.status == MoneyActionStatus.COMPLETED.value,
                )
            )
        )
        if paid:
            return True
    return False


def transition_case(
    session: Session, case: Case, to: CaseState, *, actor: str, reason: str = ""
) -> None:
    _lock(session, case)
    current = CaseState(case.state)
    if to not in CASE_TRANSITIONS[current]:
        raise IllegalTransition(f"case {case.id}: {current} -> {to} is not allowed")
    if to == C.SETTLED and not _settlement_justified(session, case):
        raise SettlementGuardError(
            f"case {case.id}: SETTLED needs an approved decision and, for a refund, "
            "a completed money action"
        )
    case.state = to.value
    _audit(
        session,
        entity_type="case",
        entity_id=case.id,
        actor=actor,
        action=_action(reason),
        before={"state": current.value},
        after={"state": to.value},
    )
    session.flush()
    if to in FINISHED_CASE_STATES:  # SM-07 sync rule
        order = session.get(Order, case.order_id)
        assert order is not None
        transition_order(session, order, O.RESOLVED, actor=actor, reason=f"case {to.value}")


def open_case(session: Session, order: Order, *, reported_by: str, reason: str, actor: str) -> Case:
    """Opens the order's single open case and moves the order to CASE_OPEN (SM-07)."""
    _lock(session, order)
    already = session.scalar(
        select(
            exists().where(
                Case.order_id == order.id,
                Case.state.not_in([s.value for s in FINISHED_CASE_STATES]),
            )
        )
    )
    if already:
        raise CaseAlreadyOpen(f"order {order.id} already has an open case")
    transition_order(session, order, O.CASE_OPEN, actor=actor, reason=f"case opened: {reason}")
    case = Case(
        order_id=order.id,
        seller_id=order.seller_id,
        payer_id=order.payer_id,
        reported_by=reported_by,
        reason=reason,
        state=C.OPEN.value,
    )
    session.add(case)
    session.flush()
    _audit(
        session,
        entity_type="case",
        entity_id=case.id,
        actor=actor,
        action="open",
        before={},
        after={"state": C.OPEN.value},
    )
    session.flush()
    return case


def apply_carrier_event(
    session: Session, order: Order, status: CarrierStatus, *, actor: str = "carrier"
) -> CarrierEventOutcome:
    """Repeat -> NOOP; forward and legal -> APPLIED; backward or illegal -> ignored (SM-09).

    Never raises for an out-of-order event: carriers resend and reorder webhooks.
    """
    _lock(session, order)
    current = OrderState(order.state)
    target = CARRIER_TARGET[status]
    if target is None or target == current:
        log.info("carrier event %s is a no-op for order %s in %s", status, order.id, current)
        return CarrierEventOutcome.NOOP
    if target in ORDER_TRANSITIONS[current]:
        transition_order(session, order, target, actor=actor, reason=f"carrier {status}")
        return CarrierEventOutcome.APPLIED
    log.info(
        "carrier event %s ignored for order %s in %s (out of order)", status, order.id, current
    )
    return CarrierEventOutcome.IGNORED_OUT_OF_ORDER
