"""Starting-pitcher Statcast features: rates, shrinkage, and the as-of cut.

Frames here are TEST FIXTURES built in memory — explicitly synthetic, never
served and never written to the application database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.features import statcast_features as sc
from app.features.asof import AsOfStore
from app.features.builder import FeatureBuilder
from app.features.context import GameContext
from app.features.elo import AsOfElo
from app.features.registry import REGISTRY, SC_SP, feature_keys
from tests.conftest import HOME_SP, make_store

AS_OF = datetime(2024, 7, 1, 20, 0, tzinfo=UTC)
SEASON_START = datetime(2024, 1, 1, tzinfo=UTC)


def _game_row(
    day: int,
    *,
    player_id: int = HOME_SP,
    season: int = 2024,
    pitches: int = 90,
    swings: int = 45,
    whiffs: int = 12,
    called: int = 18,
    out_of_zone: int = 45,
    chases: int = 12,
    bip: int = 18,
    barrels: int = 2,
    hard_hit: int = 7,
    ev_sum: float = 1580.0,
    ff_count: int = 40,
    ff_speed_sum: float = 3800.0,
    xwoba_num: float = 6.0,
    woba_denom: int = 24,
) -> dict:
    when = datetime(season, 1, 1, tzinfo=UTC) + timedelta(days=day)
    return {
        "game_id": 900000 + season * 1000 + day,
        "player_id": player_id,
        "game_date_utc": when,
        "season": season,
        "knowledge_time": when + timedelta(hours=3),
        "is_starter": True,
        "pitches": float(pitches),
        "swings": float(swings),
        "whiffs": float(whiffs),
        "called_strikes": float(called),
        "out_of_zone": float(out_of_zone),
        "chases": float(chases),
        "plate_appearances": float(woba_denom),
        "strikeouts": 6.0,
        "walks": 2.0,
        "woba_denom": float(woba_denom),
        "xwoba_num": float(xwoba_num),
        "woba_num": float(xwoba_num),
        "ff_count": float(ff_count),
        "ff_speed_sum": float(ff_speed_sum),
        "extension_count": float(pitches),
        "extension_sum": float(pitches) * 6.4,
        "balls_in_play": float(bip),
        "barrels": float(barrels),
        "hard_hit": float(hard_hit),
        "ev_count": float(bip),
        "ev_sum": float(ev_sum),
    }


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# summarize: rates are ratios of sums, never means of per-game rates
# --------------------------------------------------------------------------


def test_summarize_of_nothing_is_empty_not_zero():
    rates = sc.summarize(pd.DataFrame())
    assert rates.is_empty
    assert rates.barrel_pct is None
    assert rates.xwoba is None


def test_rates_are_ratios_of_sums():
    rows = _frame([_game_row(100), _game_row(105)])
    rates = sc.summarize(rows)
    assert rates.pitches == 180
    assert rates.whiff_pct == pytest.approx(24 / 90)
    assert rates.barrel_pct == pytest.approx(4 / 36)
    assert rates.csw_pct == pytest.approx((36 + 24) / 180)
    assert rates.fastball_velocity == pytest.approx(7600.0 / 80)


def test_a_short_outing_does_not_weigh_like_a_long_one():
    """The failure a mean-of-per-game-rates implementation would produce.

    One 90-pitch start at a 20% whiff rate and one 10-pitch relief appearance at
    100% is not a 60% pitcher. Averaging the two per-game rates says it is.
    """
    long_start = _game_row(100, pitches=90, swings=50, whiffs=10)
    cameo = _game_row(101, pitches=10, swings=4, whiffs=4)
    rates = sc.summarize(_frame([long_start, cameo]))
    assert rates.whiff_pct == pytest.approx(14 / 54)
    naive_mean = (10 / 50 + 4 / 4) / 2
    assert rates.whiff_pct < naive_mean / 2


def test_a_rate_with_no_denominator_is_unknown():
    row = _game_row(100, bip=0, barrels=0, hard_hit=0, ev_sum=0.0)
    rates = sc.summarize(_frame([row]))
    assert rates.balls_in_play == 0
    assert rates.barrel_pct is None
    assert rates.avg_exit_velocity is None
    # But the pitches themselves were still counted.
    assert rates.pitches == 90


# --------------------------------------------------------------------------
# starter_values
# --------------------------------------------------------------------------


LEAGUE = sc.StatcastBaseline(
    xwoba=0.320, barrel_pct=0.08, hard_hit_pct=0.39, avg_exit_velocity=88.9,
    whiff_pct=0.24, chase_pct=0.29, csw_pct=0.29, fastball_velocity=94.0,
)
EMPTY = pd.DataFrame()


def test_a_pitcher_with_no_statcast_gets_missing_not_zero():
    values = sc.starter_values(EMPTY, EMPTY, EMPTY, LEAGUE)
    assert set(values) == set(sc.FEATURE_KEYS)
    assert all(v.value is None for v in values.values())
    assert all(v.is_estimated for v in values.values())


def test_every_declared_key_is_produced():
    season = _frame([_game_row(d) for d in range(100, 160, 5)])
    values = sc.starter_values(season, EMPTY, season, LEAGUE)
    assert set(values) == set(sc.FEATURE_KEYS)


def test_a_tiny_sample_is_pulled_most_of_the_way_to_the_prior():
    """One start cannot establish a barrel rate."""
    one_start = _frame([_game_row(100, bip=18, barrels=6)])
    values = sc.starter_values(one_start, EMPTY, one_start, LEAGUE)
    barrel = values["sc_sp_barrel_pct_allowed"]
    assert barrel.is_estimated
    observed = 6 / 18
    assert LEAGUE.barrel_pct < barrel.value < observed
    assert abs(barrel.value - LEAGUE.barrel_pct) < abs(barrel.value - observed)


def test_a_large_sample_is_left_close_to_what_was_observed():
    many = _frame([_game_row(d, bip=18, barrels=6) for d in range(10, 170, 5)])
    values = sc.starter_values(many, EMPTY, many, LEAGUE)
    barrel = values["sc_sp_barrel_pct_allowed"]
    assert not barrel.is_estimated
    assert barrel.value == pytest.approx(6 / 18, abs=0.05)


def test_the_prior_season_is_the_prior_this_season_regresses_toward():
    """Two identical short seasons, different histories, different answers."""
    short = _frame([_game_row(100, bip=18, barrels=3)])
    strong_history = _frame(
        [_game_row(d, season=2023, bip=18, barrels=0) for d in range(100, 200, 5)]
    )
    weak_history = _frame(
        [_game_row(d, season=2023, bip=18, barrels=8) for d in range(100, 200, 5)]
    )
    with_strong = sc.starter_values(short, strong_history, short, LEAGUE)
    with_weak = sc.starter_values(short, weak_history, short, LEAGUE)
    assert (
        with_strong["sc_sp_barrel_pct_allowed"].value
        < with_weak["sc_sp_barrel_pct_allowed"].value
    )


def test_no_prior_season_falls_back_to_the_league():
    short = _frame([_game_row(100)])
    values = sc.starter_values(short, EMPTY, short, LEAGUE)
    assert values["sc_sp_barrel_pct_allowed"].value is not None
    assert values["sc_sp_barrel_pct_allowed"].is_estimated


def test_velocity_trend_is_a_delta_from_the_pitchers_own_baseline():
    """A pitcher down 3 mph shows −3, whatever his absolute velocity is."""
    season = _frame(
        [_game_row(d, ff_count=40, ff_speed_sum=40 * 95.0) for d in range(10, 150, 5)]
        + [_game_row(d, ff_count=40, ff_speed_sum=40 * 92.0) for d in range(150, 170, 5)]
    )
    recent = _frame(
        [_game_row(d, ff_count=40, ff_speed_sum=40 * 92.0) for d in range(150, 170, 5)]
    )
    trend = sc.starter_values(season, EMPTY, recent, LEAGUE)["sc_sp_velocity_delta_30d"]
    assert trend.value is not None
    assert -3.0 < trend.value < 0.0
    assert not trend.is_estimated


def test_velocity_trend_on_too_few_recent_fastballs_is_flagged():
    season = _frame([_game_row(d) for d in range(10, 150, 5)])
    thin = _frame([_game_row(160, ff_count=8, ff_speed_sum=8 * 92.0)])
    trend = sc.starter_values(season, EMPTY, thin, LEAGUE)["sc_sp_velocity_delta_30d"]
    assert trend.is_estimated
    assert "recent fastballs" in (trend.detail or "")


def test_velocity_trend_is_unknown_with_no_fastballs():
    none_thrown = _frame([_game_row(100, ff_count=0, ff_speed_sum=0.0)])
    trend = sc.starter_values(
        none_thrown, EMPTY, none_thrown, LEAGUE
    )["sc_sp_velocity_delta_30d"]
    assert trend.value is None


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


def test_every_emitted_feature_is_registered():
    """An unregistered feature cannot enter a model (FEATURE_DICTIONARY §10)."""
    for key in sc.FEATURE_KEYS:
        assert f"{key}_diff" in REGISTRY


def test_fs_v2_is_fs_v1_plus_the_statcast_group():
    v1, v2 = feature_keys("fs_v1"), feature_keys("fs_v2")
    assert v2[: len(v1)] == v1
    assert set(v2) - set(v1) == {s.key for s in SC_SP}


def test_the_allowed_measures_are_oriented_so_lower_is_better():
    for spec in SC_SP:
        if "allowed" in spec.key:
            assert not spec.higher_favors_home, spec.key


def test_the_rejected_group_carries_its_measurement():
    """BACKTEST_PLAN.md § Reporting: the evidence travels with the registration.

    The group was built, measured over a full walk-forward season and did not
    earn a place. It stays registered so the next person reads the numbers
    instead of re-running the same experiment.
    """
    for spec in SC_SP:
        assert not spec.available, spec.key
        assert "log loss" in spec.measurement, spec.key
        assert "Rejected" in spec.measurement, spec.key


def test_the_active_set_contains_nothing_unavailable():
    """A feature that reports UNAVAILABLE cannot be one the model depends on."""
    for key in feature_keys("fs_v1"):
        assert REGISTRY[key].available, key


def test_the_statcast_group_is_ablatable_on_its_own():
    from app.backtest.ablation import group_members

    names = feature_keys("fs_v2")
    statcast = group_members("starting_pitcher_statcast", names)
    box_score = group_members("starting_pitcher", names)
    assert set(statcast) == {s.key for s in SC_SP}
    # The two must not overlap, or removing one silently removes part of the
    # other and neither ablation means anything.
    assert not set(statcast) & set(box_score)
    assert box_score


# --------------------------------------------------------------------------
# Builder integration and the as-of cut
# --------------------------------------------------------------------------


@pytest.fixture()
def statcast_store(fixture_frames) -> AsOfStore:
    """The Phase-1 fixture league, with Statcast attached for the home starter."""
    rows = [_game_row(d) for d in range(60, 130, 5)]
    # A start AFTER the target game, which no feature may ever see.
    future = _game_row(300, pitches=90, whiffs=90)
    store = make_store(fixture_frames)
    return AsOfStore(
        store.games, store.team_games, store.pitcher_games, store.players,
        store.ballparks, _frame([*rows, future]).sort_values("knowledge_time"),
    )


def test_a_store_with_no_statcast_reports_it_rather_than_faking_it(store):
    assert not store.has_statcast
    builder = FeatureBuilder(store, AsOfElo(store.games), feature_set_version="fs_v2")
    ctx = GameContext.from_row(store.games.iloc[35].to_dict())
    vector = builder.build(ctx, ctx.first_pitch_utc - timedelta(hours=3))
    for spec in SC_SP:
        assert vector.features[spec.key] is None
        assert "not ingested" in (
            vector.home.get(spec.key.removesuffix("_diff")).detail or ""
        )


def test_statcast_features_are_produced_when_the_data_is_there(statcast_store):
    builder = FeatureBuilder(
        statcast_store, AsOfElo(statcast_store.games), feature_set_version="fs_v2"
    )
    ctx = GameContext.from_row(statcast_store.games.iloc[35].to_dict())
    vector = builder.build(ctx, ctx.first_pitch_utc - timedelta(hours=3))
    home = vector.home
    produced = [k for k in sc.FEATURE_KEYS if home.get(k).value is not None]
    assert len(produced) == len(sc.FEATURE_KEYS), produced


def test_a_start_after_the_prediction_moment_is_never_read(statcast_store):
    """The one failure that would look like a huge accuracy gain."""
    ctx = GameContext.from_row(statcast_store.games.iloc[35].to_dict())
    as_of = ctx.first_pitch_utc - timedelta(hours=3)
    window = statcast_store.pitcher_statcast_asof(HOME_SP, as_of)
    assert not window.empty
    assert (window["game_date_utc"] < as_of).all()
    assert (window["knowledge_time"] <= as_of).all()
    # The 100%-whiff future start would be unmissable in the rate.
    assert sc.summarize(window).whiff_pct < 0.5


def test_the_candidate_set_is_not_the_default_until_it_earns_it(store):
    """fs_v2 is a candidate. Nothing switches to it without a measurement.

    The default moved from fs_v1 to fs_v9 on a measurement (MODELING_PLAN.md
    § Multi-season projections); the rejected Statcast group is still not in
    whatever the default is.
    """
    from app.features.builder import FEATURE_SET_VERSION

    assert FEATURE_SET_VERSION == "fs_v9"
    default = FeatureBuilder(store, AsOfElo(store.games))
    ctx = GameContext.from_row(store.games.iloc[35].to_dict())
    vector = default.build(ctx, ctx.first_pitch_utc - timedelta(hours=3))
    assert not set(vector.features) & {s.key for s in SC_SP}


def test_the_league_prior_cannot_see_a_game_played_earlier_the_same_day(
    statcast_store,
):
    """An afternoon game must not move the prior an evening game is judged by."""
    from datetime import time

    builder = FeatureBuilder(
        statcast_store, AsOfElo(statcast_store.games), feature_set_version="fs_v2"
    )
    season_start = datetime(2024, 1, 1, tzinfo=UTC)
    day = datetime(2024, 5, 1, tzinfo=UTC).date()
    morning = datetime.combine(day, time(13, 0), tzinfo=UTC)
    evening = datetime.combine(day, time(23, 0), tzinfo=UTC)
    assert builder.statcast_league_baseline(
        morning, season_start
    ) == builder.statcast_league_baseline(evening, season_start)


def test_the_as_of_cut_moves_with_as_of(statcast_store):
    early = statcast_store.pitcher_statcast_asof(
        HOME_SP, datetime(2024, 3, 15, tzinfo=UTC)
    )
    later = statcast_store.pitcher_statcast_asof(
        HOME_SP, datetime(2024, 5, 15, tzinfo=UTC)
    )
    assert len(early) < len(later)
