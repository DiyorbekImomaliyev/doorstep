"""RK-01..03: the trained delivery-risk model (XGBoost, calibrated, exact SHAP reasons)."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.ai.risk import FEATURES, MODEL_DIR, RiskFeatures, load_from, load_model
from app.ai.risk_train import (
    average_precision,
    expected_calibration_error,
    fit_platt,
    synthesize,
    train,
    true_logit,
)
from app.checkout import risk as checkout_risk
from app.money import Money
from tests.factories import make_seller
from tuning import engine_config as cfg

ROOT = Path(__file__).resolve().parents[2]
META = json.loads((MODEL_DIR / "risk_v1.meta.json").read_text())


@pytest.fixture(scope="module")
def model():
    m = load_model()
    assert m is not None, "risk model missing: run `make risk-model`"
    return m


def _f(**kw) -> RiskFeatures:
    base = dict(order_value_major=60.0, buyer_country="GB", seller_case_rate=0.02, seller_orders=0)
    return RiskFeatures(**{**base, **kw})


# ---------------------------------------------------------------- RK-01 calibration + report


def test_RK_01_probabilities_are_calibrated_on_fresh_data(model):
    x, y = synthesize(40_000, seed=777)  # never seen in training, validation or test
    p = np.asarray(model.predict_many(x.tolist()))
    assert expected_calibration_error(y, p) <= 0.015
    oracle = average_precision(y, 1 / (1 + np.exp(-true_logit(x))))
    assert average_precision(y, p) >= 0.9 * oracle
    assert average_precision(y, p) > 2 * y.mean()  # far better than a random score


def test_RK_01_report_and_calibration_plot_are_saved():
    report = (ROOT / "evals" / "risk_report.md").read_text()
    m = META["metrics"]
    assert "PR-AUC" in report and "Calibration" in report
    assert f"{m['pr_auc_test']:.3f}" in report
    assert f"{m['ece_test']:.4f}" in report
    assert "risk_calibration.svg" in report
    svg = (ROOT / "evals" / "risk_calibration.svg").read_text()
    assert svg.startswith("<svg") and svg.count("<circle") == len(META["reliability_test"])


def test_RK_01_committed_model_matches_the_tuning_config():
    """A change to tuning/engine_config.py must come with a retrained model (`make risk-model`)."""
    _, meta = train(
        dict(cfg.RISK_MODEL_PARAMS),
        cfg.RISK_MODEL_ROUNDS,
        cfg.RISK_MODEL_TRAIN_ROWS,
        cfg.RISK_MODEL_SEED,
    )
    assert meta["rounds"] == META["rounds"]
    assert meta["metrics"] == META["metrics"]


def test_RK_01_platt_scaling_recovers_known_parameters():
    rng = np.random.default_rng(1)
    margin = rng.normal(-2, 1.5, 50_000)
    y = (rng.random(margin.size) < 1 / (1 + np.exp(-(0.7 * margin + 0.4)))).astype(float)
    a, b = fit_platt(margin, y)
    assert a == pytest.approx(0.7, abs=0.05) and b == pytest.approx(0.4, abs=0.08)


def test_RK_01_average_precision_edge_cases():
    assert average_precision(np.array([0.0, 0.0]), np.array([0.2, 0.1])) == 0.0
    assert average_precision(np.array([1.0, 0.0]), np.array([0.9, 0.1])) == 1.0


# ---------------------------------------------------------------- RK-02 reasons


_IDENT = re.compile(r"\b(" + "|".join(FEATURES) + r")\b")


def test_RK_02_every_score_returns_top_shap_reasons_in_plain_words(model):
    score = model.score(_f())
    assert 1 <= len(score.top_reasons) <= 3
    for r in score.top_reasons:
        assert r.feature in FEATURES
        assert not _IDENT.search(r.text) and "_" not in r.text
        assert r.effect == ("raises" if r.contribution > 0 else "lowers")
        assert r.effect.rstrip("s") in r.text  # "raises"/"raise", "lowers"/"lower"
    mags = [abs(r.contribution) for r in score.top_reasons]
    assert mags == sorted(mags, reverse=True)


def test_RK_02_shap_values_add_up_to_the_score(model):
    import xgboost as xgb

    f = _f(buyer_country="US", method="locker", address_overridden=True)
    dm = xgb.DMatrix(np.asarray([f.vector()], dtype=np.float32), feature_names=list(FEATURES))
    contribs = model.booster.predict(dm, pred_contribs=True)[0]
    margin = model.booster.predict(dm, output_margin=True)[0]
    assert float(contribs.sum()) == pytest.approx(float(margin), abs=1e-4)
    assert model.score(f).p_failure == pytest.approx(model.calibrate(float(margin)), abs=1e-4)


@pytest.mark.parametrize(
    ("kw", "fragment"),
    [
        ({"method": "locker"}, "Locker pickup"),
        ({"order_value_major": 400.0}, "order value of 400.00 raises"),
        ({"address_overridden": True}, "failed the check"),
    ],
)
def test_RK_02_the_strongest_factor_is_named(model, kw, fragment):
    texts = " ".join(r.text for r in model.score(_f(**kw)).top_reasons)
    assert fragment in texts


@settings(max_examples=60, deadline=None)
@given(
    value=st.floats(1, 2000),
    bump=st.floats(1, 500),
    country=st.sampled_from(["GB", "US"]),
    rate=st.floats(0, 0.3),
    orders=st.integers(0, 10_000),
    overridden=st.booleans(),
)
def test_RK_02_monotone_and_bounded_for_any_order(value, bump, country, rate, orders, overridden):
    m = load_model()
    assert m is not None
    kw = dict(
        buyer_country=country,
        seller_case_rate=rate,
        seller_orders=orders,
        address_overridden=overridden,
    )
    home = m.score(RiskFeatures(order_value_major=value, **kw)).p_failure
    dearer = m.score(RiskFeatures(order_value_major=value + bump, **kw)).p_failure
    locker = m.score(RiskFeatures(order_value_major=value, method="locker", **kw)).p_failure
    sig = m.score(RiskFeatures(order_value_major=value, method="signature", **kw)).p_failure
    assert 0.0 <= home <= 1.0
    assert dearer >= home - 1e-4  # a pricier parcel never looks safer
    assert locker <= home + 1e-4 and sig <= home + 1e-4


def test_RK_02_checkout_quote_uses_the_model_reasons(db):
    seller = make_seller(db)
    est = checkout_risk.estimate(
        db, seller.id, Money(amount_minor=6000, currency="GBP"), buyer_country="GB"
    )
    assert est.source == "xgboost:risk_v1"
    assert any("locker" in r for r in est.reasons)


def test_RK_02_checkout_falls_back_to_the_rule_of_thumb(db):
    seller = make_seller(db)
    est = checkout_risk.estimate(
        db, seller.id, Money(amount_minor=6000, currency="GBP"), model=None
    )
    assert est.source == "rule of thumb"
    assert any("adds" in r for r in est.reasons)


def test_RK_02_model_sees_no_personal_data():
    assert set(RiskFeatures.model_fields) == {
        "order_value_major",
        "buyer_country",
        "seller_case_rate",
        "seller_orders",
        "method",
        "address_overridden",
    }


def test_RK_02_missing_or_mismatched_model_is_not_loaded(tmp_path):
    assert load_from(tmp_path) is None
    shutil.copy(MODEL_DIR / "risk_v1.json", tmp_path / "risk_v1.json")
    meta = {**META, "features": ["order_value"]}
    (tmp_path / "risk_v1.meta.json").write_text(json.dumps(meta))
    assert load_from(tmp_path) is None


# ---------------------------------------------------------------- RK-03 model card


def test_RK_03_model_card_states_synthetic_training_data():
    card = (ROOT / "docs" / "MODEL_CARD_risk.md").read_text()
    assert "synthetic" in card.lower()
    assert "Information, not a decision" in card or "never decides" in card
    assert "synthetic" in META["trained_on"]
    for feature in FEATURES:
        assert feature in card
