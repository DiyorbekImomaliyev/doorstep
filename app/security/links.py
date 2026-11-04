"""Signed, order-scoped links (BC-01, BC-04, MC-01, MC-02).

A token is `base64url(claims).base64url(hmac_sha256(claims))`. Claims hold the order id, a scope
("buyer", "mcp", ...) and an expiry. A token for one order or scope can never act on another.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

from app.clock import Clock

MIN_SECRET_BYTES = 16
DEFAULT_TTL_S = 30 * 24 * 3600


class InvalidLink(Exception):
    """Missing, malformed, forged, expired or wrong-scope token. Callers answer 404."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class LinkSigner:
    def __init__(self, secret: str, clock: Clock) -> None:
        if len(secret.encode("utf-8")) < MIN_SECRET_BYTES:
            raise ValueError(f"signing secret must be at least {MIN_SECRET_BYTES} bytes")
        self._key = secret.encode("utf-8")
        self.clock = clock

    def _mac(self, body: str) -> str:
        return _b64(hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest())

    def issue(self, order_id: str, *, scope: str, ttl_s: int = DEFAULT_TTL_S) -> str:
        exp = int(self.clock.now().timestamp()) + ttl_s
        claims = json.dumps({"o": order_id, "s": scope, "e": exp}, separators=(",", ":"))
        body = _b64(claims.encode("utf-8"))
        return f"{body}.{self._mac(body)}"

    def verify(self, token: str, *, scope: str) -> str:
        """The order id the token is scoped to, or InvalidLink."""
        if not token.isascii():  # tokens are base64url text; anything else is forged
            raise InvalidLink("malformed token")
        body, dot, sig = token.partition(".")
        if not dot or not body or not sig or "." in sig:
            raise InvalidLink("malformed token")
        if not hmac.compare_digest(sig, self._mac(body)):
            raise InvalidLink("bad signature")
        try:
            claims = json.loads(_unb64(body))
        except ValueError:
            raise InvalidLink("malformed claims") from None
        if not isinstance(claims, dict) or claims.get("s") != scope:
            raise InvalidLink("wrong scope")
        order_id, exp = claims.get("o"), claims.get("e")
        if not isinstance(order_id, str) or not isinstance(exp, int):
            raise InvalidLink("malformed claims")
        if self.clock.now().timestamp() > exp:
            raise InvalidLink("expired")
        return order_id
