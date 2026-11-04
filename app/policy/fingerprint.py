"""Evidence fingerprint for the pre-refund re-check (PO-13, PR-04)."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.orders.models import Evidence


def evidence_fingerprint(session: Session, order_id: str) -> str:
    """Stable hash of the order's evidence rows; any added/changed evidence changes it."""
    session.flush()
    rows = session.scalars(
        select(Evidence).where(Evidence.order_id == order_id).order_by(Evidence.id)
    ).all()
    canonical = [
        {"id": e.id, "kind": e.kind, "uri": e.uri, "ai_result": e.ai_result or {}} for e in rows
    ]
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))  # JSON columns only
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()  # pragma: no mutate
