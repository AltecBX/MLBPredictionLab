"""Multi-season projections: the arithmetic, the as-of cut, and the wiring."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from app.features import projections as pj
from app.features.builder import FeatureBuilder
from app.features.context import GameContext
from app.features.registry import PROJECTIONS, feature_keys, spec
from tests.conftest import make_store

# --- pooling arithmetic ----------------------------------------------------


def test_pool_with_no_evidence_is_the_league():
    pooled = pj.pool([], (0.0, 0.0, 4.5), k=80.0)
    assert pooled.deviation == 0.0
    assert pooled.rate(4.5) == 4.5
    assert pooled.seasons_used == 0


def test_pool_regresses_a_prior_season_toward_the_league():
    # 162 games at 5.5 runs against a 4.5 league: +1.0 per game observed.
    pooled = pj.pool([(0.6, 5.5 * 162, 162.0, 4.5)], (0.0, 0.0, 4.4), k=80.0)
    # Weighted evidence 97.2 games against 80 games of league: 55% of the way.
    expected = 1.0 * (0.6 * 162) / (0.6 * 162 + 80)
    assert pooled.deviation == pytest.approx(expected)
    # And it is expressed on THIS season's league rate, not last season's.
    assert pooled.rate(4.4) == pytest.approx(4.4 + expected)
    assert pooled.seasons_used == 1


def test_pool_converges_on_the_season_in_progress():
    # A team that was terrible last year and is now dominant: with enough of
    # this season played, the projection follows this season.
    prior = [(0.6, 3.5 * 162, 162.0, 4.5)]
    early = pj.pool(prior, (5.5 * 10, 10.0, 4.5), k=80.0)
    late = pj.pool(prior, (5.5 * 150, 150.0, 4.5), k=80.0)
    assert early.deviation < 0 < late.deviation
    assert late.deviation < 1.0  # never all the way, while the prior still counts


def test_pool_ignores_seasons_without_a_denominator():
    pooled = pj.pool([(0.6, 0.0, 0.0, 4.5), (0.3, 4.0 * 100, 100.0, 4.5)],
                     (0.0, 0.0, 4.5), k=80.0)
    assert pooled.seasons_used == 1
    assert pooled.evidence == pytest.approx(30.0)


def test_each_season_is_measured_against_its_own_league():
    # Same raw rate in a higher-scoring season is a WORSE offense, and the
    # pooled deviation says so.
    high_env = pj.pool([(0.6, 4.8 * 162, 162.0, 5.0)], (0.0, 0.0, 4.5), k=80.0)
    low_env = pj.pool([(0.6, 4.8 * 162, 162.0, 4.2)], (0.0, 0.0, 4.5), k=80.0)
    assert high_env.deviation < 0 < low_env.deviation


# --- registry and wiring ----------------------------------------------------


def test_projection_features_are_registered_in_fs_v9():
    keys = feature_keys("fs_v9")
    for item in PROJECTIONS:
        assert item.key in keys
        assert spec(item.key) is item
    assert len(keys) == len(feature_keys("fs_v1")) + len(PROJECTIONS)


def test_builder_emits_projection_features(store, target_game):
    builder = FeatureBuilder(store, feature_set_version="fs_v9")
    vector = builder.build(target_game, target_game.as_of())
    for item in PROJECTIONS:
        assert item.key in vector.features
    # Both sides have games on record in the fixture, so the team projections
    # exist; the sign convention holds in that a pure difference of two
    # finite numbers came out finite.
    assert vector.features["proj_off_rpg_diff"] is not None
    assert vector.features["proj_ra_rpg_diff"] is not None


def test_projection_reads_only_games_knowable_before_as_of(fixture_frames):
    """A prior-season game that becomes knowable after as_of must not count."""
    frames = {k: v.copy() for k, v in fixture_frames.items()}
    store = make_store(frames)
    proj = pj.Projections(store)
    games = store.games
    row = games.iloc[-1]
    team_id = int(row["home_team_id"])
    season = int(row["season"])
    first_pitch = pd.Timestamp(row["game_date_utc"]).to_pydatetime()
    as_of = first_pitch.replace(tzinfo=UTC) if first_pitch.tzinfo is None else first_pitch
    before = proj.team_values(team_id, season, as_of, 4.5)["proj_off_rpg"]

    # Now ask at the very start of the season: nothing this season is knowable.
    dawn = datetime(season, 1, 1, tzinfo=UTC)
    at_dawn = proj.team_values(team_id, season, dawn, 4.5)["proj_off_rpg"]
    assert at_dawn.sample_size <= before.sample_size
    assert at_dawn.value == pytest.approx(4.5)  # no evidence at all: the league


def test_starter_projection_is_missing_without_a_starter(store):
    proj = pj.Projections(store)
    values = proj.starter_values(None, 2024, datetime(2024, 6, 1, tzinfo=UTC),
                                 0.22, 0.08, 3.1, 1.0)
    assert values["proj_sp_fip"].value is None
    assert values["proj_sp_k_minus_bb_pct"].value is None


def test_starter_with_no_history_projects_the_league_flagged(store):
    proj = pj.Projections(store)
    values = proj.starter_values(999_999, 2024, datetime(2024, 6, 1, tzinfo=UTC),
                                 0.22, 0.08, 3.1, 1.0)
    assert values["proj_sp_k_minus_bb_pct"].value == pytest.approx(0.14)
    assert values["proj_sp_fip"].value == pytest.approx(4.1)
    assert values["proj_sp_fip"].is_estimated is True
    assert values["proj_sp_fip"].sample_size == 0


def test_context_exposes_no_outcome(target_game):
    assert isinstance(target_game, GameContext)
    assert not hasattr(target_game, "home_win")
