"""Thin Shippo REST client (test mode): register a track, validate an address (TR-01, CK-01).

Only the two calls Doorstep needs. Errors raise; the job runner retries track registration.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.orders.schemas import Address

SHIPPO_BASE_URL = "https://api.goshippo.com"


class ShippoError(Exception):
    pass


class ShippoClient:
    def __init__(self, api_key: str, base_url: str = SHIPPO_BASE_URL, timeout_s: float = 10.0):
        self._headers = {"Authorization": f"ShippoToken {api_key}"}
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = httpx.post(
                f"{self.base_url}{path}", json=body, headers=self._headers, timeout=self.timeout_s
            )
        except httpx.HTTPError as exc:
            raise ShippoError(f"Shippo {path} failed: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise ShippoError(f"Shippo {path} answered {response.status_code}")
        data = response.json()
        return data if isinstance(data, dict) else {}

    def register_track(self, carrier: str, tracking_number: str) -> None:
        """Ask Shippo to watch this tracking number; events then arrive by webhook."""
        self._post("/tracks/", {"carrier": carrier, "tracking_number": tracking_number})

    def validate(self, address: Address) -> list[str]:
        """Shippo address validation. Returns the problems Shippo reports; empty means valid."""
        data = self._post(
            "/addresses/",
            {
                "name": address.name,
                "street1": address.line1,
                "street2": address.line2,
                "city": address.city,
                "zip": address.postal_code,
                "country": address.country,
                "validate": True,
            },
        )
        results = data.get("validation_results")
        results = results if isinstance(results, dict) else {}
        if results.get("is_valid", True):
            return []
        messages = results.get("messages")
        texts = (
            [str(m.get("text")) for m in messages if isinstance(m, dict) and m.get("text")]
            if isinstance(messages, list)
            else []
        )
        return texts or ["The carrier could not validate this address."]
