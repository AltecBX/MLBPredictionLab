"""Walk-forward, slicing, ablation and sanity-gate tests."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from app.backtest.ablation import GATES, NOISE_BAND, group_members, sanity_flags
from app.backtest.metrics import evaluate
from app.backtest.slices import PROBABILITY_BANDS, compute_slices
from app.backtest.walkforward import (
    collect_predictions,
    importance_stability,
    make_steps,
    run_walk_forward,
)
from app.modeling.dataset import Dataset

FEATURES = ["f1", "f2", "f3", "sp_a", "bp_b", "sched_c"]


def _dataset(days: int = 420, seed: int = 3) -> Dataset:
    """Chronological synthetic dataset — a test fixture, not user-facing data."""
    rng = np.random.default_rng(seed)
    rows = []
    start = date(2024, 4, 1)
    for day in range(days):
        for _ in range(6):
            f1, f2, f3 = rng.normal(size=3)
            sp_a, bp_b, sched_c = rng.normal(size=3)
            logit = 0.1 + 0.6 * f1 - 0.4 * f2 + 0.3 * sp_a
            probability = 1 / (1 + np.exp(-logit))
            official = start + timedelta(days=day)
            rows.append(
                {
                    "game_id": len(rows) + 1,
                    "as_of": pd.Timestamp(official, tz="UTC"),
                    "official_date": official,
                    "season": official.year,
                    "month": official.month,
                    "home_team_id": 1,
                    "away_team_id": 2,
                    "completeness": 1.0,
                    "n_missing": 0,
                    "home_starter_known": True,
                    "away_starter_known": True,
                    "lineup_confirmed": False,
                    "starter_quality_index": float(rng.normal(4, 0.8)),
                    "home_win": int(rng.binomial(1, probability)),
                    "f1": f1, "f2": f2, "f3": f3,
                    "sp_a": sp_a, "bp_b": bp_b, "sched_c": sched_c,
                }
            )
    return Dataset(pd.DataFrame(rows), FEATURES, "fs_test", "T_MINUS_3H")


# --- splitting --------------------------------------------------------------

def test_steps_are_contiguous_and_never_overlap_training():
    dataset = _dataset(days=200)
    steps = make_steps(dataset.labelled, step_days=30, validation_days=20)
    assert steps
    for step in steps:
        assert step.train_end < step.test_start
        assert step.train_start <= step.train_end
    for earlier, later in zip(steps, steps[1:], strict=False):
        assert earlier.test_end + timedelta(days=1) == later.test_start


def test_no_test_game_appears_in_its_own_training_window():
    dataset = _dataset(days=300)
    steps = make_steps(dataset.labelled, step_days=30)
    frame = dataset.labelled
    for step in steps:
        train_ids = set(frame[frame["official_date"] <= step.train_end]["game_id"])
        test_ids = set(
            frame[
                (frame["official_date"] >= step.test_start)
                & (frame["official_date"] <= step.test_end)
            ]["game_id"]
        )
        assert not train_ids & test_ids


def test_steps_below_the_minimum_training_volume_are_skipped_not_stubbed():
    dataset = _dataset(days=120)
    steps = make_steps(dataset.labelled, step_days=30)
    results = run_walk_forward(dataset, steps, C=0.1, min_train_rows=100_000)
    assert results
    assert all(r.skipped for r in results)
    assert all(r.reason and "minimum" in r.reason for r in results)
    assert collect_predictions(results).empty


# --- walk-forward -----------------------------------------------------------

def test_walk_forward_produces_out_of_sample_predictions_for_every_test_game():
    dataset = _dataset()
    steps = make_steps(dataset.labelled, step_days=30)
    results = run_walk_forward(dataset, steps, C=0.1, min_train_rows=200)
    frame = collect_predictions(results)

    assert len(frame) > 1000
    assert frame["game_id"].is_unique
    assert frame["prob"].between(0, 1).all()
    assert set(frame.columns) >= {"game_id", "prob", "actual", "train_end", "n_train"}


def test_predictions_beat_the_coin_flip_baseline_on_a_learnable_signal():
    dataset = _dataset()
    steps = make_steps(dataset.labelled, step_days=30)
    frame = collect_predictions(run_walk_forward(dataset, steps, C=0.3, min_train_rows=200))
    metrics = evaluate(frame["actual"].to_numpy(), frame["prob"].to_numpy())
    assert metrics.log_loss < metrics.baseline_log_loss


def test_walk_forward_is_reproducible():
    dataset = _dataset()
    steps = make_steps(dataset.labelled, step_days=30)
    first = collect_predictions(run_walk_forward(dataset, steps, C=0.1, min_train_rows=200))
    second = collect_predictions(run_walk_forward(dataset, steps, C=0.1, min_train_rows=200))
    assert np.allclose(first["prob"].to_numpy(), second["prob"].to_numpy())


def test_train_end_date_is_recorded_on_every_row():
    dataset = _dataset(days=200)
    steps = make_steps(dataset.labelled, step_days=30)
    frame = collect_predictions(run_walk_forward(dataset, steps, C=0.1, min_train_rows=200))
    for row in frame.itertuples():
        assert row.train_end < row.official_date


def test_importance_stability_reports_rank_variation():
    dataset = _dataset()
    steps = make_steps(dataset.labelled, step_days=30)
    results = run_walk_forward(dataset, steps, C=0.1, min_train_rows=200)
    stability = importance_stability(results)
    assert set(stability) == set(FEATURES)
    for entry in stability.values():
        assert entry["n_steps"] >= 2
        assert entry["std_rank"] >= 0


# --- slices -----------------------------------------------------------------

def test_slices_cover_every_required_dimension():
    dataset = _dataset()
    steps = make_steps(dataset.labelled, step_days=30)
    frame = collect_predictions(run_walk_forward(dataset, steps, C=0.1, min_train_rows=200))
    slices = compute_slices(frame)

    types = {s.slice_type for s in slices}
    assert {
        "overall", "season", "month", "probability_band", "favorite_underdog",
        "home_away", "lineup_confirmed", "starters_known",
    } <= types


def test_probability_bands_are_measured_from_the_favorite_perspective():
    frame = pd.DataFrame(
        {
            "game_id": range(200),
            "official_date": [date(2024, 5, 1)] * 200,
            "season": [2024] * 200,
            "month": [5] * 200,
            # Half the rows favor the away team (home prob below 0.5).
            "prob": [0.62] * 100 + [0.38] * 100,
            "actual": [1] * 62 + [0] * 38 + [0] * 62 + [1] * 38,
            "lineup_confirmed": [False] * 200,
            "home_starter_known": [True] * 200,
            "away_starter_known": [True] * 200,
            "starter_quality_index": [4.0] * 200,
        }
    )
    bands = [s for s in compute_slices(frame) if s.slice_type == "probability_band"]
    assert len(bands) == 1
    band = bands[0]
    assert band.slice_key == "60-65"
    assert band.metrics.n == 200
    assert band.extra["observed"] == pytest.approx(0.62)


def test_small_slices_report_count_without_metrics():
    frame = pd.DataFrame(
        {
            "game_id": range(10),
            "official_date": [date(2024, 5, 1)] * 10,
            "season": [2024] * 10,
            "month": [5] * 10,
            "prob": [0.55] * 10,
            "actual": [1, 0] * 5,
            "lineup_confirmed": [False] * 10,
            "home_starter_known": [True] * 10,
            "away_starter_known": [True] * 10,
            "starter_quality_index": [4.0] * 10,
        }
    )
    overall = next(s for s in compute_slices(frame) if s.slice_type == "overall")
    assert overall.metrics.n == 10
    assert overall.metrics.log_loss is None


def test_probability_bands_cover_the_whole_range():
    assert PROBABILITY_BANDS[0][0] == 0.5
    assert PROBABILITY_BANDS[-1][1] > 1.0
    for earlier, later in zip(PROBABILITY_BANDS, PROBABILITY_BANDS[1:], strict=False):
        assert earlier[1] == later[0]


# --- ablation ---------------------------------------------------------------

def test_group_members_match_by_prefix():
    assert group_members("starting_pitcher", FEATURES) == ["sp_a"]
    assert group_members("bullpen", FEATURES) == ["bp_b"]
    assert group_members("travel_rest", FEATURES) == ["sched_c"]


def test_ablation_detects_a_group_that_carries_signal():
    from app.backtest.ablation import run_ablation

    dataset = _dataset()
    steps = make_steps(dataset.labelled, step_days=30)
    baseline = collect_predictions(run_walk_forward(dataset, steps, C=0.3, min_train_rows=200))
    rows = run_ablation(dataset, steps, 0.3, baseline, min_train_rows=200)

    by_group = {row.group: row for row in rows}
    signal = by_group["starting_pitcher"]
    noise = by_group["bullpen"]

    # sp_a genuinely carries signal in the fixture, so removing it should hurt.
    assert signal.delta_log_loss > NOISE_BAND
    assert signal.verdict == "IMPROVES"
    # bp_b is pure noise, so its effect must be an order of magnitude smaller.
    assert abs(noise.delta_log_loss) < abs(signal.delta_log_loss) / 5
    assert noise.verdict in ("NEUTRAL", "HURTS")


def test_unavailable_groups_are_reported_rather_than_omitted():
    from app.backtest.ablation import UNAVAILABLE_GROUPS, run_ablation

    dataset = _dataset(days=150)
    steps = make_steps(dataset.labelled, step_days=45)
    baseline = collect_predictions(run_walk_forward(dataset, steps, C=0.3, min_train_rows=200))
    rows = run_ablation(dataset, steps, 0.3, baseline, min_train_rows=200)
    reported = {row.group for row in rows if row.verdict == "UNAVAILABLE"}
    assert set(UNAVAILABLE_GROUPS) <= reported


# --- sanity gates -----------------------------------------------------------

def _flag_frame(prob: float, n: int = 1000) -> pd.DataFrame:
    return pd.DataFrame({"prob": [prob] * n, "actual": [1] * n})


def test_impossibly_good_accuracy_is_flagged_as_suspected_leakage():
    frame = _flag_frame(0.9)
    metrics = evaluate(frame["actual"], frame["prob"])
    flags = sanity_flags(frame, metrics)
    codes = {f["code"] for f in flags}
    gates = {f["gate"] for f in flags}
    assert "SUSPECTED_LEAKAGE" in codes
    assert {"accuracy", "log_loss", "extreme_band_share"} & gates


def test_a_dominant_feature_is_flagged():
    frame = pd.DataFrame({"prob": np.linspace(0.45, 0.55, 1000), "actual": [1, 0] * 500})
    metrics = evaluate(frame["actual"], frame["prob"])
    flags = sanity_flags(frame, metrics, dominant_share=0.9)
    assert any(f["gate"] == "dominant_feature_share" for f in flags)


def test_worse_than_a_coin_flip_is_flagged_as_underperforming():
    frame = pd.DataFrame({"prob": [0.2] * 1000, "actual": [1] * 1000})
    metrics = evaluate(frame["actual"], frame["prob"])
    flags = sanity_flags(frame, metrics)
    assert any(f["code"] == "UNDERPERFORMING" for f in flags)


def test_realistic_performance_trips_no_gate():
    rng = np.random.default_rng(19)
    prob = np.clip(rng.normal(0.53, 0.055, size=3000), 0.3, 0.72)
    actual = rng.binomial(1, prob)
    frame = pd.DataFrame({"prob": prob, "actual": actual})
    metrics = evaluate(actual, prob)
    assert sanity_flags(frame, metrics, dominant_share=0.12) == []


def test_gate_thresholds_match_the_documented_values():
    assert GATES["accuracy_too_high"] == 0.62
    assert GATES["log_loss_too_low"] == 0.62
    assert GATES["roc_auc_too_high"] == 0.70
    assert GATES["dominant_feature_share"] == 0.40
    assert NOISE_BAND > 0
