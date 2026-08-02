"""Why a prediction moved.

The claim this module makes is stronger than most explanation code makes: the
decomposition is *exact*, not attributed. These tests are what makes that claim
checkable — most of them exist to catch a residual appearing where the
arithmetic says there cannot be one.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app.modeling.logistic import LogisticWinModel
from app.services.timeline import explain_change

FEATURES = ["elo_diff", "off_runs_per_game_w30_diff", "sp_fip_season_diff"]


class _Snapshot:
    """A Prediction, reduced to the fields the decomposition reads."""

    def __init__(self, served, raw, calibrated, simulation, features, weight=0.5):
        self.home_win_prob = served
        self.home_win_prob_uncalibrated = raw
        self.component_probs = {"logistic_calibrated": calibrated}
        if simulation is not None:
            self.component_probs["simulation"] = simulation
        self.feature_snapshot = {
            "features": features,
            "blend": {"weight_on_simulation": weight, "is_blended": simulation is not None},
        }
        self.as_of = datetime(2025, 6, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def model() -> LogisticWinModel:
    rng = np.random.default_rng(3)
    n = 600
    frame = pd.DataFrame(
        {name: rng.normal(0, 1, n) for name in FEATURES}
    )
    logit = 0.8 * frame["elo_diff"] - 0.4 * frame["sp_fip_season_diff"]
    frame["home_win"] = (rng.uniform(0, 1, n) < 1 / (1 + np.exp(-logit))).astype(int)
    m = LogisticWinModel(feature_names=FEATURES, C=1.0)
    m.fit(frame)
    m.fit_calibration(frame)
    return m


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _blend(a: float, b: float, w: float) -> float:
    la, lb = math.log(a / (1 - a)), math.log(b / (1 - b))
    return _sigmoid((1 - w) * la + w * lb)


def _pair(model, before_values, now_values, sim_before=0.50, sim_now=0.50, w=0.5):
    """Build two snapshots that are internally consistent with the model."""
    out = []
    for values, sim in ((before_values, sim_before), (now_values, sim_now)):
        frame = pd.DataFrame([values])
        raw = float(model.predict_raw(frame)[0])
        cal = float(model.predict(frame)[0])
        served = _blend(cal, sim, w) if sim is not None else cal
        out.append(_Snapshot(served, raw, cal, sim, values, weight=w if sim else 0.0))
    return out[0], out[1]


# --------------------------------------------------------------------------
# The exactness claim
# --------------------------------------------------------------------------


def test_the_stages_sum_to_the_move_with_no_residual(model):
    """The whole point. If this fails the decomposition is a story, not a split."""
    before, now = _pair(
        model,
        {"elo_diff": 0.2, "off_runs_per_game_w30_diff": -0.1, "sp_fip_season_diff": 0.3},
        {"elo_diff": 0.9, "off_runs_per_game_w30_diff": 0.4, "sp_fip_season_diff": -0.2},
        sim_before=0.48, sim_now=0.56,
    )
    result = explain_change(now, before, model)
    assert result.residual_log_odds == pytest.approx(0.0, abs=1e-9)
    assert (
        result.features_log_odds
        + result.calibration_log_odds
        + result.simulation_log_odds
    ) == pytest.approx(result.total_log_odds, abs=1e-9)


def test_per_feature_log_odds_deltas_sum_to_the_feature_stage(model):
    """Each driver is a real share of the linear model's own movement."""
    before, now = _pair(
        model,
        {"elo_diff": 0.0, "off_runs_per_game_w30_diff": 0.0, "sp_fip_season_diff": 0.0},
        {"elo_diff": 1.1, "off_runs_per_game_w30_diff": -0.7, "sp_fip_season_diff": 0.5},
    )
    result = explain_change(now, before, model, limit=99)
    summed = sum(d.log_odds_delta for d in result.drivers)
    # Drivers are contributions to the SERVED log-odds, so they reconstruct the
    # feature stage directly rather than needing to be rescaled by the reader.
    assert summed == pytest.approx(result.features_log_odds, abs=1e-9)


def test_a_feature_that_did_not_move_is_not_a_driver(model):
    before, now = _pair(
        model,
        {"elo_diff": 0.3, "off_runs_per_game_w30_diff": 0.2, "sp_fip_season_diff": 0.1},
        {"elo_diff": 0.9, "off_runs_per_game_w30_diff": 0.2, "sp_fip_season_diff": 0.1},
    )
    keys = {d.feature_key for d in explain_change(now, before, model, limit=99).drivers}
    assert keys == {"elo_diff"}


def test_drivers_are_ranked_by_magnitude_not_sign(model):
    before, now = _pair(
        model,
        {"elo_diff": 0.0, "off_runs_per_game_w30_diff": 0.0, "sp_fip_season_diff": 0.0},
        {"elo_diff": 0.4, "off_runs_per_game_w30_diff": 0.1, "sp_fip_season_diff": -1.5},
    )
    drivers = explain_change(now, before, model, limit=99).drivers
    magnitudes = [abs(d.log_odds_delta) for d in drivers]
    assert magnitudes == sorted(magnitudes, reverse=True)


# --------------------------------------------------------------------------
# The stages are kept apart
# --------------------------------------------------------------------------


def test_a_pure_simulation_move_is_not_attributed_to_features(model):
    """Features identical, simulation changed its mind. Nothing is a driver."""
    values = {"elo_diff": 0.4, "off_runs_per_game_w30_diff": 0.1, "sp_fip_season_diff": 0.0}
    before, now = _pair(model, values, values, sim_before=0.45, sim_now=0.62)
    result = explain_change(now, before, model)
    assert result.drivers == []
    assert result.features_log_odds == pytest.approx(0.0, abs=1e-9)
    assert result.simulation_log_odds == pytest.approx(result.total_log_odds, abs=1e-9)


def test_a_pure_feature_move_leaves_the_simulation_term_at_zero(model):
    before, now = _pair(
        model,
        {"elo_diff": 0.1, "off_runs_per_game_w30_diff": 0.0, "sp_fip_season_diff": 0.0},
        {"elo_diff": 0.8, "off_runs_per_game_w30_diff": 0.0, "sp_fip_season_diff": 0.0},
        sim_before=0.51, sim_now=0.51,
    )
    result = explain_change(now, before, model)
    assert result.simulation_log_odds == pytest.approx(0.0, abs=1e-9)
    assert result.drivers[0].feature_key == "elo_diff"


def test_a_simulation_appearing_is_reported_as_a_switch_not_a_movement(model):
    """A blend that starts existing is a structural change, and says so."""
    values = {"elo_diff": 0.4, "off_runs_per_game_w30_diff": 0.0, "sp_fip_season_diff": 0.0}
    before, _ = _pair(model, values, values, sim_before=None, sim_now=None)
    _, now = _pair(model, values, values, sim_before=0.60, sim_now=0.60)
    result = explain_change(now, before, model)
    assert result.simulation_note is not None
    assert "became available" in result.simulation_note
    assert result.residual_log_odds == pytest.approx(0.0, abs=1e-9)


def test_a_withheld_simulation_is_also_reported(model):
    values = {"elo_diff": 0.4, "off_runs_per_game_w30_diff": 0.0, "sp_fip_season_diff": 0.0}
    _, before = _pair(model, values, values, sim_before=0.60, sim_now=0.60)
    now, _ = _pair(model, values, values, sim_before=None, sim_now=None)
    result = explain_change(now, before, model)
    assert result.simulation_note is not None
    assert "withheld" in result.simulation_note


# --------------------------------------------------------------------------
# Probability points are a presentation of the log-odds split
# --------------------------------------------------------------------------


def test_contribution_pp_is_the_feature_stages_share_of_the_move(model):
    """The pp column is the exact log-odds split rescaled, so it must add up.

    Not to the whole move: a Platt calibrator rescales the log-odds, so part of
    any move belongs to the calibration stage even when the simulation holds
    still. The drivers reconstruct the feature stage's share and nothing more,
    which is the point of separating the stages at all.
    """
    before, now = _pair(
        model,
        {"elo_diff": -0.5, "off_runs_per_game_w30_diff": 0.2, "sp_fip_season_diff": 0.4},
        {"elo_diff": 1.0, "off_runs_per_game_w30_diff": -0.3, "sp_fip_season_diff": -0.1},
        sim_before=0.5, sim_now=0.5,
    )
    result = explain_change(now, before, model, limit=99)
    share = result.features_log_odds / result.total_log_odds
    assert sum(d.contribution_pp for d in result.drivers) == pytest.approx(
        result.move_pp * share, abs=1e-6
    )
    # And the calibrator really is doing something, or this asserts nothing.
    assert abs(result.calibration_log_odds) > 1e-6


def test_no_move_produces_no_invented_contributions(model):
    """Dividing by a zero move would turn rounding into a driver."""
    values = {"elo_diff": 0.3, "off_runs_per_game_w30_diff": 0.1, "sp_fip_season_diff": 0.2}
    before, now = _pair(model, values, values, sim_before=0.5, sim_now=0.5)
    result = explain_change(now, before, model, limit=99)
    assert result.move_pp == pytest.approx(0.0, abs=1e-9)
    assert all(d.contribution_pp == pytest.approx(0.0) for d in result.drivers)


# --------------------------------------------------------------------------
# Edges
# --------------------------------------------------------------------------


def test_no_previous_prediction_is_not_a_change(model):
    values = {"elo_diff": 0.3, "off_runs_per_game_w30_diff": 0.1, "sp_fip_season_diff": 0.2}
    _, now = _pair(model, values, values)
    result = explain_change(now, None, model)
    assert result.has_previous is False
    assert result.drivers == []


def test_the_limit_caps_drivers_without_changing_the_stage_totals(model):
    before, now = _pair(
        model,
        {"elo_diff": 0.0, "off_runs_per_game_w30_diff": 0.0, "sp_fip_season_diff": 0.0},
        {"elo_diff": 0.6, "off_runs_per_game_w30_diff": 0.5, "sp_fip_season_diff": 0.4},
    )
    capped = explain_change(now, before, model, limit=1)
    full = explain_change(now, before, model, limit=99)
    assert len(capped.drivers) == 1
    assert len(full.drivers) == 3
    assert capped.features_log_odds == pytest.approx(full.features_log_odds)
    assert capped.residual_log_odds == pytest.approx(0.0, abs=1e-9)


def test_the_move_is_reported_in_points_of_the_served_probability(model):
    values_before = {"elo_diff": 0.0, "off_runs_per_game_w30_diff": 0.0, "sp_fip_season_diff": 0.0}
    values_now = {"elo_diff": 1.4, "off_runs_per_game_w30_diff": 0.0, "sp_fip_season_diff": 0.0}
    before, now = _pair(model, values_before, values_now, sim_before=0.5, sim_now=0.5)
    result = explain_change(now, before, model)
    expected = (float(now.home_win_prob) - float(before.home_win_prob)) * 100
    assert result.move_pp == pytest.approx(expected, abs=1e-9)


def test_elapsed_snapshots_do_not_need_to_be_adjacent_in_time(model):
    """The decomposition is between two snapshots, not two consecutive ones."""
    values_before = {"elo_diff": 0.1, "off_runs_per_game_w30_diff": 0.0, "sp_fip_season_diff": 0.0}
    values_now = {"elo_diff": 0.9, "off_runs_per_game_w30_diff": 0.0, "sp_fip_season_diff": 0.0}
    before, now = _pair(model, values_before, values_now)
    before.as_of = now.as_of - timedelta(days=3)
    result = explain_change(now, before, model)
    assert result.residual_log_odds == pytest.approx(0.0, abs=1e-9)
