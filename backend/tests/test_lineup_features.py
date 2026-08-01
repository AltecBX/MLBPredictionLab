"""Projected lineups, batter profiles and the arsenal matchup.

Frames here are TEST FIXTURES built in memory — explicitly synthetic, never
served and never written to the application database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.features import lineup_features as lf
from app.features.batter_agg import PITCH_FAMILIES
from app.features.registry import LINEUP, REGISTRY, feature_keys

AS_OF = datetime(2025, 7, 1, 20, 0, tzinfo=UTC)
SEASON_START = datetime(2025, 1, 1, tzinfo=UTC)
TEAM = 111


def _order_rows(
    players: list[int], games: int, *, start_day: int = 1, team: int = TEAM
) -> pd.DataFrame:
    """`games` completed starts for a fixed nine, one per day."""
    rows = []
    for g in range(games):
        when = AS_OF - timedelta(days=start_day + g)
        for slot, player in enumerate(players, start=1):
            rows.append(
                {
                    "game_id": 900000 + g,
                    "team_id": team,
                    "player_id": player,
                    "batting_order_slot": slot,
                    "game_date_utc": when,
                    "knowledge_time": when + timedelta(hours=3),
                }
            )
    return pd.DataFrame(rows).sort_values("knowledge_time").reset_index(drop=True)


NINE = [101, 102, 103, 104, 105, 106, 107, 108, 109]


# --------------------------------------------------------------------------
# Projection
# --------------------------------------------------------------------------


def test_no_history_projects_no_lineup():
    projected = lf.project_lineup(pd.DataFrame(), AS_OF)
    assert projected.is_empty
    assert projected.continuity is None


def test_a_settled_nine_is_projected_in_its_usual_order():
    projected = lf.project_lineup(_order_rows(NINE, 10), AS_OF)
    assert [s.player_id for s in projected.slots] == NINE
    assert [s.slot for s in projected.slots] == list(range(1, 10))


def test_slot_weights_come_from_the_measured_table():
    projected = lf.project_lineup(_order_rows(NINE, 10), AS_OF)
    assert projected.slots[0].weight == lf.EXPECTED_PA[1]
    assert projected.slots[-1].weight == lf.EXPECTED_PA[9]
    # Leadoff takes about one more trip a game than ninth. That gap is the whole
    # reason for weighting at all.
    assert projected.slots[0].weight - projected.slots[-1].weight == pytest.approx(1.006)


def test_at_most_nine_are_projected():
    """A month of starts names more than nine players; a lineup is still nine."""
    frames = [_order_rows(NINE, 8)]
    bench = _order_rows([201, 202, 203, 204, 205, 206, 207, 208, 209], 3, start_day=9)
    projected = lf.project_lineup(
        pd.concat([*frames, bench]).reset_index(drop=True), AS_OF
    )
    assert len(projected.slots) == 9
    # The regulars, not the fill-ins.
    assert {s.player_id for s in projected.slots} == set(NINE)


def test_a_player_who_stopped_starting_drops_out():
    """The projection follows recent form, not the season's cumulative total."""
    old = _order_rows([*NINE[:8], 999], 6, start_day=15)
    recent = _order_rows([*NINE[:8], 110], 6, start_day=1)
    projected = lf.project_lineup(pd.concat([old, recent]).reset_index(drop=True), AS_OF)
    ids = {s.player_id for s in projected.slots}
    assert 110 in ids
    assert 999 not in ids


def test_nothing_outside_the_projection_window_is_used():
    stale = _order_rows(NINE, 5, start_day=lf.PROJECTION_DAYS + 5)
    assert lf.project_lineup(stale, AS_OF).is_empty


def test_continuity_reports_how_much_of_the_last_lineup_is_projected():
    settled = lf.project_lineup(_order_rows(NINE, 10), AS_OF)
    assert settled.continuity == pytest.approx(1.0)

    # One change in the most recent game. The blocks need distinct game ids, and
    # the frame has to be in knowledge order, because "most recent game" is read
    # off the tail the same way the as-of slice hands it over.
    older = _order_rows(NINE, 8, start_day=2)
    latest = _order_rows([*NINE[:8], 555], 1, start_day=1)
    latest["game_id"] = 999999
    churned = (
        pd.concat([older, latest]).sort_values("knowledge_time").reset_index(drop=True)
    )
    assert lf.project_lineup(churned, AS_OF).continuity == pytest.approx(8 / 9)


# --------------------------------------------------------------------------
# Batter profiles and shrinkage
# --------------------------------------------------------------------------


LEAGUE = lf.LeagueBatting(
    xwoba=0.320,
    whiff_pct=0.24,
    family_xwoba=(("fb", 0.340), ("br", 0.280), ("os", 0.290)),
    family_whiff=(("fb", 0.17), ("br", 0.31), ("os", 0.30)),
)


def _batter_rows(
    n_games: int = 60,
    *,
    pa: float = 4.0,
    xwoba: float = 0.320,
    swings: float = 8.0,
    whiff_rate: float = 0.24,
    family_xwoba: dict[str, float] | None = None,
) -> pd.DataFrame:
    family_xwoba = family_xwoba or {k: xwoba for k in PITCH_FAMILIES}
    rows = []
    for g in range(n_games):
        when = AS_OF - timedelta(days=g + 1)
        row = {
            "game_id": 800000 + g,
            "player_id": 101,
            "game_date_utc": when,
            "knowledge_time": when + timedelta(hours=3),
            "woba_denom": pa,
            "xwoba_num": xwoba * pa,
            "swings": swings,
            "whiffs": swings * whiff_rate,
            "pa_vs_l": pa / 3,
            "xwoba_num_vs_l": xwoba * pa / 3,
            "pa_vs_r": pa * 2 / 3,
            "xwoba_num_vs_r": xwoba * pa * 2 / 3,
        }
        for key in PITCH_FAMILIES:
            row[f"{key}_pa"] = pa / 3
            row[f"{key}_xwoba_num"] = family_xwoba[key] * pa / 3
            row[f"{key}_swings"] = swings / 3
            row[f"{key}_whiffs"] = swings / 3 * whiff_rate
        rows.append(row)
    return pd.DataFrame(rows)


def test_a_batter_with_no_record_has_no_profile():
    profile = lf.batter_profile(pd.DataFrame(), "R", LEAGUE)
    assert profile.xwoba is None
    assert profile.plate_appearances == 0.0


def test_a_short_record_is_pulled_toward_the_league():
    profile = lf.batter_profile(_batter_rows(3, xwoba=0.500), "R", LEAGUE)
    assert LEAGUE.xwoba < profile.xwoba < 0.500
    assert abs(profile.xwoba - LEAGUE.xwoba) < abs(profile.xwoba - 0.500)


def test_a_bigger_sample_is_left_nearer_what_was_observed():
    """Shrinkage is monotone in evidence, and never disappears entirely.

    A full season is ~560 plate appearances against k=220, which is a deliberate
    28% pull toward the league. The property worth asserting is the ordering, not
    a tolerance that would quietly encode one particular k.
    """
    short = lf.batter_profile(_batter_rows(15, xwoba=0.400), "R", LEAGUE).xwoba
    full = lf.batter_profile(_batter_rows(140, xwoba=0.400), "R", LEAGUE).xwoba
    assert LEAGUE.xwoba < short < full < 0.400


def test_family_rates_shrink_toward_the_batters_own_rate_not_the_league():
    """The hierarchy that makes a matchup mean anything.

    A hitter with a big overall number and a thin record against one family
    should read as a good hitter with a thin record — not as a league-average
    one. With only 27 plate appearances per family against k=130, his own rate is
    what the estimate should be sitting on.
    """
    rows = _batter_rows(
        20, xwoba=0.460, family_xwoba={"fb": 0.460, "br": 0.460, "os": 0.460}
    )
    profile = lf.batter_profile(rows, "R", LEAGUE)
    for key in PITCH_FAMILIES:
        own_gap = abs(profile.family_xwoba[key] - profile.xwoba)
        league_gap = abs(profile.family_xwoba[key] - (LEAGUE.xwoba_for(key) or 0.32))
        assert own_gap < league_gap, key
    # And a thin family sample produces essentially no matchup claim.
    assert all(abs(v) < 0.02 for v in profile.family_xwoba_edge.values())


def test_platoon_splits_move_with_the_hand_faced():
    rows = _batter_rows(80)
    rows["xwoba_num_vs_l"] = rows["pa_vs_l"] * 0.450
    rows["xwoba_num_vs_r"] = rows["pa_vs_r"] * 0.250
    against_lhp = lf.batter_profile(rows, "L", LEAGUE)
    against_rhp = lf.batter_profile(rows, "R", LEAGUE)
    assert against_lhp.xwoba_vs_hand > against_rhp.xwoba_vs_hand


def test_an_unknown_hand_leaves_the_platoon_value_unset():
    assert lf.batter_profile(_batter_rows(40), None, LEAGUE).xwoba_vs_hand is None


# --------------------------------------------------------------------------
# Arsenal
# --------------------------------------------------------------------------


def _arsenal(fb: int, br: int, os_: int) -> pd.DataFrame:
    return pd.DataFrame(
        [{"fb_pitches": fb, "br_pitches": br, "os_pitches": os_}]
    )


def test_usage_shares_sum_to_one():
    usage = lf.arsenal_usage(_arsenal(550, 300, 150))
    assert usage is not None
    assert sum(usage.values()) == pytest.approx(1.0)
    assert usage["fb"] == pytest.approx(0.55)


def test_a_pitcher_with_no_record_has_no_arsenal():
    assert lf.arsenal_usage(pd.DataFrame()) is None
    assert lf.arsenal_usage(_arsenal(0, 0, 0)) is None


def _profiles(**family_xwoba: float) -> dict[int, lf.BatterProfile]:
    rows = _batter_rows(80, xwoba=0.320, family_xwoba={**{k: 0.320 for k in PITCH_FAMILIES}, **family_xwoba})
    profile = lf.batter_profile(rows, "R", LEAGUE)
    return {p: profile for p in NINE}


def test_the_arsenal_edge_is_zero_for_a_lineup_with_no_family_preference():
    """A hitter equally good against everything has no matchup, by construction."""
    lineup = lf.project_lineup(_order_rows(NINE, 10), AS_OF)
    values = lf.lineup_values(lineup, _profiles(), lf.arsenal_usage(_arsenal(55, 30, 15)), LEAGUE)
    assert values["arsenal_xwoba_edge"].value == pytest.approx(0.0, abs=1e-9)


def test_a_lineup_that_cannot_hit_sliders_reads_worse_against_a_slider_pitcher():
    lineup = lf.project_lineup(_order_rows(NINE, 10), AS_OF)
    profiles = _profiles(br=0.180)
    slider_heavy = lf.lineup_values(
        lineup, profiles, lf.arsenal_usage(_arsenal(30, 60, 10)), LEAGUE
    )
    fastball_heavy = lf.lineup_values(
        lineup, profiles, lf.arsenal_usage(_arsenal(80, 10, 10)), LEAGUE
    )
    assert (
        slider_heavy["arsenal_xwoba_edge"].value
        < fastball_heavy["arsenal_xwoba_edge"].value
    )
    # And the edge is negative against the mix it handles badly.
    assert slider_heavy["arsenal_xwoba_edge"].value < 0


def test_the_edge_is_independent_of_how_good_the_lineup_is():
    """The property that keeps it from being a second lineup-quality feature.

    Two lineups, one far better than the other, with the same *relative* weakness
    against breaking balls, must produce the same matchup edge.
    """
    lineup = lf.project_lineup(_order_rows(NINE, 10), AS_OF)
    usage = lf.arsenal_usage(_arsenal(30, 60, 10))

    def edge(base: float) -> float:
        rows = _batter_rows(
            200,
            xwoba=base,
            family_xwoba={"fb": base + 0.02, "br": base - 0.06, "os": base + 0.02},
        )
        profile = lf.batter_profile(rows, "R", LEAGUE)
        return lf.lineup_values(
            lineup, {p: profile for p in NINE}, usage, LEAGUE
        )["arsenal_xwoba_edge"].value

    weak, strong = edge(0.280), edge(0.380)
    assert weak == pytest.approx(strong, abs=0.006)


def test_no_arsenal_means_no_matchup_rather_than_a_zero():
    lineup = lf.project_lineup(_order_rows(NINE, 10), AS_OF)
    values = lf.lineup_values(lineup, _profiles(), None, LEAGUE)
    assert values["arsenal_xwoba_edge"].value is None
    assert "arsenal" in (values["arsenal_xwoba_edge"].detail or "")
    # Lineup quality is still known — one missing input does not void the group.
    assert values["lineup_xwoba_weighted"].value is not None


def test_an_unprojectable_lineup_reports_missing_not_zero():
    values = lf.lineup_values(lf.EMPTY_LINEUP, {}, None, LEAGUE)
    assert set(values) == set(lf.FEATURE_KEYS)
    assert all(v.value is None for v in values.values())


def test_slot_weighting_actually_weights():
    """Swapping the best hitter from ninth to leadoff must move the number."""
    lineup = lf.project_lineup(_order_rows(NINE, 10), AS_OF)
    good = lf.batter_profile(_batter_rows(200, xwoba=0.420), "R", LEAGUE)
    poor = lf.batter_profile(_batter_rows(200, xwoba=0.260), "R", LEAGUE)

    leading = {p: (good if i == 0 else poor) for i, p in enumerate(NINE)}
    trailing = {p: (good if i == 8 else poor) for i, p in enumerate(NINE)}
    top = lf.lineup_values(lineup, leading, None, LEAGUE)["lineup_xwoba_weighted"].value
    bottom = lf.lineup_values(lineup, trailing, None, LEAGUE)["lineup_xwoba_weighted"].value
    assert top > bottom


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


def test_every_emitted_key_is_registered():
    """A feature that is not registered cannot enter a model.

    Most keys are differenced into `<key>_diff`; continuity is absolute and is
    registered per side instead, because a projection's reliability is a property
    of each team rather than a contest between them.
    """
    for key in lf.FEATURE_KEYS:
        registered = (
            key in REGISTRY
            or f"{key}_diff" in REGISTRY
            or (f"{key}_home" in REGISTRY and f"{key}_away" in REGISTRY)
        )
        assert registered, key


def test_fs_v3_extends_fs_v1_and_not_the_rejected_fs_v2():
    """Stacking on a rejected group would measure the pair, not the group."""
    v1, v2, v3 = (feature_keys(v) for v in ("fs_v1", "fs_v2", "fs_v3"))
    assert set(v3) - set(v1) == {s.key for s in LINEUP}
    assert not (set(v3) & (set(v2) - set(v1)))


def test_the_lineup_and_arsenal_groups_are_ablatable_separately():
    from app.backtest.ablation import group_members

    names = feature_keys("fs_v3")
    lineup = group_members("projected_lineup", names)
    arsenal = group_members("arsenal_matchup", names)
    offense = group_members("offense", names)
    assert lineup and arsenal
    assert not set(lineup) & set(arsenal)
    # The pre-existing team-offense group must not absorb either.
    assert not set(offense) & (set(lineup) | set(arsenal))
