"""Expected transition tables, copied from docs/SPEC.md section 8. Tests compare code to these."""

from __future__ import annotations

from app.orders.enums import CaseState as C
from app.orders.enums import OrderState as O

ORDER_ALLOWED: dict[O, set[O]] = {
    O.CREATED: {O.PAID},
    O.PAID: {O.PACKED, O.SHIPPED},
    O.PACKED: {O.SHIPPED},
    O.SHIPPED: {O.IN_TRANSIT, O.DELIVERED, O.PICKED_UP, O.EXCEPTION},
    O.IN_TRANSIT: {O.DELIVERED, O.PICKED_UP, O.EXCEPTION, O.CASE_OPEN},
    O.EXCEPTION: {O.IN_TRANSIT, O.DELIVERED, O.PICKED_UP, O.CASE_OPEN},
    O.DELIVERED: {O.CONFIRMED, O.CASE_OPEN},
    O.PICKED_UP: {O.CONFIRMED, O.CASE_OPEN},
    O.CONFIRMED: {O.CASE_OPEN},
    O.CASE_OPEN: {O.RESOLVED},
    O.RESOLVED: set(),
}

CASE_ALLOWED: dict[C, set[C]] = {
    C.OPEN: {C.GATHERING_EVIDENCE},
    C.GATHERING_EVIDENCE: {C.DECIDED},
    C.DECIDED: {C.AUTO_REFUND, C.AWAITING_SELLER, C.HOLD_FOR_REVIEW, C.DISPUTE_EVIDENCE},
    C.AUTO_REFUND: {C.SETTLED, C.CLOSED, C.HOLD_FOR_REVIEW, C.DISPUTE_EVIDENCE},
    C.AWAITING_SELLER: {C.SETTLED, C.CLOSED, C.HOLD_FOR_REVIEW, C.DISPUTE_EVIDENCE},
    C.HOLD_FOR_REVIEW: {C.SETTLED, C.CLOSED, C.GATHERING_EVIDENCE},
    C.DISPUTE_EVIDENCE: {C.SETTLED, C.CLOSED},
    C.SETTLED: set(),
    C.CLOSED: set(),
}


def pairs(table: dict, allowed: bool) -> list[tuple]:
    states = list(table)
    return [(a, b) for a in states for b in states if (b in table[a]) == allowed]
