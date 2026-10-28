"""The named starter against his own rotation, and how it enters the run model."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.features.builder import FeatureBuilder
from app.features.context import GameContext
from app.features.projections import K_PITCHER_INNINGS, Projections, StarterEdge
from app.modeling.run_inputs import (
    MULTIPLIER_CEILING,
    MULTIPLIER_FLOOR,
    NO_STARTER,
    PROJECTED,
    PROJECTED_STARTER,
    SERVED,
    RunComponents,
    StarterAdjustment,
    starter_adjustment,
)
from app.modeling.serving import dispersion_asof, serve_probability
from app.modeling.simulation import run_components, simulate_slate
from tests.conftest import make_store

HOME, AWAY = 100, 200
PARK = 500
ACE, FIFTH, MIDDLE, ROOKIE = 9001, 9002, 9003, 9004
HOME_RP, AWAY_RP = 9101, 9102
RESULT_LAG = timedelta(hours=3, minutes=30)

# Six innings a start. The ace strikes out nine and walks one; the fifth
# starter strikes out three, walks three and gives up two home runs.
LINES = {
    ACE: {"so_pitched": 9, "bb_allowed": 1, "hr_allowed": 0},
    FIFTH: {"so_pitched": 3, "bb_allowed": 3, "hr_allowed": 2},
    MIDDLE: {"so_pitched": 6, "bb_allowed": 2, "hr_allowed": 1},
}
RELIEF = {"so_pitched": 3, "bb_allowed": 1, "hr_allowed": 0}


def _world(seasons: tuple[int, ...] = (2023, 2024), games_per_season: int = 40,
           extra: list[tuple[datetime, int, dict]] | None = None) -> dict[str, pd.DataFrame]:
    """HOME alternates ACE and FIFTH; AWAY always starts MIDDLE.

    ``extra`` appends (first pitch, starter, line) games for HOME, which is how
    a test puts a start on the far side of an as-of cut.
    """
    schedule = []
    for season in seasons:
        opening = datetime(season, 4, 1, 23, 0, tzinfo=UTC)
        for i in range(games_per_season):
            starter = ACE if i % 2 == 0 else FIFTH
            schedule.append((opening + timedelta(days=i), starter, LINES[starter]))
    schedule.extend(extra or [])

    games, team_lines, pitcher_lines = [], [], []
    for n, (first_pitch, home_starter, home_line) in enumerate(schedule):
        game = {
            "id": 1000 + n, "season": first_pitch.year, "game_type": "R",
            "game_date_utc": first_pitch, "official_date": first_pitch.date(),
            "home_team_id": HOME, "away_team_id": AWAY, "venue_id": PARK,
            "day_night": "night", "doubleheader": "N", "game_number": 1,
            "home_score": 4, "away_score": 3, "home_win": True, "is_final": True,
            "innings_played": 9, "knowledge_time": first_pitch + RESULT_LAG,
            "home_probable_pitcher_id": home_starter, "away_probable_pitcher_id": MIDDLE,
        }
        games.append(game)
        for team, opponent, is_home, runs, allowed in (
            (HOME, AWAY, True, 4, 3), (AWAY, HOME, False, 3, 4)
        ):
            team_lines.append({
                "game_id": game["id"], "team_id": team, "opponent_team_id": opponent,
                "is_home": is_home, "game_date_utc": first_pitch,
                "knowledge_time": game["knowledge_time"],
                "runs": runs, "runs_allowed": allowed, "outs_pitched": 27,
            })
        for player, team, opponent, is_home, starter, line in (
            (home_starter, HOME, AWAY, True, True, home_line),
            (HOME_RP, HOME, AWAY, True, False, RELIEF),
            (MIDDLE, AWAY, HOME, False, True, LINES[MIDDLE]),
            (AWAY_RP, AWAY, HOME, False, False, RELIEF),
        ):
            pitcher_lines.append({
                "game_id": game["id"], "player_id": player, "team_id": team,
                "opponent_team_id": opponent, "game_date_utc": first_pitch,
                "knowledge_time": game["knowledge_time"], "is_home": is_home,
                "role": "pitcher", "is_starter": starter, "position": "P",
                "outs_pitched": 18 if starter else 9,
                "batters_faced": 25 if starter else 12,
                "runs_allowed": 3 if starter else 1,
                "earned_runs": 2 if starter else 1,
                "hbp_allowed": 0,
                **line,
            })
    names = {ACE: "Ace", FIFTH: "Fifth", MIDDLE: "Middle", ROOKIE: "Rookie",
             HOME_RP: "Home RP", AWAY_RP: "Away RP"}
    return {
        "games": pd.DataFrame(games),
        "team_games": pd.DataFrame(team_lines),
        "pitcher_games": pd.DataFrame(pitcher_lines),
        "players": pd.DataFrame([
            {"id": i, "full_name": n, "pitch_hand": "R", "bat_side": "R"}
            for i, n in names.items()
        ]),
        "ballparks": pd.DataFrame([
            {"id": PARK, "name": "Park", "latitude": 40.0, "longitude": -74.0,
             "elevation_ft": 20, "roof_type": "Open", "timezone": "America/New_York"},
        ]),
    }


AS_OF = datetime(2024, 5, 20, 12, 0, tzinfo=UTC)  # after all forty 2024 games


def _edge(frames, starter, team_evidence=100.0, as_of=AS_OF, season=2024):
    projections = Projections(make_store(frames))
    league_now = projections.season_rates(season, as_of).fip_numerator_per_inning
    return projections.starter_edge(HOME, starter, season, as_of, league_now, team_evidence)


# --- the edge -----------------------------------------------------------------


def test_the_ace_is_better_than_his_rotation_and_the_fifth_starter_worse():
    world = _world()
    ace, fifth = _edge(world, ACE), _edge(world, FIFTH)
    assert ace.fip_delta < 0 < fifth.fip_delta
    assert ace.runs_delta < 0 < fifth.runs_delta


def test_the_rotation_is_one_reference_for_every_starter_on_it():
    """How hard the team projection regresses its rotation moves both starters
    by the same amount, and never the distance between them."""
    world = _world()
    for evidence in (0.0, 50.0, 400.0):
        ace, fifth = _edge(world, ACE, evidence), _edge(world, FIFTH, evidence)
        gap = fifth.fip_delta - ace.fip_delta
        assert gap == pytest.approx(_edge(world, FIFTH, 0.0).fip_delta
                                    - _edge(world, ACE, 0.0).fip_delta)


def test_the_starter_is_regressed_by_the_projections_own_constant():
    # Same two-season record, every start the same line, so the regression is
    # the only thing between the raw gap and the reported one.
    world = _world()
    ace = _edge(world, ACE, 0.0)
    weight = ace.starter_evidence / (ace.starter_evidence + K_PITCHER_INNINGS)
    assert 0 < weight < 1
    # 20 starts of six innings this season, 20 last season at weight 0.6.
    assert ace.starter_evidence == pytest.approx(20 * 6 + 0.6 * 20 * 6)


def test_a_starter_nobody_has_seen_is_his_rotations_average_start():
    edge = _edge(_world(), ROOKIE)
    assert edge.starter_evidence == 0
    assert edge.fip_delta == 0.0
    adjustment = starter_adjustment(edge, 4.0)
    assert adjustment.multiplier == 1.0
    assert not adjustment.is_measured


def test_no_starter_named_is_an_absence_not_an_average_starter():
    assert _edge(_world(), None) is None
    adjustment = starter_adjustment(None, 4.0)
    assert adjustment.multiplier == 1.0
    assert not adjustment.is_measured
    assert adjustment.reason == "no starter named"


def test_a_start_after_the_cut_cannot_reach_the_edge():
    blowup = {"so_pitched": 0, "bb_allowed": 8, "hr_allowed": 6}
    later = AS_OF + timedelta(days=1)
    before = _edge(_world(), ACE)
    after = _edge(_world(extra=[(later, ACE, blowup)]), ACE)
    assert after.fip_delta == pytest.approx(before.fip_delta)
    assert after.starter_evidence == pytest.approx(before.starter_evidence)


def test_without_a_completed_season_there_is_no_conversion_to_runs():
    """Runs per earned run and starter depth come from the last completed
    season, so a store whose first season is the current one has nothing to
    convert a FIP gap with, and says so rather than assuming."""
    assert _edge(_world(seasons=(2024,)), ACE) is None


def test_depth_is_the_starters_own_regressed_toward_the_league():
    edge = _edge(_world(), ACE)
    # Every start in the world lasts six innings, so the league's is six too.
    assert edge.innings == pytest.approx(6.0)
    assert edge.runs_per_earned_run == pytest.approx((3 + 1) * 2 / ((2 + 1) * 2))


# --- the multiplier -----------------------------------------------------------


def _synthetic(fip_delta: float, innings: float = 6.0, ratio: float = 1.1) -> StarterEdge:
    return StarterEdge(fip_delta, innings, 300.0, 600.0, ratio)


def test_runs_delta_is_the_fip_gap_over_the_innings_he_covers():
    edge = _synthetic(-0.9, innings=6.0, ratio=1.1)
    assert edge.runs_delta == pytest.approx(1.1 * -0.9 * 6.0 / 9.0)


def test_the_multiplier_moves_runs_allowed_by_exactly_the_runs_delta():
    edge = _synthetic(-0.9)
    adjustment = starter_adjustment(edge, 4.4)
    assert adjustment.is_measured
    assert adjustment.multiplier * 4.4 == pytest.approx(4.4 + edge.runs_delta)


def test_the_multiplier_is_clamped_to_the_run_models_band():
    assert starter_adjustment(_synthetic(-30.0), 4.4).multiplier == MULTIPLIER_FLOOR
    assert starter_adjustment(_synthetic(30.0), 4.4).multiplier == MULTIPLIER_CEILING


def test_no_run_prevention_rate_means_no_adjustment():
    adjustment = starter_adjustment(_synthetic(-0.9), None)
    assert adjustment.multiplier == 1.0
    assert not adjustment.is_measured


def test_the_variant_acts_on_the_opposing_side_and_only_the_variant_does():
    components = RunComponents(
        home=4.5, away=4.5, league=4.5, home_games=50, away_games=50,
        home_projected=4.8, away_projected=4.2,
        home_starter=StarterAdjustment(0.9, True), away_starter=StarterAdjustment(1.1, True),
    )
    assert components.means(PROJECTED) == (4.8, 4.2)
    home, away = components.means(PROJECTED_STARTER)
    # The away starter decides what the home side scores, and vice versa.
    assert home == pytest.approx(4.8 * 1.1)
    assert away == pytest.approx(4.2 * 0.9)


def test_an_unmeasured_starter_leaves_the_projection_exactly_where_it_was():
    components = RunComponents(
        home=4.5, away=4.5, league=4.5, home_games=50, away_games=50,
        home_projected=4.8, away_projected=4.2,
        home_starter=NO_STARTER, away_starter=NO_STARTER,
    )
    assert components.means(PROJECTED_STARTER) == components.means(PROJECTED)


# --- wired through the run model ----------------------------------------------


def _components_for(home_starter: int, starters: bool = True) -> RunComponents:
    frames = _world(extra=[(datetime(2024, 5, 21, 23, 0, tzinfo=UTC), home_starter,
                            LINES.get(home_starter, LINES[MIDDLE]))])
    store = make_store(frames)
    row = frames["games"].iloc[-1].to_dict()
    ctx = GameContext.from_row(row)
    builder = FeatureBuilder(store)
    as_of = ctx.first_pitch_utc - timedelta(hours=3)
    components = run_components(store, builder, ctx, as_of, starters=starters)
    assert components is not None
    return components


def test_the_ace_lowers_the_visitors_runs_and_the_fifth_starter_raises_them():
    ace = _components_for(ACE)
    fifth = _components_for(FIFTH)
    assert ace.home_starter.is_measured and fifth.home_starter.is_measured
    assert ace.home_starter.multiplier < 1.0 < fifth.home_starter.multiplier
    assert ace.means(PROJECTED_STARTER)[1] < ace.means(PROJECTED)[1]
    assert fifth.means(PROJECTED_STARTER)[1] > fifth.means(PROJECTED)[1]


def test_a_caller_without_the_variant_does_not_pay_for_it():
    components = _components_for(ACE, starters=False)
    assert components.home_starter is NO_STARTER
    assert components.away_starter is NO_STARTER


def test_serving_simulates_the_starter_exactly_as_the_measurement_did():
    """`serve_probability` reaches the run means by its own path. Where the
    starters are measured — the case the one-season serving fixture cannot
    reach — it must still land on the measured variant's number, and that
    number must differ from the projection without them."""
    frames = _world(extra=[(datetime(2024, 5, 21, 23, 0, tzinfo=UTC), ACE, LINES[ACE])])
    store = make_store(frames)
    builder = FeatureBuilder(store)
    ctx = GameContext.from_row(frames["games"].iloc[-1].to_dict())
    as_of = ctx.first_pitch_utc - timedelta(hours=3)
    dispersion = dispersion_asof(store, as_of, min_sample=10)
    assert dispersion is not None

    slate = simulate_slate(
        store, builder, pd.DataFrame([{"game_id": ctx.game_id, "as_of": as_of}]),
        dispersion.size, 4000, models=(PROJECTED, SERVED),
    )
    served = serve_probability(
        store, builder, ctx, as_of, 0.55, dispersion=dispersion, simulations=4000
    )
    assert SERVED is PROJECTED_STARTER
    assert served.home_starter.is_measured and served.away_starter.is_measured
    assert served.simulation == pytest.approx(float(slate.iloc[0]["sim_prob"]), abs=1e-12)
    assert served.simulation == pytest.approx(
        float(slate.iloc[0][f"sim_{SERVED.name}"]), abs=1e-12
    )
    # The ace is pitching for the home side, so it is a better bet with him.
    assert served.simulation > float(slate.iloc[0]["sim_projected"])
