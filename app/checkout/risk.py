"""Delivery-failure risk at checkout (CK-03, RK-02).

The trained model (app/ai/risk.py: XGBoost, calibrated, exact SHAP reasons) scores the order for
doorstep delivery, the option a safer method would replace. If the model is unavailable, a
transparent rule of thumb runs instead; its coefficients live in tuning/engine_config.py."""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.risk import RiskFeatures, RiskModel, load_model
from app.money import Money
from app.orders.models import Case, Order
from tuning import engine_config as cfg


class RiskEstimate(BaseModel):
    p_failure: float
    reasons: list[str]
    source: str = "rule of thumb"


def seller_case_rate(session: Session, seller_id: str) -> float:
    """The seller's own smoothed rate of orders that ended in a delivery case."""
    orders = session.scalar(select(func.count(Order.id)).where(Order.seller_id == seller_id)) or 0
    cases = session.scalar(select(func.count(Case.id)).where(Case.seller_id == seller_id)) or 0
    return (cases + cfg.RISK_HISTORY_PRIOR_CASES) / (orders + cfg.RISK_HISTORY_PRIOR_ORDERS)


def seller_order_count(session: Session, seller_id: str) -> int:
    return session.scalar(select(func.count(Order.id)).where(Order.seller_id == seller_id)) or 0


def estimate(
    session: Session,
    seller_id: str,
    amount: Money,
    *,
    buyer_country: str = "GB",
    model: RiskModel | bool | None = True,
) -> RiskEstimate:
    """Risk for doorstep delivery. `model=True` loads the trained model; None forces the rule."""
    trained = load_model() if model is True else (model or None)
    history = seller_case_rate(session, seller_id)
    if trained is not None:
        score = trained.score(
            RiskFeatures(
                order_value_major=amount.amount_minor / 10**amount.exponent,
                buyer_country=buyer_country,
                seller_case_rate=min(history, 1.0),
                seller_orders=seller_order_count(session, seller_id),
                method="home",
            )
        )
        return RiskEstimate(
            p_failure=score.p_failure,
            reasons=[r.text for r in score.top_reasons],
            source=score.model,
        )
    major = min(amount.amount_minor / 10**amount.exponent, cfg.RISK_VALUE_CAP_MAJOR)
    value_part = cfg.RISK_PER_MAJOR_UNIT * major
    history_part = cfg.RISK_HISTORY_WEIGHT * history
    p = min(max(cfg.RISK_BASE + value_part + history_part, 0.0), 1.0)
    reasons = [
        f"order value {amount} adds {value_part:.1%}",
        f"this shop's delivery-problem rate ({history:.1%}) adds {history_part:.1%}",
    ]
    return RiskEstimate(p_failure=round(p, 4), reasons=reasons)
