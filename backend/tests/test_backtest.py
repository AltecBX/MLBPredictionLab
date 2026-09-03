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


def test_a_thin_validation_slice_leaves_a_step_uncalibrated():
    """Below the floor the step serves the raw fit; at the floor it calibrates.

    Six fixture games a day: a 45-day slice is 270 rows, under the floor of
    300, and a 60-day slice is 360, over it. A Platt fit on the opening week
    once pulled a whole month toward the visitor (MODELING_PLAN.md); the floor
    is what stops it.
    """
    from app.backtest.walkforward import MIN_CALIBRATION_ROWS
    from app.modeling.dataset import LABEL_COLUMN
    from app.modeling.logistic import LogisticWinModel

    assert MIN_CALIBRATION_ROWS == 300
    dataset = _dataset(days=200)
    thin = make_steps(dataset.labelled, step_days=30, validation_days=45)[-1]
    wide = make_steps(dataset.labelled, step_days=30, validation_days=60)[-1]
    frame = dataset.labelled
    for step, calibrated in ((thin, False), (wide, True)):
        train = frame[frame["official_date"] <= step.train_end]
        validation = train[train["official_date"] >= step.validation_start]
        assert (len(validation) >= MIN_CALIBRATION_ROWS) is calibrated
        result = run_walk_forward(dataset, [step], C=0.1, min_train_rows=200)[0]
        raw = LogisticWinModel(feature_names=FEATURES, C=0.1).fit(train, LABEL_COLUMN)
        test = frame[(frame["official_date"] >= step.test_start) & (frame["official_date"] <= step.test_end)]
        same_as_raw = np.allclose(result.predictions["prob"].to_numpy(), raw.predict_raw(test))
        assert same_as_raw is not calibrated


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


# --- the served figure --------------------------------------------------------
#
# The product serves the logistic blended with the run simulation, or the
# logistic alone where no simulation can be formed. The backtest scores that
# figure on the same games, and its slices sit beside the component's.


def _served_fixture(monkeypatch):
    """A walk-forward frame and a stubbed simulation covering half the games."""
    import app.backtest.served as served_module
    from app.backtest.served import evaluate_served

    dataset = _dataset(days=200)
    steps = make_steps(dataset.labelled, step_days=30)
    frame = collect_predictions(run_walk_forward(dataset, steps, C=0.1, min_train_rows=200))
    assert len(frame) > 300

    simulated = {}
    rows = []
    for i, game_id in enumerate(frame["game_id"].tolist()):
        # Every other game cannot be simulated: the product serves the
        # logistic alone for those, and so must the backtest.
        value = None if i % 2 else 0.5 + 0.1 * ((i % 5) - 2)
        simulated[game_id] = value
        rows.append({"game_id": game_id, "sim_projected": value, "sim_prob": value})
    calls = {}

    def fake_simulate_slate(store, builder, predictions, size, simulations, **kwargs):
        calls["simulations"] = simulations
        calls["models"] = kwargs.get("models")
        return pd.DataFrame(rows)

    monkeypatch.setattr(served_module, "simulate_slate", fake_simulate_slate)
    monkeypatch.setattr(served_module, "_observed_runs", lambda store, ids: np.array([4.0, 5.0, 3.0, 6.0, 4.0, 2.0]))
    monkeypatch.setattr(served_module, "FeatureBuilder", lambda store: object())

    def fake_asof_sizes(store, predictions, fallback):
        # Serving fits dispersion per slate; the evaluation must ask for it.
        calls["asof_fallback"] = fallback
        return {int(g): 3.5 for g in predictions["game_id"]}

    monkeypatch.setattr(served_module, "_asof_sizes", fake_asof_sizes)
    evaluation = evaluate_served(object(), dataset, frame, simulations=777)
    return frame, simulated, calls, evaluation


def test_served_dispersion_is_fitted_per_slate_as_serving_fits_it(monkeypatch):
    _, _, calls, evaluation = _served_fixture(monkeypatch)
    assert "asof_fallback" in calls
    assert evaluation.dispersion["fit"].startswith("as-of")
    assert evaluation.dispersion["asof_nb_size_min"] == 3.5
    assert evaluation.dispersion["asof_nb_size_max"] == 3.5


def test_served_probability_blends_where_a_simulation_exists_and_falls_back_where_not(monkeypatch):
    from app.modeling.simulation import _blend

    frame, simulated, calls, evaluation = _served_fixture(monkeypatch)
    assert calls["simulations"] == 777
    assert evaluation.n_games == len(frame)
    assert evaluation.n_blended == sum(1 for v in simulated.values() if v is not None)
    assert evaluation.n_logistic_only == evaluation.n_games - evaluation.n_blended

    merged = evaluation.frame.set_index("game_id")
    for game_id, sim in simulated.items():
        row = merged.loc[game_id]
        if sim is None:
            assert row["served_prob"] == pytest.approx(row["prob"])
            assert not row["served_blended"]
            assert np.isnan(row["sim_prob"])
        else:
            expected = _blend(np.array([row["prob"]]), np.array([sim]), evaluation.weight)[0]
            assert row["served_prob"] == pytest.approx(expected)
            assert row["served_blended"]
    assert evaluation.metrics.n == evaluation.n_games
    assert evaluation.weight == 0.5
    assert evaluation.run_model == "projected"


def test_served_slices_are_the_same_dimensions_under_a_prefix(monkeypatch):
    from app.backtest.served import SERVED_SLICE_PREFIX

    _, _, _, evaluation = _served_fixture(monkeypatch)
    component = {s.slice_type for s in compute_slices(evaluation.frame)}
    served = {s.slice_type for s in compute_slices(evaluation.as_served())}
    assert served == component
    assert SERVED_SLICE_PREFIX == "served_"
    # The served overall row is scored on the served figure, not the component's.
    overall = next(s for s in compute_slices(evaluation.as_served()) if s.slice_type == "overall")
    assert overall.metrics.log_loss == pytest.approx(evaluation.metrics.log_loss)
    assert overall.metrics.log_loss != pytest.approx(
        evaluate(evaluation.frame["actual"], evaluation.frame["prob"]).log_loss
    )


def test_served_config_records_what_was_blended(monkeypatch):
    _, _, _, evaluation = _served_fixture(monkeypatch)
    config = evaluation.to_config()
    assert config["available"] is True
    assert config["blend_weight"] == 0.5
    assert config["run_model"] == "projected"
    assert config["simulations"] == 777
    assert config["n_blended"] + config["n_logistic_only"] == config["n_games"]
    assert config["dispersion"]["training_side_team_games"] == 6
    summary = evaluation.summary()
    assert summary["log_loss"] == pytest.approx(evaluation.metrics.log_loss)


def test_an_empty_walk_forward_cannot_be_served():
    from app.backtest.served import evaluate_served

    with pytest.raises(ValueError):
        evaluate_served(object(), _dataset(days=30), pd.DataFrame())


def test_ablation_reports_the_group_alone_view_and_a_combined_reading():
    """Leave-one-out and group-alone are reported together (BACKTEST_PLAN.md §6)."""
    from app.backtest.ablation import run_ablation

    dataset = _dataset()
    steps = make_steps(dataset.labelled, step_days=30)
    baseline = collect_predictions(run_walk_forward(dataset, steps, C=0.3, min_train_rows=200))
    rows = {r.group: r for r in run_ablation(dataset, steps, 0.3, baseline, min_train_rows=200)}

    signal = rows["starting_pitcher"]
    noise = rows["bullpen"]

    # sp_a carries the fixture's only extra signal, so it predicts on its own.
    assert signal.solo_log_loss is not None
    assert signal.solo_predicts is True
    assert signal.reading == "UNIQUE SIGNAL — keep"

    # bp_b is pure noise: neutral to remove and predicts nothing alone.
    assert noise.solo_predicts is False
    assert noise.reading == "NO SIGNAL — remove or reduce"

    # Unavailable groups pass the reading through rather than inventing one.
    assert rows["weather"].reading == "UNAVAILABLE"
    for row in rows.values():
        assert row.to_dict()["reading"] == row.reading
