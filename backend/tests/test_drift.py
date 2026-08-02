"""Population stability and calibration drift.

The failure mode a drift monitor has is not being wrong — it is being
*confidently quiet*: reporting a comfortable zero for a feature it never
actually compared. Most of these tests are about the cases where the honest
answer is "not measured" rather than a number.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.drift import (
    MIN_SAMPLE,
    PSI_MODERATE,
    PSI_STABLE,
    _band,
    population_stability_index,
)

RNG = np.random.default_rng(11)


# --------------------------------------------------------------------------
# PSI
# --------------------------------------------------------------------------


def test_the_same_distribution_has_almost_no_drift():
    a = RNG.normal(0, 1, 4000)
    b = RNG.normal(0, 1, 4000)
    psi = population_stability_index(a, b)
    assert psi is not None
    assert psi < PSI_STABLE


def test_a_shifted_distribution_is_detected():
    a = RNG.normal(0, 1, 4000)
    b = RNG.normal(1.2, 1, 4000)
    psi = population_stability_index(a, b)
    assert psi is not None
    assert psi > PSI_MODERATE


def test_a_widened_distribution_is_detected_at_an_identical_mean():
    """Drift is not only a change of mean, and a mean-based check misses this."""
    a = RNG.normal(0, 1, 4000)
    b = RNG.normal(0, 3, 4000)
    assert a.mean() == pytest.approx(b.mean(), abs=0.15)
    psi = population_stability_index(a, b)
    assert psi is not None
    assert psi > PSI_STABLE


def test_psi_grows_with_the_size_of_the_shift():
    reference = RNG.normal(0, 1, 4000)
    small = population_stability_index(reference, RNG.normal(0.3, 1, 4000))
    large = population_stability_index(reference, RNG.normal(1.5, 1, 4000))
    assert small < large


def test_psi_is_finite_when_a_bin_empties_completely():
    """Disjoint supports would give an infinite index without the share floor."""
    psi = population_stability_index(RNG.normal(0, 1, 2000), RNG.normal(50, 1, 2000))
    assert psi is not None
    assert np.isfinite(psi)


# --------------------------------------------------------------------------
# Where the honest answer is "not measured"
# --------------------------------------------------------------------------


def test_too_small_a_sample_reports_nothing_rather_than_a_number():
    small = RNG.normal(0, 1, MIN_SAMPLE - 1)
    assert population_stability_index(small, RNG.normal(0, 1, 4000)) is None
    assert population_stability_index(RNG.normal(0, 1, 4000), small) is None


def test_a_constant_feature_reports_nothing_rather_than_perfect_stability():
    """A zero here would claim a stability that was never measured."""
    constant = np.full(500, 1.0)
    assert population_stability_index(constant, RNG.normal(0, 1, 500)) is None


def test_quantile_bins_catch_a_shift_that_equal_width_bins_would_miss():
    """A feature concentrated in a narrow range still has to be comparable.

    With equal-width bins over this reference, nearly every observation lands
    in one bin and the index reports stability no matter what happened.
    """
    reference = RNG.normal(0, 0.01, 4000)
    shifted = RNG.normal(0.02, 0.01, 4000)
    psi = population_stability_index(reference, shifted)
    assert psi is not None
    assert psi > PSI_STABLE


def test_non_finite_values_are_dropped_rather_than_poisoning_the_index():
    a = RNG.normal(0, 1, 2000)
    b = np.concatenate([RNG.normal(0, 1, 2000), [np.nan, np.inf, -np.inf]])
    psi = population_stability_index(a, b)
    assert psi is not None
    assert np.isfinite(psi)


# --------------------------------------------------------------------------
# Bands
# --------------------------------------------------------------------------


def test_the_bands_are_ordered_and_cover_the_range():
    assert _band(0.0) == "STABLE"
    assert _band(PSI_STABLE - 1e-9) == "STABLE"
    assert _band(PSI_STABLE) == "MODERATE"
    assert _band(PSI_MODERATE - 1e-9) == "MODERATE"
    assert _band(PSI_MODERATE) == "SHIFTED"
    assert _band(10.0) == "SHIFTED"
