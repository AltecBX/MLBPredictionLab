"""Per-pitcher bullpen availability.

The rest rules are conventions rather than fitted parameters, so what these
tests pin is that the classification is *the rule*, applied to as-of data, with
no path by which tonight's relief outing can reach tonight's prediction.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.features.bullpen import (
    AVAILABLE,
    HEAVY_OUTING_PITCHES,
    HEAVY_TWO_DAY_PITCHES,
    LIMITED,
    MAX_CONSECUTIVE_DAYS,
    MIN_APPEARANCES_FOR_CORPS,
    MODERATE_OUTING_PITCHES,
    UNAVAILABLE,
    RelieverStatus,
    _classify,
    _consecutive_days,
    bullpen_status,
    summarize,
)

TEAM = 111
LEAGUE_K_BB = 0.14


# --------------------------------------------------------------------------
# The rule itself
# --------------------------------------------------------------------------


def test_a_rested_reliever_is_available():
    assert _classify(0, 0, 0) == AVAILABLE


def test_a_heavy_outing_yesterday_rules_a_pitcher_out():
    assert _classify(HEAVY_OUTING_PITCHES, HEAVY_OUTING_PITCHES, 1) == UNAVAILABLE


def test_two_heavy_days_rule_a_pitcher_out_even_when_neither_alone_would():
    """The two-day total is a separate rule, not a restatement of the one-day one."""
    each = HEAVY_TWO_DAY_PITCHES // 2 + 1
    assert each < HEAVY_OUTING_PITCHES
    assert _classify(each, each * 2, 2) == UNAVAILABLE


def test_three_straight_days_rules_a_pitcher_out_regardless_of_pitch_count():
    assert _classify(5, 9, MAX_CONSECUTIVE_DAYS) == UNAVAILABLE


def test_a_moderate_outing_yesterday_is_limited_not_unavailable():
    """The middle state exists because managers use it; collapsing it loses that."""
    assert _classify(MODERATE_OUTING_PITCHES, MODERATE_OUTING_PITCHES, 1) == LIMITED


def test_back_to_back_days_are_limited():
    assert _classify(10, 18, 2) == LIMITED


def test_limited_is_not_unavailable():
    """A limited pitcher can still pitch. The two states must not collapse."""
    limited = RelieverStatus(1, LIMITED, 0, 0, 0, 0, 0, 0, 0.15)
    unavailable = RelieverStatus(2, UNAVAILABLE, 0, 0, 0, 0, 0, 0, 0.15)
    assert limited.can_pitch
    assert not unavailable.can_pitch


# --------------------------------------------------------------------------
# Consecutive days are counted back from yesterday, not from today
# --------------------------------------------------------------------------


def test_consecutive_days_counts_back_from_yesterday():
    today = datetime(2025, 6, 10, tzinfo=UTC).date()
    yesterday = today - timedelta(days=1)
    day_before = today - timedelta(days=2)
    assert _consecutive_days({yesterday, day_before}, today) == 2


def test_a_run_that_ended_two_days_ago_is_a_rested_pitcher():
    """The gap is the whole point: rest is measured by the break, not the work."""
    today = datetime(2025, 6, 10, tzinfo=UTC).date()
    older = {today - timedelta(days=2), today - timedelta(days=3)}
    assert _consecutive_days(older, today) == 0


def test_todays_own_appearance_does_not_count():
    """A game at first pitch has not been played. Counting it would be leakage."""
    today = datetime(2025, 6, 10, tzinfo=UTC).date()
    assert _consecutive_days({today}, today) == 0


# --------------------------------------------------------------------------
# Reading real frames through the as-of store
# --------------------------------------------------------------------------


def _relief_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


class _FakeStore:
    """Only the one method `bullpen_status` uses, with the as-of cut applied."""

    def __init__(self, frame: pd.DataFrame):
        self.frame = frame

    def team_pitcher_games_asof(self, team_id, as_of, start=None, relievers_only=False):
        frame = self.frame
        frame = frame[(frame["team_id"] == team_id) & (frame["knowledge_time"] <= as_of)]
        frame = frame[frame["game_date_utc"] < as_of]
        if start is not None:
            frame = frame[frame["game_date_utc"] >= start]
        if relievers_only:
            frame = frame[~frame["is_starter"].astype(bool)]
        return frame


def _appearance(player: int, day: datetime, pitches: int, *, is_starter: bool = False):
    return {
        "player_id": player,
        "team_id": TEAM,
        "game_date_utc": day,
        "knowledge_time": day + timedelta(hours=4),
        "is_starter": is_starter,
        "pitches_thrown": pitches,
        "batters_faced": max(1, pitches // 4),
        "so_pitched": max(0, pitches // 8),
        "bb_allowed": 0,
        "outs_pitched": 3,
    }


def _store_with(rows: list[dict]) -> _FakeStore:
    return _FakeStore(_relief_frame(rows))


NOW = datetime(2025, 6, 20, 23, 0, tzinfo=UTC)
SEASON_START = datetime(2025, 3, 20, tzinfo=UTC)


def test_a_pitcher_below_the_corps_threshold_is_not_counted():
    """One mop-up inning does not make a position player a reliever."""
    rows = [
        _appearance(9, NOW - timedelta(days=d), 15)
        for d in range(1, MIN_APPEARANCES_FOR_CORPS)
    ]
    statuses = bullpen_status(_store_with(rows), TEAM, NOW, SEASON_START, LEAGUE_K_BB)
    assert statuses == []


def test_a_real_reliever_is_classified_from_his_own_workload():
    rows = [_appearance(7, NOW - timedelta(days=d), 12) for d in (2, 5, 9, 14)]
    statuses = bullpen_status(_store_with(rows), TEAM, NOW, SEASON_START, LEAGUE_K_BB)
    assert len(statuses) == 1
    assert statuses[0].pitcher_id == 7
    assert statuses[0].availability == AVAILABLE
    assert statuses[0].pitches_last_1d == 0


def test_yesterdays_heavy_outing_reaches_the_classification():
    rows = [
        _appearance(7, NOW - timedelta(days=1), HEAVY_OUTING_PITCHES + 2),
        _appearance(7, NOW - timedelta(days=6), 10),
        _appearance(7, NOW - timedelta(days=11), 10),
    ]
    statuses = bullpen_status(_store_with(rows), TEAM, NOW, SEASON_START, LEAGUE_K_BB)
    assert statuses[0].availability == UNAVAILABLE
    assert statuses[0].pitches_last_1d == HEAVY_OUTING_PITCHES + 2


def test_starters_are_excluded_from_the_bullpen():
    rows = [
        _appearance(3, NOW - timedelta(days=d), 95, is_starter=True) for d in (2, 7, 12)
    ]
    statuses = bullpen_status(_store_with(rows), TEAM, NOW, SEASON_START, LEAGUE_K_BB)
    assert statuses == []


def test_tonights_relief_outing_cannot_reach_tonights_prediction():
    """The leakage case this feature group exists to get right.

    A reliever who pitches in the game being predicted must look exactly as he
    did before it. If tonight's appearance leaked in, an unavailable pitcher
    would be a near-perfect signal that his team already used its pen.
    """
    before = [_appearance(7, NOW - timedelta(days=d), 10) for d in (2, 6, 11)]
    tonight = _appearance(7, NOW + timedelta(hours=1), HEAVY_OUTING_PITCHES + 20)
    without = bullpen_status(_store_with(before), TEAM, NOW, SEASON_START, LEAGUE_K_BB)
    with_tonight = bullpen_status(
        _store_with([*before, tonight]), TEAM, NOW, SEASON_START, LEAGUE_K_BB
    )
    assert with_tonight == without


# --------------------------------------------------------------------------
# The team-level summary
# --------------------------------------------------------------------------


def test_summarize_returns_none_for_an_empty_corps():
    """No bullpen on record is an absence, not a bullpen of size zero."""
    assert summarize([]) is None


def test_available_count_excludes_the_unavailable_and_the_limited():
    statuses = [
        RelieverStatus(1, AVAILABLE, 0, 0, 0, 0, 0, 0, 0.20),
        RelieverStatus(2, LIMITED, 0, 0, 0, 0, 0, 0, 0.15),
        RelieverStatus(3, UNAVAILABLE, 0, 0, 0, 0, 0, 0, 0.10),
    ]
    summary = summarize(statuses)
    assert summary.corps_size == 3
    assert summary.available_count == 1


def test_available_quality_averages_only_those_who_can_pitch():
    statuses = [
        RelieverStatus(1, AVAILABLE, 0, 0, 0, 0, 0, 0, 0.20),
        RelieverStatus(2, LIMITED, 0, 0, 0, 0, 0, 0, 0.10),
        RelieverStatus(3, UNAVAILABLE, 0, 0, 0, 0, 0, 0, 0.90),
    ]
    summary = summarize(statuses)
    # The 0.90 arm cannot pitch, so it must not raise the number.
    assert summary.available_quality == pytest.approx(0.15)


def test_best_reliever_availability_is_graded_not_binary():
    statuses = [
        RelieverStatus(1, LIMITED, 0, 0, 0, 0, 0, 0, 0.30),
        RelieverStatus(2, AVAILABLE, 0, 0, 0, 0, 0, 0, 0.10),
    ]
    assert summarize(statuses).best_reliever_available == 0.5


def test_best_reliever_is_the_best_rated_not_the_most_used():
    statuses = [
        RelieverStatus(1, UNAVAILABLE, 0, 0, 0, 0, 0, 0, 0.35),
        RelieverStatus(2, AVAILABLE, 0, 0, 0, 0, 0, 0, 0.05),
    ]
    assert summarize(statuses).best_reliever_available == 0.0


def test_unrated_relievers_do_not_become_a_zero_quality():
    """A pitcher with no rating is unmeasured, and averaging him in as 0 would
    be exactly the placeholder this repository forbids."""
    statuses = [
        RelieverStatus(1, AVAILABLE, 0, 0, 0, 0, 0, 0, None),
        RelieverStatus(2, AVAILABLE, 0, 0, 0, 0, 0, 0, 0.20),
    ]
    assert summarize(statuses).available_quality == pytest.approx(0.20)


def test_a_corps_with_no_ratings_reports_absence_rather_than_zero():
    statuses = [RelieverStatus(1, AVAILABLE, 0, 0, 0, 0, 0, 0, None)]
    summary = summarize(statuses)
    assert summary.available_quality is None
    assert summary.best_reliever_available is None
