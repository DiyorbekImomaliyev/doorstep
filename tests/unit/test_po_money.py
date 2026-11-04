"""PO-10 — money is integer minor units + ISO code, GBP and USD only, never floats."""

from __future__ import annotations

import ast
import re

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from app.money import Currency, CurrencyMismatch, Money, UnsupportedCurrency
from app.orders.schemas import CheckoutRequest
from tests.conftest import ROOT


def gbp(minor: int) -> Money:
    return Money(amount_minor=minor, currency="GBP")


@pytest.mark.parametrize("bad", [50.0, 50.5, "50", None])
def test_PO_10_money_rejects_non_integer_amounts(bad):
    with pytest.raises(ValidationError):
        Money(amount_minor=bad, currency="GBP")  # type: ignore[arg-type]


def test_PO_10_money_rejects_negative_amounts():
    with pytest.raises(ValidationError):
        gbp(-1)


@pytest.mark.parametrize("code", ["EUR", "JPY", "gbp", ""])
def test_PO_10_unsupported_currency_rejected(code):
    with pytest.raises((ValidationError, UnsupportedCurrency)):
        Money(amount_minor=100, currency=code)


def test_PO_10_checkout_rejects_unsupported_currency():
    with pytest.raises(ValidationError):
        CheckoutRequest(
            seller_id="s1",
            amount={"amount_minor": 100, "currency": "EUR"},  # type: ignore[arg-type]
            shipping_method="home",
        )  # type: ignore[arg-type]
    ok = CheckoutRequest(seller_id="s1", amount=gbp(100), shipping_method="home")  # type: ignore[arg-type]
    assert ok.amount.currency == Currency.GBP


def test_PO_10_cross_currency_compare_or_sum_raises():
    usd = Money(amount_minor=100, currency="USD")
    with pytest.raises(CurrencyMismatch):
        _ = gbp(100) + usd
    with pytest.raises(CurrencyMismatch):
        _ = gbp(100) - usd
    with pytest.raises(CurrencyMismatch):
        _ = gbp(100) < usd
    with pytest.raises(CurrencyMismatch):
        _ = gbp(100) >= usd
    assert gbp(100) + gbp(200) == gbp(300)
    assert gbp(300) - gbp(100) == gbp(200)
    assert gbp(100) <= gbp(100) < gbp(101)


@given(minor=st.integers(min_value=0, max_value=10**10), currency=st.sampled_from(["GBP", "USD"]))
def test_PO_10_paypal_decimal_round_trip_exact(minor, currency):
    m = Money(amount_minor=minor, currency=currency)
    value = m.to_paypal_value()
    assert re.fullmatch(r"\d+\.\d{2}", value)
    assert Money.from_paypal(value, currency) == m


@pytest.mark.parametrize(
    ("value", "minor"), [("60.00", 6000), ("10.5", 1050), ("0.01", 1), ("1000", 100000)]
)
def test_PO_10_from_paypal_parses_exactly(value, minor):
    assert Money.from_paypal(value, "GBP") == gbp(minor)


@pytest.mark.parametrize("value", ["10.005", "-1.00", "abc", "1e3", ""])
def test_PO_10_from_paypal_rejects_inexact_values(value):
    with pytest.raises(ValueError):
        Money.from_paypal(value, "GBP")


MONEY_PATH = [
    ROOT / "app" / "money.py",
    *sorted((ROOT / "app" / "policy").rglob("*.py")),
    ROOT / "app" / "paypal_client" / "writes" / "money.py",
]


@pytest.mark.parametrize("path", MONEY_PATH, ids=lambda p: str(p.relative_to(ROOT)))
def test_PO_10_no_float_in_money_path(path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        assert not (isinstance(node, ast.Name) and node.id == "float"), f"{path}:{node.lineno}"
        assert not (isinstance(node, ast.Constant) and isinstance(node.value, float)), (
            f"{path}:{node.lineno}"
        )
