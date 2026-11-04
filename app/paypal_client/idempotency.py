"""PayPal-Request-Id keys (PP-05): {entity}:{id}:{action}[:{money_action_id}].

Deterministic, so a retried job always sends the same key and PayPal answers with the original
result instead of moving money twice (hard rule 4). Keys longer than PayPal's limit are replaced
by a SHA-256 of the full key, which stays deterministic and collision-resistant.
"""

from __future__ import annotations

import hashlib

# PayPal documents PayPal-Request-Id as max 108 characters (Orders v2 / Payments v2 headers).
PAYPAL_REQUEST_ID_MAX_LEN = 108


def request_id_for(
    entity: str, entity_id: str, action: str, money_action_id: str | None = None
) -> str:
    parts = [entity, entity_id, action]
    if money_action_id is not None:
        parts.append(money_action_id)
    key = ":".join(parts)
    if len(key) <= PAYPAL_REQUEST_ID_MAX_LEN:
        return key
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()  # 64 hex chars
    prefix = f"{entity}:{action}:"[: PAYPAL_REQUEST_ID_MAX_LEN - len(digest)]
    return prefix + digest
