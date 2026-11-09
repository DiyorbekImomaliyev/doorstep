"""Delivery-failure risk model (RK-01..03): XGBoost + exact TreeSHAP reasons.

- The model is a small gradient-boosted tree ensemble with monotone constraints (a higher order
  value or a worse shop history can never lower the risk; a locker or a signature can never
  raise it). It is trained by `scripts/train_risk.py` on synthetic data seeded from published
  rates (see docs/MODEL_CARD_risk.md) and committed as `app/ai/models/risk_v1.json`.
- Scores are calibrated with Platt scaling fitted on a validation split.
- Reasons are exact Shapley values from XGBoost's own TreeSHAP (`pred_contribs=True`), turned
  into plain words. No `shap` package is needed.
- Inputs hold no personal data: no name, address or postcode, only order value, buyer country,
  delivery method, the address-check outcome and the shop's own history.
- No model file, or XGBoost missing: `load_model()` returns None and checkout falls back to the
  rule of thumb in app/checkout/risk.py.
"""

from __future__ import annotations

import json
import logging
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

log = logging.getLogger("doorstep.risk")

MODEL_DIR = Path(__file__).parent / "models"
MODEL_NAME = "risk_v1"

FEATURES: tuple[str, ...] = (
    "order_value",
    "buyer_in_us",
    "seller_case_rate",
    "new_shop",
    "method_locker",
    "method_signature",
    "address_overridden",
)

VALUE_CAP_MAJOR = 1000.0
NEW_SHOP_ORDERS = 20


class RiskFeatures(BaseModel):
    """What the model may see. Minimum data: nothing that identifies the buyer."""

    order_value_major: float = Field(ge=0)
    buyer_country: str
    seller_case_rate: float = Field(ge=0, le=1)
    seller_orders: int = Field(ge=0)
    method: Literal["home", "locker", "signature"] = "home"
    address_overridden: bool = False

    def vector(self) -> list[float]:
        return [
            min(self.order_value_major, VALUE_CAP_MAJOR),
            1.0 if self.buyer_country.upper() == "US" else 0.0,
            self.seller_case_rate,
            1.0 if self.seller_orders < NEW_SHOP_ORDERS else 0.0,
            1.0 if self.method == "locker" else 0.0,
            1.0 if self.method == "signature" else 0.0,
            1.0 if self.address_overridden else 0.0,
        ]


class RiskReason(BaseModel):
    feature: str
    effect: Literal["raises", "lowers"]
    contribution: float  # log-odds, exact TreeSHAP value
    text: str


class RiskScore(BaseModel):
    p_failure: float
    top_reasons: list[RiskReason]
    model: str


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def reason_text(feature: str, value: float, raises: bool, f: RiskFeatures) -> str:
    """Plain words for one SHAP contribution (RK-02). Never a feature identifier."""
    if feature == "order_value":
        amount = f"{f.order_value_major:,.2f}"
        return (
            f"An order value of {amount} raises the risk"
            if raises
            else f"A lower order value ({amount}) lowers the risk"
        )
    if feature == "buyer_in_us":
        if value:
            return "US doorstep delivery (more parcel theft) raises the risk"
        return "UK delivery (fewer doorstep thefts) lowers the risk"
    if feature == "seller_case_rate":
        rate = f"{f.seller_case_rate:.1%}"
        return (
            f"This shop's past delivery problems ({rate}) raise the risk"
            if raises
            else f"This shop's low rate of delivery problems ({rate}) lowers the risk"
        )
    if feature == "new_shop":
        if value:
            return "A new shop with little delivery history raises the risk"
        return "This shop's track record lowers the risk"
    if feature == "method_locker":
        if value:
            return "Locker pickup (the buyer's code proves collection) lowers the risk"
        return "Doorstep delivery instead of a locker raises the risk"
    if feature == "method_signature":
        if value:
            return "A signature on delivery lowers the risk"
        return "No signature on delivery raises the risk"
    if feature == "address_overridden":
        if value:
            return "The buyer kept an address that failed the check, which raises the risk"
        return "No address problem flagged, which lowers the risk"
    raise ValueError(f"unknown feature {feature}")  # pragma: no cover - FEATURES is closed


class RiskModel:
    """A trained booster plus its Platt calibration. Thread-safe for prediction."""

    def __init__(self, booster: Any, platt_a: float, platt_b: float, version: str) -> None:
        self.booster = booster
        self.platt_a = platt_a
        self.platt_b = platt_b
        self.version = version

    def calibrate(self, margin: float) -> float:
        return _sigmoid(self.platt_a * margin + self.platt_b)

    def margins(self, rows: list[list[float]]) -> list[float]:
        import numpy as np
        import xgboost as xgb

        dm = xgb.DMatrix(np.asarray(rows, dtype=np.float32), feature_names=list(FEATURES))
        return [float(m) for m in self.booster.predict(dm, output_margin=True)]

    def predict_many(self, rows: list[list[float]]) -> list[float]:
        return [self.calibrate(m) for m in self.margins(rows)]

    def score(self, f: RiskFeatures, *, top: int = 3) -> RiskScore:
        import numpy as np
        import xgboost as xgb

        x = f.vector()
        dm = xgb.DMatrix(np.asarray([x], dtype=np.float32), feature_names=list(FEATURES))
        contribs = [float(c) for c in self.booster.predict(dm, pred_contribs=True)[0]]
        margin = sum(contribs)  # TreeSHAP is exact: features + bias == the raw margin
        shap = list(zip(FEATURES, x, contribs[:-1], strict=True))
        shap.sort(key=lambda t: abs(t[2]), reverse=True)
        reasons = [
            RiskReason(
                feature=name,
                effect="raises" if c > 0 else "lowers",
                contribution=round(c, 4),
                text=reason_text(name, value, c > 0, f),
            )
            for name, value, c in shap[:top]
            if abs(c) > 1e-6
        ]
        return RiskScore(
            p_failure=round(self.calibrate(margin), 4),
            top_reasons=reasons,
            model=f"xgboost:{self.version}",
        )


def save_meta(path: Path, meta: dict[str, Any]) -> None:
    path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_from(directory: Path, name: str = MODEL_NAME) -> RiskModel | None:
    model_path = directory / f"{name}.json"
    meta_path = directory / f"{name}.meta.json"
    if not model_path.exists() or not meta_path.exists():
        return None
    try:
        import xgboost as xgb
    except ImportError:  # pragma: no cover - xgboost is a runtime dependency
        log.warning("xgboost not installed; using the rule-of-thumb risk")
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if tuple(meta.get("features", ())) != FEATURES:
        log.warning("risk model features do not match the code; using the rule of thumb")
        return None
    booster = xgb.Booster()
    booster.load_model(str(model_path))
    booster.set_param({"nthread": 1})
    platt = meta["platt"]
    return RiskModel(booster, float(platt["a"]), float(platt["b"]), str(meta["version"]))


@lru_cache(maxsize=1)
def load_model() -> RiskModel | None:
    return load_from(MODEL_DIR)
