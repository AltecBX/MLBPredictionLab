"""The gate a candidate feature group has to pass.

These test the judgement and the comparison arithmetic. The end-to-end run
against real data is a command, not a test — `compare-feature-sets` — because
the answer depends on the data and is not something to assert in advance.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.backtest.feature_set_compare import (
    NOISE_BAND,
    Comparison,
    PairedDelta,
    SetResult,
    _bootstrap_calibration_delta,
    _coverage,
    _headline,
    _judge,
    _paired_bootstrap,
    _per_game_log_loss,
)
from app.backtest.metrics import evaluate
from app.modeling.dataset import Dataset


def _better(mean: float) -> PairedDelta:
    """An interval that clearly favours the candidate."""
    return PairedDelta(mean, mean * 0.5, mean * 1.5)


def _worse(mean: float) -> PairedDelta:
    return PairedDelta(-mean, -mean * 1.5, -mean * 0.5)


def _inconclusive(mean: float = 0.0005) -> PairedDelta:
    """A point estimate whose interval spans zero."""
    return PairedDelta(mean, -0.01, 0.01)


def test_a_clear_improvement_on_every_metric_is_adopted():
    verdict, reading = _judge(
        _better(NOISE_BAND * 3), _better(0.002), _better(0.004)
    )
    assert verdict == "ADOPT"
    assert "earns its place" in reading


def test_an_improvement_that_costs_calibration_is_flagged():
    verdict, reading = _judge(_better(NOISE_BAND * 3), _better(0.002), _worse(0.004))
    assert verdict == "ADOPT_WITH_CAVEAT"
    assert "Reliability" in reading


def test_a_measurably_worse_log_loss_is_rejected_whatever_else_improves():
    """Log loss has a veto. A better calibration curve does not buy it back."""
    verdict, reading = _judge(_worse(0.01), _better(0.002), _better(0.05))
    assert verdict == "REJECT"
    assert "measured no is a result" in reading


def test_a_point_estimate_inside_its_own_interval_is_not_an_improvement():
    """The failure a fixed noise band alone would produce.

    A Δ log loss above the band but with an interval spanning zero is a sample
    that cannot tell the two models apart, and adopting on it would be adopting
    on noise.
    """
    verdict, _ = _judge(
        PairedDelta(NOISE_BAND * 3, -0.004, 0.01), _inconclusive(), _inconclusive()
    )
    assert verdict == "NO_EFFECT"


def test_a_real_but_trivial_improvement_does_not_change_the_model():
    """Distinguishable from zero and too small to matter is still not a reason."""
    tiny = NOISE_BAND / 3
    verdict, _ = _judge(
        PairedDelta(tiny, tiny * 0.5, tiny * 1.5), _inconclusive(), _inconclusive()
    )
    assert verdict == "NO_EFFECT"


def test_calibration_alone_can_carry_a_group():
    """BACKTEST_PLAN.md § Phase 2A: reliability wins when the two disagree."""
    verdict, reading = _judge(_inconclusive(), _inconclusive(), _better(0.02))
    assert verdict == "ADOPT_ON_CALIBRATION"
    assert "reliability wins" in reading.lower()


def test_calibration_cannot_carry_a_group_past_a_worse_brier_score():
    verdict, _ = _judge(_inconclusive(), _worse(0.01), _better(0.02))
    assert verdict == "NO_EFFECT"


def test_nothing_moving_is_no_effect():
    verdict, reading = _judge(_inconclusive(), _inconclusive(), _inconclusive())
    assert verdict == "NO_EFFECT"
    assert "earn its place" in reading


# --------------------------------------------------------------------------
# Paired statistics
# --------------------------------------------------------------------------


def test_per_game_log_loss_matches_the_aggregate():
    actual = np.array([1, 0, 1, 1, 0])
    prob = np.array([0.7, 0.3, 0.6, 0.9, 0.2])
    from app.backtest.metrics import log_loss

    assert _per_game_log_loss(actual, prob).mean() == pytest.approx(
        log_loss(actual, prob)
    )


def test_per_game_log_loss_survives_a_confident_miss():
    """A probability of exactly 0 on an event that happened must not be inf."""
    values = _per_game_log_loss(np.array([1, 0]), np.array([0.0, 1.0]))
    assert np.isfinite(values).all()
    assert (values > 20).all()


def test_a_bootstrap_of_identical_models_cannot_tell_them_apart():
    rng = np.random.default_rng(3)
    deltas = rng.normal(0.0, 0.5, 2000)
    interval = _paired_bootstrap(deltas)
    assert not interval.is_distinguishable_from_zero
    assert interval.ci_low < 0 < interval.ci_high


def test_a_bootstrap_of_a_consistent_edge_finds_it():
    rng = np.random.default_rng(3)
    deltas = rng.normal(0.05, 0.2, 2000)
    interval = _paired_bootstrap(deltas)
    assert interval.is_distinguishable_from_zero
    assert interval.favours_candidate


def test_an_empty_comparison_produces_a_zero_interval():
    interval = _paired_bootstrap(np.array([]))
    assert (interval.mean, interval.ci_low, interval.ci_high) == (0.0, 0.0, 0.0)
    assert not interval.is_distinguishable_from_zero


def test_the_calibration_bootstrap_finds_a_genuinely_better_curve():
    """One model states the truth; the other is systematically overconfident."""
    rng = np.random.default_rng(5)
    honest = rng.uniform(0.25, 0.75, 1500)
    actual = (rng.uniform(size=1500) < honest).astype(int)
    overconfident = np.clip((honest - 0.5) * 2.2 + 0.5, 0.02, 0.98)
    interval = _bootstrap_calibration_delta(actual, overconfident, honest)
    assert interval.favours_candidate


def test_the_calibration_bootstrap_is_deterministic():
    rng = np.random.default_rng(7)
    prob = rng.uniform(0.3, 0.7, 400)
    actual = (rng.uniform(size=400) < prob).astype(int)
    other = np.clip(prob + 0.05, 0.01, 0.99)
    first = _bootstrap_calibration_delta(actual, other, prob)
    second = _bootstrap_calibration_delta(actual, other, prob)
    assert first.to_dict() == second.to_dict()


def test_coverage_reports_how_often_a_candidate_feature_had_a_value():
    frame = pd.DataFrame(
        {
            "home_win": [1.0, 0.0, 1.0, 0.0],
            "new_feature": [0.1, None, 0.3, None],
            "always_there": [1.0, 2.0, 3.0, 4.0],
        }
    )
    dataset = Dataset(frame, ["new_feature", "always_there"], "fs_test", "T_MINUS_3H")
    coverage = _coverage(dataset, ["new_feature", "always_there"])
    assert coverage == {"new_feature": 0.5, "always_there": 1.0}


def test_coverage_of_a_feature_that_is_never_present_is_zero_not_absent():
    """A verdict on a group that was never measurable is about coverage."""
    frame = pd.DataFrame({"home_win": [1.0, 0.0], "never": [None, None]})
    dataset = Dataset(frame, ["never"], "fs_test", "T_MINUS_3H")
    assert _coverage(dataset, ["never"]) == {"never": 0.0}


def test_the_report_drops_the_per_bin_calibration_arrays():
    rng = np.random.default_rng(11)
    probabilities = rng.uniform(0.2, 0.8, 400)
    actual = (rng.uniform(size=400) < probabilities).astype(int)
    metrics = evaluate(actual, probabilities).to_dict()
    assert metrics["bins"]
    headline = _headline(metrics)
    assert "bins" not in headline
    assert headline["log_loss"] == pytest.approx(metrics["log_loss"])


def test_the_report_states_which_games_both_models_predicted():
    """A comparison on different game sets is not a comparison."""
    comparison = Comparison(
        baseline=SetResult("fs_v1", 42, {"log_loss": 0.66}, 1000),
        candidate=SetResult("fs_v2", 51, {"log_loss": 0.65}, 900),
        n_games=900,
        n_common_games=880,
        delta_log_loss=0.01,
        delta_brier=0.002,
        delta_ece=0.001,
        delta_accuracy=0.003,
        log_loss_interval=PairedDelta(0.01, 0.004, 0.016),
        brier_interval=PairedDelta(0.002, 0.001, 0.003),
        calibration_interval=PairedDelta(0.001, -0.002, 0.004),
        verdict="ADOPT",
        reading="…",
        candidate_coverage={"sc_sp_whiff_pct_diff": 0.81},
    )
    report = comparison.to_dict()
    assert report["n_common_games"] == 880
    assert report["baseline"]["n_features"] < report["candidate"]["n_features"]
    assert report["candidate_coverage"]["sc_sp_whiff_pct_diff"] == 0.81
    assert report["paired_95_ci"]["log_loss"]["ci_low"] == 0.004
