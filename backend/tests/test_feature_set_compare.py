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
    SetResult,
    _coverage,
    _headline,
    _judge,
)
from app.backtest.metrics import evaluate
from app.modeling.dataset import Dataset


def test_a_clear_improvement_on_every_metric_is_adopted():
    verdict, reading = _judge(NOISE_BAND * 3, 0.002, 0.004)
    assert verdict == "ADOPT"
    assert "earns its place" in reading


def test_an_improvement_that_costs_calibration_is_flagged():
    verdict, reading = _judge(NOISE_BAND * 3, 0.002, -0.004)
    assert verdict == "ADOPT_WITH_CAVEAT"
    assert "wrong way" in reading


def test_a_clear_regression_is_rejected():
    verdict, reading = _judge(-NOISE_BAND * 3, -0.002, -0.004)
    assert verdict == "REJECT"
    assert "measured no is a result" in reading


def test_a_difference_inside_the_noise_band_does_not_earn_a_place():
    """Failing to hurt is not the same as helping."""
    verdict, reading = _judge(NOISE_BAND / 2, 0.02, 0.02)
    assert verdict == "NO_EFFECT"
    assert "earn its place" in reading


def test_the_band_is_symmetric():
    assert _judge(NOISE_BAND, 0.0, 0.0)[0] == "NO_EFFECT"
    assert _judge(-NOISE_BAND, 0.0, 0.0)[0] == "NO_EFFECT"


def test_a_missing_calibration_number_does_not_block_a_verdict():
    verdict, _ = _judge(NOISE_BAND * 3, 0.002, None)
    assert verdict == "ADOPT"


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
        verdict="ADOPT",
        reading="…",
        candidate_coverage={"sc_sp_whiff_pct_diff": 0.81},
    )
    report = comparison.to_dict()
    assert report["n_common_games"] == 880
    assert report["baseline"]["n_features"] < report["candidate"]["n_features"]
    assert report["candidate_coverage"]["sc_sp_whiff_pct_diff"] == 0.81
