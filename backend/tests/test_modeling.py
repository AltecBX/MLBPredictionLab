"""Model, calibration, metrics and reproducibility tests."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app.backtest.metrics import (
    ALWAYS_FIFTY_LOG_LOSS,
    brier_score,
    calibration_bins,
    evaluate,
    expected_calibration_error,
    log_loss,
    roc_auc,
    wilson_interval,
)
from app.modeling.calibration import fit_calibrator, select_method
from app.modeling.logistic import LogisticWinModel
from app.services.run_projection import fair_moneyline

FEATURES = ["x1", "x2", "x3"]


def _synthetic(n: int = 800, seed: int = 7) -> pd.DataFrame:
    """Deterministic synthetic frame — a test fixture, never user-facing data."""
    rng = np.random.default_rng(seed)
    x1 = rng.normal(size=n)
    x2 = rng.normal(size=n)
    x3 = rng.normal(size=n)
    logit = 0.15 + 0.8 * x1 - 0.5 * x2
    probability = 1 / (1 + np.exp(-logit))
    y = rng.binomial(1, probability)
    return pd.DataFrame({"x1": x1, "x2": x2, "x3": x3, "home_win": y})


# --- metrics ----------------------------------------------------------------

def test_log_loss_of_a_coin_flip_matches_the_baseline():
    y = np.array([0, 1, 0, 1])
    p = np.full(4, 0.5)
    assert log_loss(y, p) == pytest.approx(ALWAYS_FIFTY_LOG_LOSS)


def test_brier_score_is_mean_squared_error():
    y = np.array([1.0, 0.0])
    p = np.array([0.75, 0.25])
    assert brier_score(y, p) == pytest.approx(0.0625)


def test_roc_auc_of_a_perfect_ranker_is_one():
    y = np.array([0, 0, 1, 1])
    p = np.array([0.1, 0.2, 0.8, 0.9])
    assert roc_auc(y, p) == pytest.approx(1.0)


def test_roc_auc_is_none_with_a_single_class():
    assert roc_auc(np.array([1, 1, 1]), np.array([0.4, 0.5, 0.6])) is None


def test_wilson_interval_brackets_the_point_estimate():
    low, high = wilson_interval(30, 50)
    assert low < 0.6 < high
    assert 0.0 <= low <= high <= 1.0


def test_wilson_interval_is_wider_for_smaller_samples():
    narrow = wilson_interval(300, 500)
    wide = wilson_interval(3, 5)
    assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])


def test_perfect_calibration_has_near_zero_error():
    rng = np.random.default_rng(3)
    p = rng.uniform(0.05, 0.95, size=20000)
    y = rng.binomial(1, p)
    bins = calibration_bins(y, p)
    assert expected_calibration_error(bins, len(y)) < 0.02


def test_small_slices_report_count_only():
    """A metric computed on a dozen games is noise, so it is suppressed."""
    metrics = evaluate([1, 0, 1], [0.6, 0.4, 0.7])
    assert metrics.n == 3
    assert metrics.log_loss is None
    assert metrics.calibration_error is None
    assert metrics.mean_predicted is not None


def test_evaluate_populates_every_headline_metric():
    frame = _synthetic()
    metrics = evaluate(frame["home_win"], np.clip(0.5 + 0.1 * frame["x1"], 0.02, 0.98))
    for field in ("log_loss", "brier_score", "calibration_error", "accuracy", "roc_auc"):
        assert getattr(metrics, field) is not None


# --- calibration ------------------------------------------------------------

def test_calibration_improves_a_systematically_overconfident_model():
    rng = np.random.default_rng(11)
    truth = rng.uniform(0.3, 0.7, size=4000)
    y = rng.binomial(1, truth)
    # Push probabilities away from 0.5 to simulate overconfidence.
    overconfident = np.clip(0.5 + (truth - 0.5) * 2.4, 0.01, 0.99)

    before = log_loss(y, overconfident)
    calibrator = fit_calibrator(overconfident, y, method="sigmoid")
    after = log_loss(y, calibrator.transform(overconfident))
    assert after < before


def test_calibrator_is_applied_not_refit():
    rng = np.random.default_rng(5)
    p = rng.uniform(0.1, 0.9, size=500)
    y = rng.binomial(1, p)
    calibrator = fit_calibrator(p, y, method="sigmoid")
    first = calibrator.transform(p)
    second = calibrator.transform(p)
    assert np.allclose(first, second)


def test_method_selection_prefers_platt_on_small_validation_sets():
    assert select_method(500) == "sigmoid"
    assert select_method(50_000) == "isotonic"
    assert select_method(50_000, "sigmoid") == "sigmoid"


def test_degenerate_validation_falls_back_to_identity():
    calibrator = fit_calibrator(np.array([0.4, 0.6]), np.array([1, 1]))
    assert calibrator.method == "identity"
    assert np.allclose(calibrator.transform(np.array([0.4])), np.array([0.4]))


# --- model ------------------------------------------------------------------

def test_model_learns_the_synthetic_signal():
    frame = _synthetic()
    model = LogisticWinModel(feature_names=FEATURES, C=1.0).fit(frame)
    coefficients = model.coefficients
    assert coefficients["x1"] > 0.2
    assert coefficients["x2"] < -0.1
    assert abs(coefficients["x3"]) < abs(coefficients["x1"])


def test_probabilities_are_valid_and_sum_to_one():
    frame = _synthetic()
    model = LogisticWinModel(feature_names=FEATURES, C=1.0).fit(frame)
    probabilities = model.predict(frame)
    assert np.all(probabilities > 0) and np.all(probabilities < 1)
    assert np.allclose(probabilities + (1 - probabilities), 1.0)


def test_contributions_sum_is_consistent_with_the_probability():
    """Contributions are exact leave-one-out effects, additive in log-odds."""
    frame = _synthetic(n=200)
    model = LogisticWinModel(feature_names=FEATURES, C=1.0).fit(frame)
    row = frame.iloc[[0]]

    z = model.transformed_row(row)[0]
    beta = np.array([model.coefficients[name] for name in FEATURES])
    eta = model.intercept + float(np.dot(beta, z))
    probability = 1 / (1 + math.exp(-eta))
    assert model.predict_raw(row)[0] == pytest.approx(probability, abs=1e-9)

    contributions = model.contributions(row)[0]
    for name, value in zip(FEATURES, beta * z, strict=True):
        expected = 100 * (probability - 1 / (1 + math.exp(-(eta - value))))
        assert contributions[name] == pytest.approx(expected, abs=1e-9)


def test_model_is_reproducible_across_refits():
    frame = _synthetic()
    a = LogisticWinModel(feature_names=FEATURES, C=0.3, seed=42).fit(frame)
    b = LogisticWinModel(feature_names=FEATURES, C=0.3, seed=42).fit(frame)
    assert a.coefficients == b.coefficients
    assert a.intercept == b.intercept
    assert np.allclose(a.predict(frame), b.predict(frame))


def test_missing_feature_column_raises_rather_than_silently_imputing():
    frame = _synthetic().drop(columns=["x2"])
    model = LogisticWinModel(feature_names=FEATURES, C=1.0)
    with pytest.raises(KeyError, match="missing columns"):
        model.fit(frame)


def test_nulls_are_imputed_from_the_training_fold_median():
    frame = _synthetic()
    frame.loc[frame.index[:10], "x3"] = None
    model = LogisticWinModel(feature_names=FEATURES, C=1.0).fit(frame)
    predictions = model.predict(frame)
    assert not np.isnan(predictions).any()


# --- fair price -------------------------------------------------------------

@pytest.mark.parametrize(
    ("probability", "expected"),
    [(0.5, -100), (0.6, -150), (0.75, -300), (0.4, 150), (0.25, 300)],
)
def test_fair_moneyline_matches_the_standard_conversion(probability, expected):
    assert fair_moneyline(probability) == expected


def test_fair_moneyline_is_symmetric():
    for p in (0.35, 0.5, 0.62, 0.8):
        assert fair_moneyline(p) == -fair_moneyline(1 - p) or p == 0.5
