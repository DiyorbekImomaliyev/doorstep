from __future__ import annotations


class PayPalError(Exception):
    pass


class PayPalAuthError(PayPalError):
    """Second 401 in a row (PP-01). The sandbox token or credentials are bad."""


class PayPalClientError(PayPalError):
    """4xx: not retried (PP-07)."""

    def __init__(
        self,
        status: int,
        name: str = "",
        body: dict[str, object] | None = None,
        *,
        debug_id: str = "",
    ):
        suffix = f" (debug_id {debug_id})" if debug_id else ""
        super().__init__(f"PayPal {status} {name}".rstrip() + suffix)
        self.status = status
        self.name = name
        self.body = body or {}
        self.debug_id = debug_id


class PayPalServerError(PayPalError):
    """5xx or timeout: retried by the job runner with the same PayPal-Request-Id (PP-07)."""

    def __init__(self, status: int | None, message: str = ""):
        super().__init__(message or f"PayPal {status}")
        self.status = status


class PayPalTimeout(PayPalServerError):
    def __init__(self, message: str = "timeout"):
        super().__init__(None, message)


class PayPalNotSent(PayPalServerError):
    """The request never reached PayPal: the token could not be fetched, or no connection was
    opened. Retried like a 5xx. A write that must know whether PayPal acted (the dispute offer,
    PP-09) may treat it as "nothing happened"."""

    def __init__(self, message: str):
        super().__init__(None, message)
