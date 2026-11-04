from __future__ import annotations

from typing import Any


def shippo_event_id(payload: dict[str, Any]) -> str:
    """WH-08: sha256(tracking_number|status|status_date) hex digest when Shippo sends no id."""
    raise NotImplementedError
