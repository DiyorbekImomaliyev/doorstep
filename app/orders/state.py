"""The ONLY place order and case states change (hard rule 6). SPEC section 8 tables."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy.orm import Session

from app.orders.enums import CaseState, OrderState
from app.orders.models import Case, Order


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


def transition_order(
    session: Session, order: Order, to: OrderState, *, actor: str, reason: str = ""
) -> None:
    raise NotImplementedError


def transition_case(
    session: Session, case: Case, to: CaseState, *, actor: str, reason: str = ""
) -> None:
    raise NotImplementedError


def open_case(session: Session, order: Order, *, reported_by: str, reason: str, actor: str) -> Case:
    raise NotImplementedError


def apply_carrier_event(
    session: Session, order: Order, status: CarrierStatus, *, actor: str = "carrier"
) -> CarrierEventOutcome:
    raise NotImplementedError
