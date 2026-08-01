"""Blend arithmetic and the rule that a component must earn its weight."""

from __future__ import annotations

import numpy as np
import pytest

from app.modeling.ensemble import WEIGHT_GRID, blend


def test_zero_weight_returns_the_primary_untouched():
    """Weight 0 is the null hypothesis and must be exactly a no-op."""
    primary = np.array([0.2, 0.5, 0.9])
    secondary = np.array([0.8, 0.1, 0.3])
    assert np.allclose(blend(primary, secondary, 0.0), primary)


def test_weight_one_returns_the_secondary():
    primary = np.array([0.2, 0.5, 0.9])
    secondary = np.array([0.8, 0.1, 0.3])
    assert np.allclose(blend(primary, secondary, 1.0), secondary, atol=1e-6)


def test_the_grid_includes_zero_so_the_null_hypothesis_can_win():
    assert 0.0 in WEIGHT_GRID


def test_blending_is_in_log_odds_not_probability_space():
    """Probability averaging is shrinkage toward .500 wearing an ensemble hat.

    Two models that agree a side is strong should stay strong after blending.
    Averaging probabilities cannot do that; averaging log-odds can.
    """
    primary = np.array([0.90])
    secondary = np.array([0.90])
    blended = blend(primary, secondary, 0.5)
    assert np.allclose(blended, 0.90, atol=1e-6)

    # And the arithmetic mean of the odds, not of the probabilities, is what a
    # disagreement resolves to.
    p, q = np.array([0.80]), np.array([0.20])
    mid = blend(p, q, 0.5)
    assert np.allclose(mid, 0.5, atol=1e-6)  # log-odds +1.386 and -1.386 cancel


@pytest.mark.parametrize("weight", [0.1, 0.25, 0.5, 0.75])
def test_blend_stays_between_its_components(weight):
    rng = np.random.default_rng(7)
    primary = rng.uniform(0.05, 0.95, 200)
    secondary = rng.uniform(0.05, 0.95, 200)
    blended = blend(primary, secondary, weight)
    lower = np.minimum(primary, secondary)
    upper = np.maximum(primary, secondary)
    assert np.all(blended >= lower - 1e-9)
    assert np.all(blended <= upper + 1e-9)


def test_gbdt_exposes_the_same_surface_the_harness_expects():
    """The walk-forward treats both components alike, so they must look alike."""
    from app.modeling.gbdt import GbdtWinModel
    from app.modeling.logistic import LogisticWinModel

    required = {"fit", "fit_calibration", "predict_raw", "predict", "matrix"}
    assert required <= set(dir(GbdtWinModel))
    assert required <= set(dir(LogisticWinModel))


def test_gbdt_defaults_are_constrained_for_a_small_wide_dataset():
    """~9k rows and 42 correlated features overfit trivially without limits."""
    from app.modeling.gbdt import DEFAULT_PARAMS

    assert DEFAULT_PARAMS["max_depth"] <= 4
    assert DEFAULT_PARAMS["learning_rate"] <= 0.05
    assert DEFAULT_PARAMS["min_samples_leaf"] >= 40
    assert DEFAULT_PARAMS["early_stopping"] is True
