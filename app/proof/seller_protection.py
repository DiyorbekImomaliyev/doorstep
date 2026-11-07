"""PayPal's Seller Protection verdict on a capture (PP-11).

PayPal reports ``seller_protection.status`` (ELIGIBLE, PARTIALLY_ELIGIBLE, NOT_ELIGIBLE) and the
dispute categories it covers on every capture. Doorstep appends each new report to the order's
audit trail and shows the latest one to the seller. It is information from PayPal, not evidence:
it is kept out of the evidence table on purpose, so the failure-point engine, the money policy
and the pre-refund evidence fingerprint never see it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.orders.models import AuditLog

ACTION = "paypal.seller_protection"
ACTOR = "paypal"


def latest_reports(session: Session, order_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    """The latest report per order, in one query for any number of orders."""
    latest: dict[str, dict[str, Any]] = {}
    for row in session.scalars(
        select(AuditLog)
        .where(
            AuditLog.entity_type == "order",
            AuditLog.entity_id.in_(order_ids),
            AuditLog.action == ACTION,
        )
        .order_by(AuditLog.at.desc(), AuditLog.id.desc())
    ):
        latest.setdefault(row.entity_id, dict(row.after))
    return latest


def latest_report(session: Session, order_id: str) -> dict[str, Any] | None:
    return latest_reports(session, [order_id]).get(order_id)


def record_seller_protection(
    session: Session,
    order_id: str,
    status: str | None,
    categories: Sequence[str],
    now: datetime,
) -> bool:
    """Append PayPal's verdict when it is new. No status, or a repeat, writes nothing."""
    if not status:
        return False
    report = {"status": status, "dispute_categories": list(categories)}
    previous = latest_report(session, order_id)
    if previous == report:
        return False
    session.add(
        AuditLog(
            actor=ACTOR,
            action=ACTION,
            entity_type="order",
            entity_id=order_id,
            before=previous or {},
            after=report,
            at=now,
        )
    )
    session.flush()
    return True
