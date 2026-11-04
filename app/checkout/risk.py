"""Delivery-failure risk at checkout (CK-03). A transparent rule of thumb, used until the trained
risk model (RK) is available; every coefficient lives in tuning/engine_config.py."""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.money import Money
from app.orders.models import Case, Order
from tuning import engine_config as cfg


class RiskEstimate(BaseModel):
    p_failure: float
    reasons: list[str]


def seller_case_rate(session: Session, seller_id: str) -> float:
    """The seller's own smoothed rate of orders that ended in a delivery case."""
    orders = session.scalar(select(func.count(Order.id)).where(Order.seller_id == seller_id)) or 0
    cases = session.scalar(select(func.count(Case.id)).where(Case.seller_id == seller_id)) or 0
    return (cases + cfg.RISK_HISTORY_PRIOR_CASES) / (orders + cfg.RISK_HISTORY_PRIOR_ORDERS)


def estimate(session: Session, seller_id: str, amount: Money) -> RiskEstimate:
    major = min(amount.amount_minor / 10**amount.exponent, cfg.RISK_VALUE_CAP_MAJOR)
    history = seller_case_rate(session, seller_id)
    value_part = cfg.RISK_PER_MAJOR_UNIT * major
    history_part = cfg.RISK_HISTORY_WEIGHT * history
    p = min(max(cfg.RISK_BASE + value_part + history_part, 0.0), 1.0)
    reasons = [
        f"order value {amount} adds {value_part:.1%}",
        f"this shop's delivery-problem rate ({history:.1%}) adds {history_part:.1%}",
    ]
    return RiskEstimate(p_failure=round(p, 4), reasons=reasons)
