"""Shippo webhook helpers."""

from __future__ import annotations

import hashlib
from typing import Any


def shippo_event_id(payload: dict[str, Any]) -> str:
    """WH-08: sha256(tracking_number|status|status_date) hex digest when Shippo sends no id."""
    data = payload.get("data")
    data = data if isinstance(data, dict) else {}
    status_block = data.get("tracking_status")
    status_block = status_block if isinstance(status_block, dict) else {}
    key = "|".join(
        str(part or "")
        for part in (
            data.get("tracking_number"),
            status_block.get("status"),
            status_block.get("status_date"),
        )
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
