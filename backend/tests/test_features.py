"""Feature calculation, shrinkage and determinism tests."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest

from app.core.clock import as_of_for_game
from app.features import aggregates as agg
from app.features.registry import FS_V1, REGISTRY, feature_keys, spec
from app.features.shrinkage import FeatureValue, shrink, shrink_mean

# --- registry contract ------------------------------------------------------

def test_every_active_feature_is_registered():
    keys = feature_keys("fs_v1")
    assert len(keys) == len(FS_V1)
    for key in keys:
        assert spec(key).available is True


def test_unregistered_feature_raises():
    with pytest.raises(KeyError, match="not registered"):
        spec("not_a_real_feature")


def test_registry_keys_are_unique():
    keys = [s.key for s in REGISTRY.values()]
    assert len(keys) == len(set(keys))


def test_deferred_features_are_marked_unavailable():
    from app.features.registry import DEFERRED

    for item in DEFERRED:
        assert item.available is False
        assert item.phase >= 2
        assert item.source_category


# --- shrinkage --------------------------------------------------------------

def test_shrinkage_pulls_small_samples_toward_the_prior():
    tiny = shrink(events=10, denominator=10, prior_rate=0.25, k=60, min_sample=60)
    assert tiny.value == pytest.approx((10 + 0.25 * 60) / (10 + 60))
    assert tiny.is_estimated is True
    assert 0.25 < tiny.value < 1.0


def test_shrinkage_converges_to_the_observed_rate_with_volume():
    heavy = shrink(events=6000, denominator=20000, prior_rate=0.25, k=60, min_sample=60)
    assert heavy.value == pytest.approx(0.30, abs=0.002)
    assert heavy.is_estimated is False


def test_shrink_mean_with_no_observations_returns_the_prior_flagged():
    result = shrink_mean(None, 0, 4.5, 25)
    assert result.value == 4.5
    assert result.is_estimated is True
    assert result.sample_size == 0


def test_missing_feature_value_carries_a_reason():
    missing = FeatureValue.missing("starter unknown")
    assert missing.value is None
    assert missing.is_estimated is True
    assert missing.detail == "starter unknown"


# --- aggregates -------------------------------------------------------------

def test_team_aggregate_computes_expected_rates():
    frame = pd.DataFrame(
        [
            {"runs": 5, "runs_allowed": 3, "hits": 9, "doubles": 2, "triples": 0,
             "home_runs": 1, "walks": 4, "intentional_walks": 0, "hit_by_pitch": 1,
             "strikeouts": 7, "at_bats": 35, "plate_appearances": 40, "sac_flies": 0,
             "batters_faced": 38, "strikeouts_pitched": 9, "walks_allowed": 2,
             "home_runs_allowed": 1, "hits_allowed": 7, "errors": 1, "won": 1.0},
            {"runs": 2, "runs_allowed": 6, "hits": 5, "doubles": 1, "triples": 0,
             "home_runs": 0, "walks": 2, "intentional_walks": 0, "hit_by_pitch": 0,
             "strikeouts": 11, "at_bats": 33, "plate_appearances": 36, "sac_flies": 1,
             "batters_faced": 40, "strikeouts_pitched": 6, "walks_allowed": 5,
             "home_runs_allowed": 2, "hits_allowed": 10, "errors": 0, "won": 0.0},
        ]
    )
    result = agg.team_aggregate(frame)
    assert result.games == 2
    assert result.runs_per_game == pytest.approx(3.5)
    assert result.runs_allowed_per_game == pytest.approx(4.5)
    assert result.run_diff_per_game == pytest.approx(-1.0)
    assert result.win_pct == pytest.approx(0.5)
    assert result.errors_per_game == pytest.approx(0.5)
    assert 0 < result.woba_proxy < 1
    assert 0 < result.pythag_win_pct < 1


def test_pitching_aggregate_era_and_whip():
    frame = pd.DataFrame(
        [
            {"outs_pitched": 18, "earned_runs": 2, "hr_allowed": 1, "bb_allowed": 2,
             "hbp_allowed": 0, "so_pitched": 7, "hits_allowed": 5, "batters_faced": 24,
             "pitches_thrown": 95, "ground_outs_pitched": 7, "air_outs_pitched": 6,
             "is_starter": True},
        ]
    )
    result = agg.pitching_aggregate(frame, fip_constant=3.1)
    assert result.innings == pytest.approx(6.0)
    assert result.era == pytest.approx(3.0)
    assert result.whip == pytest.approx(7 / 6)
    assert result.k_pct == pytest.approx(7 / 24)
    assert result.bb_pct == pytest.approx(2 / 24)
    assert result.k_minus_bb_pct == pytest.approx(5 / 24)
    assert result.starts == 1


def test_fip_constant_matches_league_era_minus_raw_rate():
    frame = pd.DataFrame(
        [{"outs_pitched": 27, "earned_runs": 4, "hr_allowed": 1, "bb_allowed": 3,
          "hbp_allowed": 1, "so_pitched": 9, "is_starter": True}]
    )
    constant = agg.fip_constant(frame)
    innings = 9.0
    expected = (9 * 4 / innings) - ((13 * 1 + 3 * (3 + 1) - 2 * 9) / innings)
    assert constant == pytest.approx(expected)


def test_haversine_matches_a_known_distance():
    # New York to Los Angeles is roughly 3,940 km.
    km = agg.haversine_km(40.7128, -74.0060, 34.0522, -118.2437)
    assert 3900 < km < 4000


def test_utc_offset_hours_handles_unknown_timezone():
    from datetime import UTC, datetime

    assert agg.utc_offset_hours(None, datetime(2024, 7, 1, tzinfo=UTC)) is None
    assert agg.utc_offset_hours("Not/AZone", datetime(2024, 7, 1, tzinfo=UTC)) is None
    assert agg.utc_offset_hours("America/New_York", datetime(2024, 7, 1, tzinfo=UTC)) == -4.0


# --- builder ----------------------------------------------------------------

def test_builder_emits_the_full_active_feature_set(builder, target_game):
    vector = builder.build(target_game, as_of_for_game(target_game.first_pitch_utc))
    assert set(vector.features) == set(feature_keys("fs_v1"))
    assert set(vector.sample_sizes) == set(vector.features)
    assert set(vector.estimated_flags) == set(vector.features)


def test_builder_is_deterministic(builder, target_game):
    as_of = as_of_for_game(target_game.first_pitch_utc)
    first = builder.build(target_game, as_of)
    second = builder.build(target_game, as_of)
    assert first.features == second.features
    assert first.sample_sizes == second.sample_sizes
    assert first.completeness == second.completeness


def test_home_field_feature_is_always_one(builder, target_game):
    vector = builder.build(target_game, as_of_for_game(target_game.first_pitch_utc))
    assert vector.features["env_home_field"] == 1.0


def test_missing_starter_drops_completeness_and_flags_the_feature(builder, target_game):
    from dataclasses import replace

    as_of = as_of_for_game(target_game.first_pitch_utc)
    full = builder.build(target_game, as_of)

    without = replace(target_game, home_starter_id=None, home_starter_status="UNKNOWN")
    reduced = builder.build(without, as_of)

    assert reduced.features["sp_identified_home"] == 0.0
    assert reduced.completeness < full.completeness
    assert reduced.features["sp_era_season_diff"] is None
    assert "sp_era_season_diff" in reduced.missing_features


def test_sign_convention_positive_favors_home(builder, target_game):
    """Inverted features are assembled away-minus-home so positive favors home."""
    as_of = as_of_for_game(target_game.first_pitch_utc)
    vector = builder.build(target_game, as_of)

    home_era = vector.home.get("sp_era_season").value
    away_era = vector.away.get("sp_era_season").value
    assert home_era is not None and away_era is not None
    assert vector.features["sp_era_season_diff"] == pytest.approx(away_era - home_era)

    home_elo = vector.home.get("elo").value
    away_elo = vector.away.get("elo").value
    assert vector.features["elo_diff"] == pytest.approx(home_elo - away_elo)


def test_completeness_is_bounded(builder, target_game):
    vector = builder.build(target_game, as_of_for_game(target_game.first_pitch_utc))
    assert 0.0 <= vector.completeness <= 1.0


def test_league_baseline_uses_the_day_boundary(builder, target_game):
    """Baselines are cached per day and computed at that day's start — never later."""
    as_of = as_of_for_game(target_game.first_pitch_utc)
    baseline = builder.league_baseline(target_game.season, as_of)
    later = builder.league_baseline(target_game.season, as_of + timedelta(hours=2))
    assert baseline == later
    assert baseline.runs_per_game is not None and baseline.runs_per_game > 0


def test_elevation_is_converted_to_kilometres(builder, target_game):
    vector = builder.build(target_game, as_of_for_game(target_game.first_pitch_utc))
    elevation_km = vector.features["env_venue_elevation_km"]
    assert elevation_km is not None
    assert 0 <= elevation_km < 2.0  # no MLB park is above 2 km


def test_starter_rest_is_capped_and_flags_a_single_start_pitcher():
    """A pitcher with one start on record cannot establish a rotation cadence."""
    from datetime import UTC, datetime

    from app.features.builder import MAX_MEANINGFUL_REST, FeatureBuilder

    first_pitch = datetime(2024, 8, 1, 23, 0, tzinfo=UTC)

    # A 40-day layoff caps at the meaningful bound and is flagged estimated.
    single = pd.DataFrame({"game_date_utc": [pd.Timestamp(first_pitch) - timedelta(days=40)]})
    rest, short = FeatureBuilder._starter_rest(single, first_pitch)
    assert rest.value == MAX_MEANINGFUL_REST
    assert rest.is_estimated is True
    assert short.value == 0.0

    # Normal rotation rest passes through unflagged.
    regular = pd.DataFrame(
        {
            "game_date_utc": [
                pd.Timestamp(first_pitch) - timedelta(days=11),
                pd.Timestamp(first_pitch) - timedelta(days=5),
            ]
        }
    )
    rest, short = FeatureBuilder._starter_rest(regular, first_pitch)
    assert rest.value == pytest.approx(5.0)
    assert rest.is_estimated is False
    assert short.value == 0.0

    # Short rest is flagged.
    short_rest = pd.DataFrame(
        {
            "game_date_utc": [
                pd.Timestamp(first_pitch) - timedelta(days=9),
                pd.Timestamp(first_pitch) - timedelta(days=3),
            ]
        }
    )
    _, short = FeatureBuilder._starter_rest(short_rest, first_pitch)
    assert short.value == 1.0


def test_starter_rest_is_missing_without_any_prior_start():
    from datetime import UTC, datetime

    from app.features.builder import FeatureBuilder

    rest, short = FeatureBuilder._starter_rest(
        pd.DataFrame(), datetime(2024, 8, 1, tzinfo=UTC)
    )
    assert rest.value is None and short.value is None
    assert "no prior starts" in (rest.detail or "")
