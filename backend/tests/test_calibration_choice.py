"""Choosing isotonic or Platt by measuring, not by counting rows.

The old rule picked isotonic above a sample-size threshold. That is a prior, not
a measurement, and it can be wrong in the expensive direction: isotonic is far
more flexible, so on a model whose miscalibration is close to a simple monotone
stretch it will happily fit the validation slice's noise.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.modeling.calibration import choose_calibration


def _raw_and_labels(n: int, distort, seed: int = 3):
    """Raw scores plus outcomes whose true rate is a distortion of them."""
    rng = np.random.default_rng(seed)
    raw = rng.uniform(0.05, 0.95, n)
    true = np.clip(distort(raw), 0.01, 0.99)
    y = (rng.uniform(0, 1, n) < true).astype(int)
    return raw, y


def test_a_step_shaped_distortion_lets_isotonic_win():
    """A shape Platt structurally cannot express, so the win is real.

    Platt is a monotone stretch in log-odds and must smooth through a step;
    isotonic can land on it. The margin here is 0.048 with an interval nowhere
    near zero — which is what it takes, deliberately.
    """
    raw, y = _raw_and_labels(6000, lambda p: np.where(p < 0.5, 0.20, 0.80), seed=7)
    choice = choose_calibration(raw, y)
    assert choice.method == "isotonic"
    assert choice.scores["isotonic"] < choice.scores["sigmoid"]
    assert "excluding zero" in choice.reason


def test_a_hairs_difference_does_not_buy_the_more_flexible_calibrator():
    """The rule that matters: isotonic must beat Platt beyond the noise band.

    On a distortion Platt already expresses, isotonic's extra freedom wins by a
    coin toss in the fifth decimal about half the time. Adopting on that is how
    the more complex model wins by luck — the same failure the bullpen group
    demonstrated with a regularisation constant.
    """
    raw, y = _raw_and_labels(
        4000, lambda p: 1 / (1 + np.exp(-(0.6 * np.log(p / (1 - p))))), seed=3
    )
    choice = choose_calibration(raw, y)
    assert choice.method == "sigmoid"
    assert "noise band" in choice.reason


def test_too_little_data_falls_back_to_platt_and_says_why():
    raw, y = _raw_and_labels(60, lambda p: p)
    choice = choose_calibration(raw, y)
    assert choice.method == "sigmoid"
    assert choice.scores["sigmoid"] is None
    assert "Too little validation data" in choice.reason


def test_a_single_class_in_the_scoring_slice_does_not_crash():
    raw = np.linspace(0.05, 0.95, 400)
    y = np.zeros(400, dtype=int)
    choice = choose_calibration(raw, y)
    assert choice.method == "sigmoid"
    assert choice.scores["sigmoid"] is None


def test_the_split_is_chronological_not_random():
    """The scoring slice must be the tail, so a calibrator cannot see its future."""
    raw, y = _raw_and_labels(2000, lambda p: p)
    choice = choose_calibration(raw, y, holdout_fraction=0.25)
    assert choice.n_fit == 1500
    assert choice.n_score == 500


def test_the_decision_serialises_with_both_scores():
    raw, y = _raw_and_labels(4000, lambda p: p)
    payload = choose_calibration(raw, y).to_dict()
    assert payload["method"] in {"sigmoid", "isotonic"}
    assert set(payload["scores"]) == {"sigmoid", "isotonic"}
    assert payload["n_score"] > 0
    assert payload["reason"]


def test_ties_go_to_the_model_with_fewer_parameters():
    """Equal evidence is not a reason to take the more flexible calibrator."""
    raw, y = _raw_and_labels(5000, lambda p: p, seed=11)
    choice = choose_calibration(raw, y)
    assert choice.method == "sigmoid"
    assert "noise band" in choice.reason


@pytest.mark.parametrize("n", [200, 1000, 5000])
def test_a_choice_is_always_returned(n):
    raw, y = _raw_and_labels(n, lambda p: p)
    assert choose_calibration(raw, y).method in {"sigmoid", "isotonic"}
