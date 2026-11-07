"""Money: integer minor units + ISO 4217 code. GBP and USD only (PO-10)."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class Currency(StrEnum):
    GBP = "GBP"
    USD = "USD"


# ISO 4217 minor-unit exponent per supported currency. Never assume 2 silently.
MINOR_UNIT_EXPONENT: dict[Currency, int] = {Currency.GBP: 2, Currency.USD: 2}

_PAYPAL_VALUE = re.compile(r"\d+(\.\d+)?")


class UnsupportedCurrency(ValueError):
    pass


class CurrencyMismatch(ValueError):
    pass


def parse_currency(code: str) -> Currency:
    """Exact ISO code (upper case) or UnsupportedCurrency."""
    try:
        return Currency(code)
    except ValueError:
        raise UnsupportedCurrency(f"unsupported currency {code!r}") from None


class Money(BaseModel):
    """Immutable amount. Never a float: integer minor units plus a supported currency."""

    model_config = ConfigDict(frozen=True)

    amount_minor: StrictInt = Field(ge=0)
    currency: Currency

    @property
    def exponent(self) -> int:
        return MINOR_UNIT_EXPONENT[self.currency]

    def to_paypal_value(self) -> str:
        """Exact decimal string for PayPal, e.g. 6000 GBP -> "60.00"."""
        scale = 10**self.exponent
        units, minor = divmod(self.amount_minor, scale)
        return f"{units}.{minor:0{self.exponent}d}" if self.exponent else str(units)

    @classmethod
    def from_paypal(cls, value: str, currency: str) -> Money:
        """Parse PayPal's decimal string exactly; reject anything that is not whole minor units."""
        cur = parse_currency(currency)
        if not _PAYPAL_VALUE.fullmatch(value):
            raise ValueError(f"not a PayPal amount: {value!r}")
        try:
            scaled = Decimal(value).scaleb(MINOR_UNIT_EXPONENT[cur])
        except InvalidOperation as exc:  # pragma: no cover - regex already guards the format
            raise ValueError(f"not a PayPal amount: {value!r}") from exc
        if scaled != scaled.to_integral_value():
            raise ValueError(f"{value!r} is not a whole number of {cur} minor units")
        return cls(amount_minor=int(scaled), currency=cur)

    def zero(self) -> Money:
        return Money(amount_minor=0, currency=self.currency)

    def _same(self, other: Money) -> None:
        if not isinstance(other, Money):
            raise TypeError(f"cannot combine Money with {type(other).__name__}")
        if other.currency != self.currency:
            raise CurrencyMismatch(f"{self.currency} vs {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._same(other)
        return Money(amount_minor=self.amount_minor + other.amount_minor, currency=self.currency)

    def __sub__(self, other: Money) -> Money:
        """Raises ValueError (via validation) if the result would be negative."""
        self._same(other)
        return Money(amount_minor=self.amount_minor - other.amount_minor, currency=self.currency)

    def __lt__(self, other: Money) -> bool:
        self._same(other)
        return self.amount_minor < other.amount_minor

    def __le__(self, other: Money) -> bool:
        self._same(other)
        return self.amount_minor <= other.amount_minor

    def __gt__(self, other: Money) -> bool:
        self._same(other)
        return self.amount_minor > other.amount_minor

    def __ge__(self, other: Money) -> bool:
        self._same(other)
        return self.amount_minor >= other.amount_minor

    def __str__(self) -> str:
        return f"{self.to_paypal_value()} {self.currency}"
