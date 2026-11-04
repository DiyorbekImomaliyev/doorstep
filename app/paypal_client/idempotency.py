"""PayPal-Request-Id keys (PP-05): {entity}:{id}:{action}[:{money_action_id}]."""

from __future__ import annotations

# PayPal's documented limit for PayPal-Request-Id; confirm against the docs in phase C.
PAYPAL_REQUEST_ID_MAX_LEN = 108


def request_id_for(
    entity: str, entity_id: str, action: str, money_action_id: str | None = None
) -> str:
    raise NotImplementedError
