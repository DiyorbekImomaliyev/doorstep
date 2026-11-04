"""Money: integer minor units + ISO 4217 code. GBP and USD only (PO-10, ADR-003, ADR-008)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class Currency(StrEnum):
    GBP = "GBP"
    USD = "USD"


class UnsupportedCurrency(ValueError):
    pass


class CurrencyMismatch(ValueError):
    pass


class Money(BaseModel):
    amount_minor: int
    currency: str

    def to_paypal_value(self) -> str:
        """Exact decimal string for PayPal, e.g. 6000 GBP -> "60.00"."""
        raise NotImplementedError

    @classmethod
    def from_paypal(cls, value: str, currency: str) -> Money:
        """Parse PayPal's decimal string exactly; reject anything that is not whole minor units."""
        raise NotImplementedError

    def __add__(self, other: Money) -> Money:
        raise NotImplementedError

    def __sub__(self, other: Money) -> Money:
        raise NotImplementedError

    def __lt__(self, other: Money) -> bool:
        raise NotImplementedError

    def __le__(self, other: Money) -> bool:
        raise NotImplementedError

    def __gt__(self, other: Money) -> bool:
        raise NotImplementedError

    def __ge__(self, other: Money) -> bool:
        raise NotImplementedError
