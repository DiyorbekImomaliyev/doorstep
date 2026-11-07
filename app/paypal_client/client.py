"""Thin PayPal REST client: OAuth token cache, one retry on 401, logging (PP-01, PP-06).

Transport is owned here, not by the generated SDK: the token cache
follows the injectable Clock and refresh margin, a 401 is refreshed and retried exactly once,
errors are mapped by HTTP status even when PayPal's error body is thin, and there are no
transport-level retries. Retries happen only in the jobs outbox, with the same PayPal-Request-Id.

Logs carry method, path, status, latency and the PayPal-Request-Id. They never carry the
client secret, the access token or the Authorization header.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel

from app.clock import Clock
from app.paypal_client.errors import (
    PayPalAuthError,
    PayPalClientError,
    PayPalNotSent,
    PayPalServerError,
    PayPalTimeout,
)

LOGGER_NAME = "doorstep.paypal"
log = logging.getLogger(LOGGER_NAME)
TOKEN_PATH = "/v1/oauth2/token"  # noqa: S105 - a URL path, not a secret


class PayPalConfig(BaseModel):
    client_id: str
    client_secret: str
    base_url: str
    webhook_id: str = ""
    timeout_s: float = 10.0
    token_refresh_margin_s: int = 300


@dataclass
class _Token:
    value: str
    expires_at: float  # Clock.monotonic() seconds


def _json_or_empty(response: httpx.Response) -> dict[str, Any] | list[Any]:
    if not response.content:
        return {}
    try:
        body = response.json()
    except ValueError:
        return {"raw": response.text[:500]}
    return body if isinstance(body, dict | list) else {"value": body}


class PayPalClient:
    def __init__(self, config: PayPalConfig, clock: Clock) -> None:
        self.config = config
        self.clock = clock
        self._http = httpx.Client(
            base_url=config.base_url.rstrip("/"),
            timeout=httpx.Timeout(config.timeout_s),
            headers={"Accept": "application/json"},
        )
        self._token: _Token | None = None
        self._lock = threading.Lock()

    def close(self) -> None:
        self._http.close()

    # ---- OAuth ------------------------------------------------------------------

    def _fresh_token(self) -> str:
        with self._lock:
            now = self.clock.monotonic()
            token = self._token
            if token is not None and now < token.expires_at - self.config.token_refresh_margin_s:
                return token.value
            response = self._send(
                "POST",
                TOKEN_PATH,
                auth=(self.config.client_id, self.config.client_secret),
                data={"grant_type": "client_credentials"},
            )
            if response.status_code in (400, 401, 403):
                raise PayPalAuthError(f"PayPal token request refused ({response.status_code})")
            self._raise_for_status(response)
            body = _json_or_empty(response)
            value = body.get("access_token") if isinstance(body, dict) else None
            if not isinstance(value, str) or not value:
                raise PayPalServerError(response.status_code, "token response had no access_token")
            expires_in = body.get("expires_in", 0) if isinstance(body, dict) else 0
            lifetime = int(expires_in) if isinstance(expires_in, int | str) else 0
            self._token = _Token(value, now + max(lifetime, 0))
            return value

    def _invalidate_token(self) -> None:
        with self._lock:
            self._token = None

    # ---- transport --------------------------------------------------------------

    def _send(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        request_id: str | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """One HTTP exchange, logged. Network failures become PayPalTimeout/PayPalServerError."""
        started = time.perf_counter()
        status: int | None = None
        try:
            response = self._http.request(method, path, headers=dict(headers or {}), **kwargs)
            status = response.status_code
            return response
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            # No connection was opened, so PayPal never saw the request.
            raise PayPalNotSent(f"{method} {path} not sent: {type(exc).__name__}") from exc
        except httpx.TimeoutException as exc:
            raise PayPalTimeout(f"{method} {path} timed out") from exc
        except httpx.TransportError as exc:
            raise PayPalServerError(None, f"{method} {path} failed: {type(exc).__name__}") from exc
        finally:
            latency_ms = round((time.perf_counter() - started) * 1000, 1)
            log.info(
                "paypal %s %s -> %s in %sms",
                method,
                path,
                status if status is not None else "no response",
                latency_ms,
                extra={
                    "method": method,
                    "path": path,
                    "status": status,
                    "latency_ms": latency_ms,
                    "request_id": request_id,
                },
            )

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        status = response.status_code
        if status < 400:
            return
        body = _json_or_empty(response)
        name = str(body.get("name", "")) if isinstance(body, dict) else ""
        debug_id = str(body.get("debug_id", "")) if isinstance(body, dict) else ""
        log.warning(
            "paypal error %s %s debug_id=%s",
            status,
            name,
            debug_id or "-",
            extra={"status": status, "paypal_error": name, "debug_id": debug_id or None},
        )
        suffix = f" (debug_id {debug_id})" if debug_id else ""
        if status >= 500:
            raise PayPalServerError(status, f"PayPal {status} {name}".strip() + suffix)
        raise PayPalClientError(
            status, name, body if isinstance(body, dict) else {"body": body}, debug_id=debug_id
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        request_id: str | None = None,
        files: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Send an authenticated request; map errors to app.paypal_client.errors.

        A 401 refreshes the token and retries once; a second 401 raises PayPalAuthError.
        Returns the JSON object body, or {} for an empty body (e.g. 204). A JSON array body is
        returned under "items".
        """
        extra: dict[str, str] = dict(headers or {})
        if request_id is not None:
            extra["PayPal-Request-Id"] = request_id
        if json is not None and files is None:
            extra.setdefault("Content-Type", "application/json")
        payload: dict[str, Any] = {}
        if json is not None:
            payload["json"] = json
        if files is not None:
            payload["files"] = files
        if data is not None:
            payload["data"] = data
        for attempt in (1, 2):
            try:
                token = self._fresh_token()
            except PayPalNotSent:
                raise
            except PayPalServerError as exc:  # the token request failed: this one was never sent
                raise PayPalNotSent(f"{method} {path} not sent: token request failed") from exc
            response = self._send(
                method,
                path,
                headers={**extra, "Authorization": f"Bearer {token}"},
                request_id=request_id,
                **payload,
            )
            if response.status_code == 401:
                self._invalidate_token()
                if attempt == 1:
                    continue
                raise PayPalAuthError(f"{method} {path}: 401 after token refresh")
            self._raise_for_status(response)
            body = _json_or_empty(response)
            return body if isinstance(body, dict) else {"items": body}
        raise AssertionError("unreachable")  # pragma: no cover
