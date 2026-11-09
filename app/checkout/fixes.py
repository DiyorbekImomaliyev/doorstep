"""EC-02: orders whose address the buyer fixed before paying.

Doorstep's address check writes a `checkout.address_checked` audit row each time it runs. The
first check runs on the address PayPal returns; another runs only on an address the buyer
corrected. So an order with a failed check and a passed one had its address fixed before
capture, whatever order the rows were written in. A buyer who kept the address has no passed
check, so it is not counted.
"""

from __future__ import annotations

from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.orders.models import AuditLog

ADDRESS_CHECKED = "checkout.address_checked"


def fixed_addresses(session: Session, order_ids: Collection[str]) -> set[str]:
    """The ids, of those given, whose address failed the check and then passed it."""
    if not order_ids:
        return set()
    seen: dict[str, set[bool]] = {}
    for order_id, after in session.execute(
        select(AuditLog.entity_id, AuditLog.after).where(
            AuditLog.action == ADDRESS_CHECKED,
            AuditLog.entity_type == "order",
            AuditLog.entity_id.in_(list(order_ids)),
        )
    ):
        seen.setdefault(order_id, set()).add(bool((after or {}).get("valid")))
    return {order_id for order_id, results in seen.items() if results == {True, False}}
