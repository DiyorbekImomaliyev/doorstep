"""Synthetic data, training and evaluation for the risk model (RK-01, RK-03).

The synthetic world is seeded from published rates: about 1-2% of
orders have a costly problem in mature shops, US doorstep theft adds roughly 0.5-1 point, and
lockers and signatures prevent most of the theft and "left insecure" failures. A new shop and an
address the buyer kept after it failed the check both raise the rate. These effects are
assumptions, stated in docs/MODEL_CARD_risk.md; production retrains on each seller's outcomes.

Everything here is deterministic for a given seed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.ai.risk import FEATURES, NEW_SHOP_ORDERS, VALUE_CAP_MAJOR

# Ground truth of the synthetic world, in log-odds. Not used by the app at runtime.
TRUTH = {
    "intercept": -3.2,  # a mature UK shop, home delivery, a 40.00 order: about 3.9%
    "log_value": 0.75,  # per unit of ln(value / 40); valuable parcels are targeted more
    "us": 0.45,
    "case_rate": 14.0,  # applied to (case rate - 2%)
    "new_shop": 0.35,
    "locker": -1.3,
    "signature": -0.9,
    "address_overridden": 1.0,
}
TRUTH_VALUE_FLOOR = 1.0

# Monotone constraints, same order as FEATURES: value, us, case rate, new shop, locker,
# signature, overridden address.
MONOTONE = (1, 0, 1, 1, -1, -1, 1)


def true_logit(x: np.ndarray) -> np.ndarray:
    t = TRUTH
    return (
        t["intercept"]
        + t["log_value"] * np.log(np.maximum(x[:, 0], TRUTH_VALUE_FLOOR) / 40.0)
        + t["us"] * x[:, 1]
        + t["case_rate"] * (x[:, 2] - 0.02)
        + t["new_shop"] * x[:, 3]
        + t["locker"] * x[:, 4]
        + t["signature"] * x[:, 5]
        + t["address_overridden"] * x[:, 6]
    )


def synthesize(n: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """n synthetic orders: features in FEATURES order and a 0/1 delivery-failure label."""
    rng = np.random.default_rng(seed)
    value = np.minimum(rng.lognormal(mean=math.log(40), sigma=0.9, size=n), VALUE_CAP_MAJOR)
    us = (rng.random(n) < 0.5).astype(float)
    case_rate = rng.beta(2, 90, size=n)
    orders = rng.lognormal(mean=math.log(150), sigma=1.5, size=n)  # ~9% new shops
    new_shop = (orders < NEW_SHOP_ORDERS).astype(float)
    # Sellers pick signature more often for valuable orders; lockers are a buyer choice.
    p_sig = np.clip(0.05 + value / 1500, 0, 0.6)
    u = rng.random(n)
    signature = (u < p_sig).astype(float)
    locker = ((u >= p_sig) & (u < p_sig + 0.18)).astype(float)
    overridden = (rng.random(n) < 0.05).astype(float)
    x = np.column_stack([value, us, case_rate, new_shop, locker, signature, overridden])
    p = 1 / (1 + np.exp(-true_logit(x)))
    y = (rng.random(n) < p).astype(float)
    return x, y


def fit_platt(margin: np.ndarray, y: np.ndarray, iters: int = 50) -> tuple[float, float]:
    """Platt scaling p = sigmoid(a * margin + b), fitted by Newton's method on log loss."""
    a, b = 1.0, 0.0
    for _ in range(iters):
        z = a * margin + b
        p = 1 / (1 + np.exp(-z))
        g = np.array([np.sum((p - y) * margin), np.sum(p - y)])
        w = p * (1 - p)
        h = np.array(
            [[np.sum(w * margin * margin), np.sum(w * margin)], [np.sum(w * margin), np.sum(w)]]
        ) + 1e-9 * np.eye(2)
        step = np.linalg.solve(h, g)
        a, b = a - float(step[0]), b - float(step[1])
        if float(np.abs(step).max()) < 1e-10:
            break
    return a, b


def average_precision(y: np.ndarray, p: np.ndarray) -> float:
    """PR-AUC as average precision (the step-wise area under the precision-recall curve)."""
    order = np.argsort(-p, kind="stable")
    y_sorted = y[order]
    hits = np.cumsum(y_sorted)
    precision = hits / np.arange(1, len(y) + 1)
    positives = y_sorted.sum()
    return float((precision * y_sorted).sum() / positives) if positives else 0.0


@dataclass(frozen=True)
class CalibrationBin:
    mean_predicted: float
    observed_rate: float
    count: int


def reliability(y: np.ndarray, p: np.ndarray, bins: int = 10) -> list[CalibrationBin]:
    """Equal-count bins of predicted risk: mean prediction vs observed failure rate."""
    order = np.argsort(p, kind="stable")
    out = []
    for chunk in np.array_split(order, bins):
        if len(chunk):
            out.append(CalibrationBin(float(p[chunk].mean()), float(y[chunk].mean()), len(chunk)))
    return out


def expected_calibration_error(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    rows = reliability(y, p, bins)
    n = sum(r.count for r in rows)
    return sum(r.count / n * abs(r.mean_predicted - r.observed_rate) for r in rows)


def brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def train(params: dict[str, Any], rounds: int, n: int, seed: int) -> tuple[Any, dict[str, Any]]:
    """Train on 60%, early-stop and calibrate on 20%, report on the untouched 20%."""
    import xgboost as xgb

    x, y = synthesize(n, seed)
    a, b = int(n * 0.6), int(n * 0.8)
    names = list(FEATURES)
    d_train = xgb.DMatrix(x[:a], label=y[:a], feature_names=names)
    d_val = xgb.DMatrix(x[a:b], label=y[a:b], feature_names=names)
    d_test = xgb.DMatrix(x[b:], feature_names=names)
    full = {
        **params,
        "objective": "binary:logistic",
        "eval_metric": "logloss",
        "monotone_constraints": "(" + ",".join(str(m) for m in MONOTONE) + ")",
        "seed": seed,
        "nthread": 1,
    }
    booster = xgb.train(
        full,
        d_train,
        num_boost_round=rounds,
        evals=[(d_val, "val")],
        early_stopping_rounds=30,
        verbose_eval=False,
    )
    rng = (0, booster.best_iteration + 1)
    platt_a, platt_b = fit_platt(
        booster.predict(d_val, output_margin=True, iteration_range=rng), y[a:b]
    )
    # Keep only the trees up to the best iteration, so the saved model is the evaluated one.
    booster = booster[: booster.best_iteration + 1]
    m_test = booster.predict(d_test, output_margin=True)
    p_test = 1 / (1 + np.exp(-(platt_a * m_test + platt_b)))
    y_test = y[b:]
    true_p = 1 / (1 + np.exp(-true_logit(x[b:])))
    metrics = {
        "n_train": a,
        "n_validation": b - a,
        "n_test": n - b,
        "base_rate_test": round(float(y_test.mean()), 4),
        "pr_auc_test": round(average_precision(y_test, p_test), 4),
        "pr_auc_oracle_test": round(average_precision(y_test, true_p), 4),
        "brier_test": round(brier(y_test, p_test), 5),
        "ece_test": round(expected_calibration_error(y_test, p_test), 4),
        "trees": booster.best_iteration + 1 if hasattr(booster, "best_iteration") else rng[1],
    }
    meta = {
        "version": "risk_v1",
        "features": list(FEATURES),
        "platt": {"a": platt_a, "b": platt_b},
        "trained_on": "synthetic data seeded from published rates (docs/MODEL_CARD_risk.md)",
        "seed": seed,
        "params": {k: v for k, v in full.items() if k != "nthread"},
        "rounds": rng[1],
        "metrics": metrics,
        "reliability_test": [r.__dict__ for r in reliability(y_test, p_test)],
    }
    return booster, meta
