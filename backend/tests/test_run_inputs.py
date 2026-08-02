"""Park factors, the starter/bullpen split, and how they enter the run model."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.features.park import K_PARK_GAMES, MIN_GAMES_EACH_WAY, ParkFactors
from app.modeling.run_inputs import (
    BASE,
    MULTIPLIER_CEILING,
    MULTIPLIER_FLOOR,
    NO_SPLIT,
    PARK_AND_PITCHING,
    PARK_ONLY,
    PITCHING_ONLY,
    PitchingSplit,
    RunComponents,
    pitching_split,
)
from tests.conftest import make_store

SEASON = 2024
START = datetime(SEASON, 4, 1, 23, 0, tzinfo=UTC)
SEASON_START = datetime(SEASON, 1, 1, tzinfo=UTC)
RESULT_LAG = timedelta(hours=3, minutes=30)

HOME, AWAY, THIRD = 100, 200, 300
HOME_PARK, AWAY_PARK, NEUTRAL_PARK = 500, 501, 599
ACE, REPLACEMENT, RELIEVER = 9001, 9002, 9101


# --------------------------------------------------------------------------
# Frame builders. Synthetic fixtures, never data served to anyone.
# --------------------------------------------------------------------------


def _park_world(
    n_home: int, n_road: int, home_total: int, road_total: int, venue: int = HOME_PARK
) -> dict[str, pd.DataFrame]:
    """A world where team HOME scores ``home_total`` runs per game at home.

    Both team rows of a game carry the same total, so the split between the two
    sides is irrelevant to a park factor and is kept even.
    """
    games, lines = [], []
    for i in range(n_home + n_road):
        at_home = i < n_home
        total = home_total if at_home else road_total
        first_pitch = START + timedelta(days=i)
        game = {
            "id": 1000 + i, "season": SEASON, "game_type": "R",
            "game_date_utc": first_pitch, "official_date": first_pitch.date(),
            "home_team_id": HOME if at_home else AWAY,
            "away_team_id": AWAY if at_home else HOME,
            "venue_id": venue if at_home else AWAY_PARK,
            "day_night": "night", "doubleheader": "N", "game_number": 1,
            "home_score": total // 2, "away_score": total - total // 2,
            "home_win": True, "is_final": True, "innings_played": 9,
            "knowledge_time": first_pitch + RESULT_LAG,
            "home_probable_pitcher_id": ACE, "away_probable_pitcher_id": ACE,
        }
        games.append(game)
        for team, opponent, is_home in (
            (HOME, AWAY, at_home), (AWAY, HOME, not at_home)
        ):
            lines.append({
                "game_id": game["id"], "team_id": team, "opponent_team_id": opponent,
                "is_home": is_home, "game_date_utc": first_pitch,
                "knowledge_time": game["knowledge_time"],
                "runs": total / 2, "runs_allowed": total / 2, "outs_pitched": 27,
            })
    return {
        "games": pd.DataFrame(games),
        "team_games": pd.DataFrame(lines),
    }


def _factors(frames: dict[str, pd.DataFrame]) -> ParkFactors:
    return ParkFactors(frames["games"], frames["team_games"])


AFTER_EVERYTHING = START + timedelta(days=400)


# --------------------------------------------------------------------------
# Park factors
# --------------------------------------------------------------------------


def test_a_hitters_park_is_measured_as_one():
    """Twelve runs a game at home against eight on the road is a factor of 1.5."""
    pf = _factors(_park_world(60, 60, 12, 8)).factor(HOME, AFTER_EVERYTHING)
    assert pf.is_measured
    assert pf.raw == pytest.approx(1.5)
    # Shrunk toward neutral by the pre-registered constant, never past it.
    assert 1.0 < pf.value < pf.raw


def test_shrinkage_is_exactly_the_constant_it_claims_to_be():
    pf = _factors(_park_world(K_PARK_GAMES, K_PARK_GAMES, 12, 8)).factor(
        HOME, AFTER_EVERYTHING
    )
    # Equal games and constant means the raw factor is trusted exactly halfway.
    assert pf.value == pytest.approx(1.0 + (1.5 - 1.0) * 0.5)


def test_a_smaller_sample_lands_closer_to_neutral():
    big = _factors(_park_world(120, 120, 12, 8)).factor(HOME, AFTER_EVERYTHING)
    small = _factors(_park_world(15, 15, 12, 8)).factor(HOME, AFTER_EVERYTHING)
    assert big.raw == pytest.approx(small.raw)
    assert abs(small.value - 1.0) < abs(big.value - 1.0)


def test_too_few_games_reports_no_factor_rather_than_a_neutral_one():
    """UNAVAILABLE and EVEN are different states, and a park is no exception.

    A park with nine home games has not been measured. Returning a shrunk value
    near 1.0 would read downstream as "measured, and this park is average", which
    is a claim the sample cannot support.
    """
    pf = _factors(_park_world(MIN_GAMES_EACH_WAY - 1, 60, 12, 8)).factor(
        HOME, AFTER_EVERYTHING
    )
    assert not pf.is_measured
    assert pf.raw is None
    assert pf.value == 1.0


def test_a_game_after_the_cut_cannot_reach_the_factor():
    """The as-of rule. A park factor may not contain the game it helps predict."""
    frames = _park_world(60, 60, 12, 8)
    parks = _factors(frames)
    early = parks.factor(HOME, START + timedelta(days=30))
    late = parks.factor(HOME, AFTER_EVERYTHING)
    assert early.home_games < late.home_games
    # And at the very start there is nothing at all to measure.
    assert not parks.factor(HOME, START - timedelta(days=1)).is_measured


def test_a_neutral_site_game_gets_no_park_factor():
    """A team's home/road split describes its own building and no other."""
    parks = _factors(_park_world(60, 60, 12, 8))
    own = parks.for_game(HOME, HOME_PARK, AFTER_EVERYTHING)
    elsewhere = parks.for_game(HOME, NEUTRAL_PARK, AFTER_EVERYTHING)
    assert own.is_measured
    assert not elsewhere.is_measured
    assert elsewhere.value == 1.0


def test_exposure_is_the_average_park_a_team_has_played_in():
    frames = _park_world(60, 60, 12, 8)
    parks = _factors(frames)
    played = frames["team_games"]
    played = played[played["team_id"] == HOME]
    played = played.assign(
        knowledge_time=pd.to_datetime(played["knowledge_time"], utc=True)
    )
    exposure = parks.exposure(HOME, played, AFTER_EVERYTHING)
    own = parks.factor(HOME, AFTER_EVERYTHING).value
    other = parks.factor(AWAY, AFTER_EVERYTHING).value
    # Half its games are in its own park and half in the opponent's, so exposure
    # is the mean of the two — and in this world those two are reciprocal, which
    # is why a team splitting its schedule between them comes out near neutral
    # while its own park is emphatically not.
    assert exposure == pytest.approx((own + other) / 2, rel=0.001)
    assert own > 1.25 > 1.0 > other


def test_an_empty_world_produces_no_factors_and_does_not_raise():
    parks = ParkFactors(pd.DataFrame(), pd.DataFrame())
    assert not parks.is_available
    assert parks.factor(HOME, AFTER_EVERYTHING).value == 1.0
    assert parks.exposure(HOME, pd.DataFrame(), AFTER_EVERYTHING) == 1.0


# --------------------------------------------------------------------------
# How the park enters the run model
# --------------------------------------------------------------------------


def _components(**kwargs) -> RunComponents:
    base = {"home": 4.5, "away": 4.5, "league": 4.5, "home_games": 50, "away_games": 50}
    return RunComponents(**(base | kwargs))


def test_the_product_form_squares_a_park_and_the_adjustment_unsquares_it():
    """The reason the adjustment divides by both exposures rather than one.

    Expected runs are `offence × defence ÷ league`. In a park that inflates
    scoring by f, BOTH the offence rate and the opposing defence rate come out
    inflated by f, so their product carries f². The base model therefore
    over-states a hitters' park rather than merely failing to model it, and the
    correction has to remove the square before applying the park once.
    """
    f, neutral = 1.20, 4.5
    c = _components(
        home=neutral * f**2, away=neutral * f**2,
        park_factor=f, home_exposure=f, away_exposure=f, park_measured=True,
    )
    assert c.means(BASE)[0] == pytest.approx(neutral * f**2)
    assert c.means(PARK_ONLY)[0] == pytest.approx(neutral * f)


def test_a_neutral_world_leaves_the_run_model_exactly_unchanged():
    """The property that makes this a refinement and not a rewrite."""
    c = _components(home=4.8, away=4.2)
    assert c.means(PARK_ONLY) == c.means(BASE)
    assert c.means(PITCHING_ONLY) == c.means(BASE)
    assert c.means(PARK_AND_PITCHING) == c.means(BASE)


def test_a_park_moves_both_sides_by_the_same_factor():
    """A park acts on runs, not on teams. It cannot favour one dugout."""
    c = _components(home=5.0, away=4.0, park_factor=1.15, park_measured=True)
    home, away = c.means(PARK_ONLY)
    assert home / 5.0 == pytest.approx(away / 4.0)


def test_a_zero_exposure_cannot_divide_the_model_by_nothing():
    c = _components(park_factor=1.1, home_exposure=0.0, park_measured=True)
    assert c.park_adjustment == 1.0


# --------------------------------------------------------------------------
# The starter/bullpen split
# --------------------------------------------------------------------------


def _pitching_world(
    n_games: int, starter_runs: float, relief_runs: float, starter_id: int = ACE
) -> dict[str, pd.DataFrame]:
    """A team whose starter and bullpen each allow a fixed number per outing.

    Six innings from the starter and three from the bullpen, every game, so the
    innings share is exactly two thirds and the arithmetic is checkable by hand.
    """
    games, team_lines, pitcher_lines = [], [], []
    for i in range(n_games):
        first_pitch = START + timedelta(days=i)
        game = {
            "id": 1000 + i, "season": SEASON, "game_type": "R",
            "game_date_utc": first_pitch, "official_date": first_pitch.date(),
            "home_team_id": HOME, "away_team_id": AWAY, "venue_id": HOME_PARK,
            "day_night": "night", "doubleheader": "N", "game_number": 1,
            "home_score": 4, "away_score": 3, "home_win": True, "is_final": True,
            "innings_played": 9, "knowledge_time": first_pitch + RESULT_LAG,
            "home_probable_pitcher_id": starter_id, "away_probable_pitcher_id": ACE,
        }
        games.append(game)
        total = starter_runs + relief_runs
        team_lines.append({
            "game_id": game["id"], "team_id": HOME, "opponent_team_id": AWAY,
            "is_home": True, "game_date_utc": first_pitch,
            "knowledge_time": game["knowledge_time"],
            "runs": 4, "runs_allowed": total, "outs_pitched": 27,
        })
        for player, starter, outs, runs in (
            (starter_id, True, 18, starter_runs), (RELIEVER, False, 9, relief_runs)
        ):
            pitcher_lines.append({
                "game_id": game["id"], "player_id": player, "team_id": HOME,
                "opponent_team_id": AWAY, "game_date_utc": first_pitch,
                "knowledge_time": game["knowledge_time"], "is_home": True,
                "role": "pitcher", "is_starter": starter, "position": "P",
                "outs_pitched": outs, "runs_allowed": runs, "earned_runs": runs,
                "batters_faced": 24 if starter else 12,
            })
    return {
        "games": pd.DataFrame(games),
        "team_games": pd.DataFrame(team_lines),
        "pitcher_games": pd.DataFrame(pitcher_lines),
        "players": pd.DataFrame([
            {"id": ACE, "full_name": "Ace", "pitch_hand": "R", "bat_side": "R"},
            {"id": REPLACEMENT, "full_name": "Fifth Starter", "pitch_hand": "R",
             "bat_side": "R"},
            {"id": RELIEVER, "full_name": "Reliever", "pitch_hand": "R", "bat_side": "R"},
        ]),
        "ballparks": pd.DataFrame([
            {"id": HOME_PARK, "name": "Home Park", "latitude": 40.0, "longitude": -74.0,
             "elevation_ft": 20, "roof_type": "Open", "timezone": "America/New_York"},
        ]),
    }


def _split(frames, starter_id=ACE) -> PitchingSplit:
    return pitching_split(
        make_store(frames), HOME, starter_id, AFTER_EVERYTHING, SEASON_START
    )


def test_no_starter_named_is_not_an_average_starter():
    """A missing probable pitcher is an absence of knowledge, not a finding."""
    split = pitching_split(
        make_store(_pitching_world(40, 2, 1)), HOME, None, AFTER_EVERYTHING, SEASON_START
    )
    assert split is NO_SPLIT
    assert split.multiplier == 1.0
    assert not split.is_measured


def test_a_starter_who_matches_his_own_staff_moves_nothing():
    """The identity the decomposition rests on.

    Runs allowed per nine are equal for the starter and the bullpen here, so the
    team rate IS both of them and substituting one for the other must be exactly
    neutral. If this drifts, the split has stopped reconstructing what it splits.
    """
    # Two runs in six innings and one in three are both 3.00 per nine.
    split = _split(_pitching_world(40, 2, 1))
    assert split.is_measured
    assert split.multiplier == pytest.approx(1.0, abs=1e-9)


def test_a_better_starter_lowers_the_opponents_expected_runs():
    split = _split(_pitching_world(40, 1, 2))
    assert split.is_measured
    assert split.multiplier < 1.0
    assert split.starter_runs_per_9 < split.bullpen_runs_per_9


def test_a_worse_starter_raises_them():
    assert _split(_pitching_world(40, 4, 1)).multiplier > 1.0


def test_the_starters_share_of_the_innings_is_measured_not_assumed():
    """Six innings of nine is two thirds, and the fixture says so on purpose."""
    split = _split(_pitching_world(40, 2, 1))
    assert split.starter_share == pytest.approx(2 / 3, rel=0.02)


def test_a_starter_with_no_starts_yet_is_unmeasured_rather_than_neutral():
    split = _split(_pitching_world(40, 2, 1), starter_id=REPLACEMENT)
    assert not split.is_measured
    assert split.multiplier == 1.0
    assert split.reason is not None
    # The bullpen half is still known, and saying so is the point of reporting it.
    assert split.bullpen_runs_per_9 is not None


def test_a_team_with_no_history_cannot_anchor_a_split():
    split = _split(_pitching_world(3, 2, 1))
    assert not split.is_measured
    assert split.multiplier == 1.0


def test_a_short_record_is_shrunk_harder_than_a_long_one():
    """Sample size has to move the answer, or the shrinkage is decorative."""
    short = _split(_pitching_world(4, 1, 3))
    long = _split(_pitching_world(80, 1, 3))
    assert abs(short.multiplier - 1.0) < abs(long.multiplier - 1.0)


def test_the_multiplier_cannot_leave_its_band():
    """One freak record must not hand a team a two-run edge."""
    extreme = _split(_pitching_world(40, 0, 6))
    assert extreme.multiplier >= MULTIPLIER_FLOOR
    assert _split(_pitching_world(40, 12, 0)).multiplier <= MULTIPLIER_CEILING


def test_a_game_after_the_cut_cannot_reach_the_split():
    frames = _pitching_world(40, 1, 3)
    early = pitching_split(
        make_store(frames), HOME, ACE, START + timedelta(days=2), SEASON_START
    )
    assert not early.is_measured


# --------------------------------------------------------------------------
# How pitching enters the run model
# --------------------------------------------------------------------------


def test_a_teams_pitching_moves_the_other_side_of_the_scoreboard():
    """The wiring that would be invisible if it were reversed.

    The home team's pitchers decide what the AWAY team scores. A crossed pair of
    multipliers still produces plausible numbers on every game and would show up
    only as a model that mysteriously fails to work.
    """
    strong_home_staff = PitchingSplit(0.5, True)
    c = _components(home=4.5, away=4.5, home_pitching=strong_home_staff)
    home, away = c.means(PITCHING_ONLY)
    assert away == pytest.approx(4.5 * 0.5)
    assert home == pytest.approx(4.5)


def test_park_and_pitching_compose_without_interfering():
    c = _components(
        home=4.5, away=4.5,
        park_factor=1.2, park_measured=True,
        home_pitching=PitchingSplit(0.9, True),
        away_pitching=PitchingSplit(1.1, True),
    )
    home, away = c.means(PARK_AND_PITCHING)
    assert home == pytest.approx(4.5 * 1.1 * 1.2)
    assert away == pytest.approx(4.5 * 0.9 * 1.2)
