"""EC-01: what Doorstep costs a seller this month, and the problems it settled before a dispute.

Only measured counts and published figures reach the seller: orders this month, addresses fixed
before payment (EC-02), problems settled with no PayPal dispute on the order, PayPal's own dispute
fee and when PayPal waives it, the published cost of a failed delivery (rules/economics/, each
with its source), and Doorstep's price from the same file. Nothing here is a forecast or a
"saved" total: the break-even is the price restated as failed deliveries.
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.checkout.fixes import fixed_addresses
from app.orders.enums import CaseState
from app.orders.models import Case, Dispute, Order, Seller

HOME_CURRENCY = {"GB": "GBP", "US": "USD"}  # the regions Doorstep supports


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Fee(_Strict):
    amount_minor: StrictInt = Field(ge=0)
    source: StrictStr = Field(min_length=1)


class Price(_Strict):
    free_orders_per_month: StrictInt = Field(ge=0)
    per_order_minor: dict[str, StrictInt]
    monthly_cap_minor: dict[str, StrictInt]


class Economics(_Strict):
    id: StrictStr
    kind: StrictStr
    version: StrictStr
    source: StrictStr
    paypal_dispute_fee: dict[str, Fee]
    dispute_fee_waived_below_multiple: StrictInt = Field(ge=1)
    fee_terms_source: dict[str, StrictStr]
    failed_delivery_cost: dict[str, Fee]
    price: Price


def load_economics(rules_dir: Path) -> Economics:
    path = rules_dir / "economics" / "economics_v1.yaml"
    return Economics.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def monthly_price(econ: Economics, orders: int, currency: str) -> int:
    """Free up to the monthly allowance, then a price per order, capped each month."""
    billable = max(0, orders - econ.price.free_orders_per_month)
    return min(
        billable * econ.price.per_order_minor[currency], econ.price.monthly_cap_minor[currency]
    )


def break_even_failures(econ: Economics, orders: int, currency: str) -> int:
    """This month's price as failed deliveries: how many it takes to cost the seller as much."""
    price = monthly_price(econ, orders, currency)
    return math.ceil(price / econ.failed_delivery_cost[currency].amount_minor)


class EconomicsView(BaseModel):
    currency: str
    month: str  # "2026-11"
    orders_this_month: int
    free_orders_per_month: int
    price_minor: int
    per_order_minor: int
    monthly_cap_minor: int
    settled_before_dispute: int
    addresses_fixed: int  # EC-02: this month, failed Doorstep's check and passed once corrected
    dispute_fee_minor: int
    fee_source: str
    fee_waived_below_minor: int  # PayPal charges no standard fee on a claim worth less than this
    fee_terms_source: str
    failed_delivery_cost_minor: int
    failed_delivery_source: str
    break_even_failures: int
    rule: str  # "economics_v1 · 2026-11-09"


def seller_economics(s: Session, seller: Seller, econ: Economics, now: datetime) -> EconomicsView:
    currency = HOME_CURRENCY.get(seller.region, "GBP")
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_ids = list(
        s.scalars(
            select(Order.id).where(Order.seller_id == seller.id, Order.created_at >= month_start)
        )
    )
    orders = len(month_ids)
    disputed = select(Dispute.id).where(Dispute.order_id == Case.order_id).exists()
    settled = int(
        s.scalar(
            select(func.count())
            .select_from(Case)
            .where(
                Case.seller_id == seller.id,
                Case.state == CaseState.SETTLED.value,
                ~disputed,
            )
        )
        or 0
    )
    fee = econ.paypal_dispute_fee[currency]
    return EconomicsView(
        currency=currency,
        month=f"{month_start:%Y-%m}",
        orders_this_month=orders,
        free_orders_per_month=econ.price.free_orders_per_month,
        price_minor=monthly_price(econ, orders, currency),
        per_order_minor=econ.price.per_order_minor[currency],
        monthly_cap_minor=econ.price.monthly_cap_minor[currency],
        settled_before_dispute=settled,
        addresses_fixed=len(fixed_addresses(s, month_ids)),
        dispute_fee_minor=fee.amount_minor,
        fee_source=fee.source,
        fee_waived_below_minor=fee.amount_minor * econ.dispute_fee_waived_below_multiple,
        fee_terms_source=econ.fee_terms_source[currency],
        failed_delivery_cost_minor=econ.failed_delivery_cost[currency].amount_minor,
        failed_delivery_source=econ.failed_delivery_cost[currency].source,
        break_even_failures=break_even_failures(econ, orders, currency),
        rule=f"{econ.id} · {econ.version}",
    )
