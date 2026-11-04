from __future__ import annotations

from sqlalchemy.orm import Session


def evidence_fingerprint(session: Session, order_id: str) -> str:
    """Stable hash of the order's evidence rows; any added/changed evidence changes it."""
    raise NotImplementedError
