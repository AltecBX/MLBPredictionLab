"""Feature registry.

A feature that is not registered here cannot enter a model
(FEATURE_DICTIONARY.md §10). The registry drives model input selection, the
explanation narratives and the UI's sample-size annotations.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FeatureCategory(StrEnum):
    STARTING_PITCHING = "starting_pitching"
    OFFENSE = "offense"
    BULLPEN = "bullpen"
    DEFENSE = "defense"
    SCHEDULE = "schedule"
    ENVIRONMENT = "environment"
    TEAM_STRENGTH = "team_strength"
    HISTORY = "history"
    STREAKS = "streaks"


CATEGORY_LABELS: dict[str, str] = {
    FeatureCategory.STARTING_PITCHING: "Starting pitching",
    FeatureCategory.OFFENSE: "Offense",
    FeatureCategory.BULLPEN: "Bullpen",
    FeatureCategory.DEFENSE: "Defense",
    FeatureCategory.SCHEDULE: "Rest and travel",
    FeatureCategory.ENVIRONMENT: "Ballpark and environment",
    FeatureCategory.TEAM_STRENGTH: "Team strength",
    FeatureCategory.HISTORY: "Matchup history",
    FeatureCategory.STREAKS: "Streaks",
}


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    key: str
    display_name: str
    category: FeatureCategory
    description: str
    unit: str = ""
    window: str | None = None
    min_sample: int = 0
    phase: int = 1
    available: bool = True
    # True when a larger value favors the home side.
    higher_favors_home: bool = True
    source_category: str = "results"
    # Phrased so it can be attached to whichever side the feature favors:
    # "<Team> <narrative>." A feature with no phrase falls back to a factual
    # statement built from its display name and value.
    narrative: str = ""
    # True when the feature is not a home-minus-away difference.
    is_absolute: bool = False
    # What a walk-forward measurement found, for a group that was built and then
    # rejected. BACKTEST_PLAN.md § Reporting requires the evidence to travel
    # with the registration so the next person reads it rather than re-running
    # the same experiment.
    measurement: str = ""


def _spec(*args: object, **kwargs: object) -> FeatureSpec:
    return FeatureSpec(*args, **kwargs)  # type: ignore[arg-type]


# --- Phase 1 active feature set (fs_v1) ------------------------------------
FS_V1: list[FeatureSpec] = [
    # Team strength
    FeatureSpec(
        "elo_diff", "Elo rating edge", FeatureCategory.TEAM_STRENGTH,
        "Difference in Elo team strength, using each team's pre-game rating only.",
        unit="pts", window="rolling", min_sample=20,
        narrative="carries the stronger overall team rating",
    ),
    FeatureSpec(
        "team_win_pct_season_diff", "Season win percentage edge",
        FeatureCategory.TEAM_STRENGTH,
        "Season-to-date win percentage, shrunk toward .500 on small samples.",
        unit="pct", window="season", min_sample=10,
        narrative="has the better record once small samples are regressed",
    ),
    FeatureSpec(
        "team_run_diff_per_game_diff", "Run differential edge",
        FeatureCategory.TEAM_STRENGTH,
        "Runs scored minus runs allowed per game, season to date.",
        unit="runs/g", window="season", min_sample=10,
        narrative="has outscored opponents by more per game",
    ),
    FeatureSpec(
        "team_pythag_win_pct_diff", "Expected win percentage edge",
        FeatureCategory.TEAM_STRENGTH,
        "Pythagorean win expectation from runs scored and allowed.",
        unit="pct", window="season", min_sample=10,
        narrative="has the better run-based expected record",
    ),
    FeatureSpec(
        "team_home_away_split_diff", "Home/road split edge", FeatureCategory.TEAM_STRENGTH,
        "Home team's home win rate versus away team's road win rate.",
        unit="pct", window="season", min_sample=10,
        narrative="has been better in this home-or-road role",
    ),
    FeatureSpec(
        "team_sos_diff", "Strength of schedule edge", FeatureCategory.TEAM_STRENGTH,
        "Average Elo of opponents faced to date; a harder schedule discounts a record.",
        unit="pts", window="season", min_sample=10,
        narrative="has faced the tougher schedule to date",
    ),
    FeatureSpec(
        "team_opp_adj_offense_diff", "Opponent-adjusted offense",
        FeatureCategory.OFFENSE,
        "Runs scored per game adjusted for the quality of pitching staffs faced.",
        unit="runs/g", window="season", min_sample=10,
        narrative="scores more once opposing pitching is accounted for",
    ),
    FeatureSpec(
        "team_opp_adj_pitching_diff", "Opponent-adjusted run prevention",
        FeatureCategory.DEFENSE,
        "Runs allowed per game adjusted for the quality of offenses faced. "
        "Sign is inverted so a positive value favors the home team.",
        unit="runs/g", window="season", min_sample=10,
        narrative="has prevented more runs once opposing offenses are accounted for",
    ),

    # Offense
    FeatureSpec(
        "off_runs_per_game_w30_diff", "Recent scoring edge (30d)", FeatureCategory.OFFENSE,
        "Runs per game over the last 30 days, shrunk toward the league rate.",
        unit="runs/g", window="w30", min_sample=10,
        narrative="has been the more productive offense over the last month",
    ),
    FeatureSpec(
        "off_runs_per_game_season_diff", "Season scoring edge", FeatureCategory.OFFENSE,
        "Runs per game, season to date, shrunk toward the league rate.",
        unit="runs/g", window="season", min_sample=10,
        narrative="has scored more per game this season",
    ),
    FeatureSpec(
        "off_form_delta_w14_diff", "Recent form swing (14d)", FeatureCategory.OFFENSE,
        "Last 14 days of scoring relative to each team's own stabilized baseline. "
        "Bounded so a hot streak moves but cannot replace season-long ability.",
        unit="runs/g", window="w14", min_sample=5,
        narrative="is swinging the bats better than its own baseline",
    ),
    FeatureSpec(
        "off_woba_proxy_season_diff", "Offensive quality (wOBA proxy)",
        FeatureCategory.OFFENSE,
        "Linear-weights wOBA computed from box-score events.",
        unit="wOBA", window="season", min_sample=10,
        narrative="has the better overall offensive quality",
    ),
    FeatureSpec(
        "off_k_pct_season_diff", "Strikeout rate edge", FeatureCategory.OFFENSE,
        "Batter strikeout rate. Sign is inverted so a positive value favors the home team.",
        unit="%", window="season", min_sample=10,
        narrative="strikes out less often at the plate",
    ),
    FeatureSpec(
        "off_vs_hand_diff", "Performance vs. opposing hand", FeatureCategory.OFFENSE,
        "Runs per game against starters of the hand each team faces tonight.",
        unit="runs/g", window="season", min_sample=8,
        narrative="hits this pitcher's handedness better",
    ),

    # Starting pitching
    FeatureSpec(
        "sp_identified_home", "Home starter identified", FeatureCategory.STARTING_PITCHING,
        "1 when the home starter is known at prediction time.",
        unit="flag", min_sample=0, is_absolute=True,
        narrative="the home starter is announced, so the model is not falling back "
                  "to a replacement-level prior",
    ),
    FeatureSpec(
        "sp_identified_away", "Away starter identified", FeatureCategory.STARTING_PITCHING,
        "1 when the away starter is known at prediction time.",
        unit="flag", min_sample=0, higher_favors_home=False, is_absolute=True,
        narrative="the away starter is announced, so the model is not falling back "
                  "to a replacement-level prior",
    ),
    FeatureSpec(
        "sp_era_season_diff", "Starter ERA edge", FeatureCategory.STARTING_PITCHING,
        "Season ERA of each expected starter, shrunk toward the league rate. "
        "Sign is inverted so a positive value favors the home team.",
        unit="ERA", window="season", min_sample=5,
        narrative="sends the starter with the better run prevention",
    ),
    FeatureSpec(
        "sp_fip_season_diff", "Starter FIP edge", FeatureCategory.STARTING_PITCHING,
        "Fielding-independent pitching, which stabilizes faster than ERA. Inverted sign.",
        unit="FIP", window="season", min_sample=5,
        narrative="has the better fielding-independent starter",
    ),
    FeatureSpec(
        "sp_whip_season_diff", "Starter WHIP edge", FeatureCategory.STARTING_PITCHING,
        "Walks plus hits per inning. Inverted sign.",
        unit="WHIP", window="season", min_sample=5,
        narrative="has the starter who allows fewer baserunners",
    ),
    FeatureSpec(
        "sp_k_pct_season_diff", "Starter strikeout rate edge",
        FeatureCategory.STARTING_PITCHING,
        "Strikeouts per batter faced.", unit="%", window="season", min_sample=5,
        narrative="has the starter who misses more bats",
    ),
    FeatureSpec(
        "sp_bb_pct_season_diff", "Starter walk rate edge", FeatureCategory.STARTING_PITCHING,
        "Walks per batter faced. Inverted sign.", unit="%", window="season", min_sample=5,
        narrative="has the starter with better control",
    ),
    FeatureSpec(
        "sp_k_minus_bb_pct_diff", "Starter K−BB% edge", FeatureCategory.STARTING_PITCHING,
        "Strikeout rate minus walk rate — the most predictive single starter rate.",
        unit="%", window="season", min_sample=5,
        narrative="has the starter with better command-and-miss profile",
    ),
    FeatureSpec(
        "sp_hr_per_9_diff", "Starter home run rate edge", FeatureCategory.STARTING_PITCHING,
        "Home runs allowed per nine innings. Inverted sign.",
        unit="HR/9", window="season", min_sample=5,
        narrative="has the starter who gives up fewer home runs",
    ),
    FeatureSpec(
        "sp_ip_per_start_diff", "Starter length edge", FeatureCategory.STARTING_PITCHING,
        "Innings per start — a longer starter shields a tired bullpen.",
        unit="IP", window="season", min_sample=5,
        narrative="expects more innings from its starter",
    ),
    FeatureSpec(
        "sp_days_rest_diff", "Starter rest edge", FeatureCategory.STARTING_PITCHING,
        "Days since each starter's previous start, capped at 10 days because beyond "
        "that the gap is an injured-list return or a debut rather than a rotation "
        "decision. Flagged estimated when the pitcher has one start on record.",
        unit="days", min_sample=1,
        narrative="sends the better-rested starter",
    ),
    FeatureSpec(
        "sp_short_rest_diff", "Short-rest penalty", FeatureCategory.STARTING_PITCHING,
        "Flag for a starter working on fewer than four days of rest. Inverted sign.",
        unit="flag", min_sample=1,
        narrative="is not asking its starter to work on short rest",
    ),
    FeatureSpec(
        "sp_experience_diff", "Starter experience edge", FeatureCategory.STARTING_PITCHING,
        "Starts on record before this game, counted from the ingested history rather "
        "than a full career total. Rookies and recent call-ups carry more downside "
        "variance, which is what this captures.",
        unit="starts", window="ingested history", min_sample=0,
        narrative="sends the more experienced starter",
    ),

    # Bullpen
    FeatureSpec(
        "bp_era_w30_diff", "Bullpen ERA edge (30d)", FeatureCategory.BULLPEN,
        "Relief ERA over the last 30 days, shrunk toward the league rate. Inverted sign.",
        unit="ERA", window="w30", min_sample=20,
        narrative="has gotten better relief work over the last month",
    ),
    FeatureSpec(
        "bp_k_minus_bb_pct_season_diff", "Bullpen K−BB% edge", FeatureCategory.BULLPEN,
        "Relief strikeout rate minus walk rate, season to date.",
        unit="%", window="season", min_sample=20,
        narrative="has the bullpen with the better strikeout-to-walk profile",
    ),
    FeatureSpec(
        "bp_fatigue_index_diff", "Bullpen fatigue edge", FeatureCategory.BULLPEN,
        "Blend of three-day and seven-day relief workload relative to the team's own "
        "baseline. Inverted sign, so a positive value means the opponent's pen is more taxed.",
        unit="index", window="3/7d", min_sample=3,
        narrative="has the fresher bullpen",
    ),
    FeatureSpec(
        "bp_ip_last_3d_diff", "Bullpen innings last 3 days", FeatureCategory.BULLPEN,
        "Relief innings thrown in the previous three days. Inverted sign.",
        unit="IP", window="3d", min_sample=1,
        narrative="has asked less of its bullpen over the last three days",
    ),

    # Defense
    FeatureSpec(
        "def_errors_per_game_diff", "Fielding errors edge", FeatureCategory.DEFENSE,
        "Errors per game. Inverted sign.", unit="E/g", window="season", min_sample=10,
        narrative="has been the cleaner defensive team",
    ),
    FeatureSpec(
        "def_efficiency_proxy_diff", "Defensive efficiency edge", FeatureCategory.DEFENSE,
        "Share of balls in play converted into outs.",
        unit="pct", window="season", min_sample=10,
        narrative="converts more balls in play into outs",
    ),

    # Schedule
    FeatureSpec(
        "sched_days_rest_diff", "Team rest edge", FeatureCategory.SCHEDULE,
        "Days since each team's previous game.", unit="days", min_sample=1,
        narrative="comes in with more rest",
    ),
    FeatureSpec(
        "sched_travel_km_diff", "Travel edge", FeatureCategory.SCHEDULE,
        "Great-circle distance travelled since the previous game. Inverted sign.",
        unit="km", min_sample=1,
        narrative="travelled less to get here",
    ),
    FeatureSpec(
        "sched_timezone_shift_diff", "Time-zone shift edge", FeatureCategory.SCHEDULE,
        "Hours of time-zone change since the previous game. Inverted sign.",
        unit="hours", min_sample=1,
        narrative="has crossed fewer time zones to get here",
    ),
    FeatureSpec(
        "sched_games_last_7d_diff", "Recent workload edge", FeatureCategory.SCHEDULE,
        "Games played in the previous seven days. Inverted sign.",
        unit="games", min_sample=1,
        narrative="has played fewer games this past week",
    ),
    FeatureSpec(
        "sched_day_after_night_diff", "Day-after-night edge", FeatureCategory.SCHEDULE,
        "Flag for a day game following a night game. Inverted sign.",
        unit="flag", min_sample=1,
        narrative="is not playing a day game after a night game",
    ),

    # Environment
    FeatureSpec(
        "env_home_field", "Home field", FeatureCategory.ENVIRONMENT,
        "Constant home indicator. Because every row is a home-team row, this column "
        "has zero variance after standardization and therefore contributes exactly "
        "zero to any individual prediction — the learned home-field effect lives in "
        "the model intercept. It is retained as an explicit marker of the sign "
        "convention and as the slot a future home/neutral-site distinction would use.",
        unit="flag", min_sample=0, is_absolute=True,
        narrative="is at home, where the league has historically won more often",
    ),
    FeatureSpec(
        "env_is_dome", "Enclosed ballpark", FeatureCategory.ENVIRONMENT,
        "Fixed dome or closed retractable roof, which removes weather variance. "
        "This is an environment indicator, not a claim about either club.",
        unit="flag", min_sample=0, is_absolute=True,
        narrative="plays this game in an enclosed ballpark, where the model has "
                  "measured a small edge for the home side",
    ),
    FeatureSpec(
        "env_venue_elevation_km", "Ballpark elevation", FeatureCategory.ENVIRONMENT,
        "Ballpark elevation in kilometres; thinner air carries batted balls further.",
        unit="km", min_sample=0, is_absolute=True,
        narrative="plays at an elevation where the model has measured an edge for "
                  "the home side",
    ),

    # History
    FeatureSpec(
        "h2h_season_series_shrunk_diff", "Season series", FeatureCategory.HISTORY,
        "This season's head-to-head record, shrunk hard (k=40) so a short series "
        "cannot outweigh hundreds of games of team quality.",
        unit="pct", window="season", min_sample=3,
        narrative="has had the better of this season's series",
    ),
]


# --- Later-phase features: registered, unavailable in Phase 1 ---------------
DEFERRED: list[FeatureSpec] = [
    FeatureSpec("sp_tto_penalty_diff", "Times-through-order penalty",
                FeatureCategory.STARTING_PITCHING,
                "wOBA increase from first to third time through the order.",
                phase=2, available=False, source_category="statcast"),
    FeatureSpec("lineup_wrc_plus_weighted_diff", "Projected lineup strength",
                FeatureCategory.OFFENSE,
                "Plate-appearance-weighted wRC+ of the projected lineup.",
                phase=2, available=False, source_category="lineups"),
    FeatureSpec("lineup_platoon_advantage_diff", "Platoon advantage",
                FeatureCategory.OFFENSE,
                "Share of the projected lineup holding the platoon edge tonight.",
                phase=2, available=False, source_category="lineups"),
    FeatureSpec("bp_closer_available_diff", "Closer availability",
                FeatureCategory.BULLPEN,
                "Whether each team's closer is available tonight.",
                phase=2, available=False, source_category="bullpen_availability"),
    FeatureSpec("bp_expected_quality_diff", "Available bullpen quality",
                FeatureCategory.BULLPEN,
                "Availability-weighted quality of the relievers who can pitch.",
                phase=2, available=False, source_category="bullpen_availability"),
    FeatureSpec("def_oaa_diff", "Outs above average", FeatureCategory.DEFENSE,
                "Statcast range-based defensive value.",
                phase=2, available=False, source_category="statcast"),
    FeatureSpec("catcher_framing_runs_diff", "Catcher framing",
                FeatureCategory.DEFENSE,
                "Expected strike-zone value added by the starting catcher.",
                phase=2, available=False, source_category="statcast"),
    FeatureSpec("env_temperature_f", "Temperature", FeatureCategory.ENVIRONMENT,
                "Forecast temperature at first pitch.",
                phase=2, available=False, source_category="weather"),
    FeatureSpec("env_wind_field_relative", "Wind direction",
                FeatureCategory.ENVIRONMENT,
                "Wind relative to the field, using the ballpark's azimuth.",
                phase=2, available=False, source_category="weather"),
    FeatureSpec("env_park_run_factor", "Park run factor", FeatureCategory.ENVIRONMENT,
                "Multi-year regressed park run factor.",
                phase=2, available=False, source_category="park_factors"),
    FeatureSpec("env_umpire_k_pct", "Umpire strike-zone profile",
                FeatureCategory.ENVIRONMENT,
                "Plate umpire's historical called-strike tendency.",
                phase=2, available=False, source_category="statcast"),
    FeatureSpec("market_novig_home_prob", "Market probability",
                FeatureCategory.TEAM_STRENGTH,
                "De-vigged market implied probability from a licensed odds provider.",
                phase=3, available=False, source_category="odds"),
    FeatureSpec("bvp_history_diff", "Batter vs. pitcher history",
                FeatureCategory.HISTORY,
                "Career batter-vs-pitcher results, gated at 25 plate appearances and "
                "capped in total contribution.",
                phase=3, available=False, source_category="statcast"),
]


# --- Phase 2A: starting-pitcher Statcast (fs_v2) — BUILT, MEASURED, REJECTED
#
# Each is a home-minus-away difference oriented so that a POSITIVE value favors
# the home side; for the four "allowed" measures that means away-minus-home,
# since allowing weaker contact is the good outcome.
#
# All nine are `available=False`. They were built, measured over a full
# walk-forward season, and did not earn a place. Two independent views agreed:
#
#   * Head to head, fs_v1 against fs_v2 on the same games, twice — 2024 gave
#     Δ log loss −0.0004 [−0.0032, +0.0026] over 1,741 games, 2025 gave +0.0004
#     [−0.0003, +0.0010] over 2,363. All six intervals across the two seasons
#     span zero and the sign flips between them, which is what a null effect
#     looks like. Accuracy rose both times, which is exactly the trade this
#     system does not make.
#   * Leave-one-out inside fs_v2 — removing the group *improved* log loss by
#     0.0041, the largest such improvement of any group. Group-alone, it beat a
#     coin flip by 0.00001.
#
# The diagnosis is in the univariate numbers, not in the fit. Every one of the
# nine correlates with the outcome more weakly than the box-score starting
# pitcher features already in the model (best +0.069 against +0.082 for
# sp_k_minus_bb_pct_diff), and the strongest of them are 0.52–0.74 correlated
# with those same features. They are a noisier re-measurement of what the model
# already knows. The genuinely new part — contact quality allowed and the
# velocity trend — correlates most weakly of all (+0.017, +0.015, +0.005).
#
# This is not an over-shrinkage artifact: the spreads are healthy, 5 percentage
# points of hard-hit rate and 1.7 mph of velocity between the two starters.
#
# They stay registered, with the measurement attached, because the code that
# computes them is correct and the next attempt should start from a different
# hypothesis rather than from this one again. MODELING_PLAN.md has the full
# result beside the GBDT one.
REJECTION = (
    "Walk-forward over two seasons. 2024, 1,741 games: Δ log loss −0.0004, "
    "paired 95% CI [−0.0032, +0.0026]. 2025, 2,363 games: +0.0004 "
    "[−0.0003, +0.0010]. Six intervals across log loss, Brier and calibration, "
    "all six spanning zero, and the sign flips between seasons. Leave-one-out "
    "removal improved log loss by 0.0041; group-alone beat a coin flip by "
    "0.00001. Rejected. See MODELING_PLAN.md § Starting-pitcher Statcast."
)

SC_SP: list[FeatureSpec] = [
    FeatureSpec(
        "sc_sp_xwoba_allowed_diff", "Starter xwOBA allowed",
        FeatureCategory.STARTING_PITCHING,
        "Expected wOBA against, from Statcast contact quality on balls in play "
        "and the actual value of every other plate appearance. Regressed toward "
        "the pitcher's prior season, itself regressed toward the league.",
        unit="xwOBA", window="season", min_sample=100, phase=2, available=False,
        higher_favors_home=False, source_category="statcast",
        measurement=REJECTION,
        narrative="sends out the starter allowing weaker expected contact",
    ),
    FeatureSpec(
        "sc_sp_barrel_pct_allowed_diff", "Starter barrel rate allowed",
        FeatureCategory.STARTING_PITCHING,
        "Barrels per batted ball allowed, using Savant's own classification.",
        unit="pct", window="season", min_sample=40, phase=2, available=False,
        higher_favors_home=False, source_category="statcast",
        measurement=REJECTION,
        narrative="has given up barrels less often",
    ),
    FeatureSpec(
        "sc_sp_hard_hit_pct_allowed_diff", "Starter hard-hit rate allowed",
        FeatureCategory.STARTING_PITCHING,
        "Share of batted balls allowed at 95 mph or more.",
        unit="pct", window="season", min_sample=40, phase=2, available=False,
        higher_favors_home=False, source_category="statcast",
        measurement=REJECTION,
        narrative="has allowed hard contact less often",
    ),
    FeatureSpec(
        "sc_sp_avg_exit_velocity_allowed_diff", "Starter exit velocity allowed",
        FeatureCategory.STARTING_PITCHING,
        "Mean exit velocity of batted balls allowed.",
        unit="mph", window="season", min_sample=40, phase=2, available=False,
        higher_favors_home=False, source_category="statcast",
        measurement=REJECTION,
        narrative="has been hit less hard on average",
    ),
    FeatureSpec(
        "sc_sp_whiff_pct_diff", "Starter whiff rate",
        FeatureCategory.STARTING_PITCHING,
        "Swings missed per swing. Measures stuff without the strikeout's "
        "dependence on the count getting there.",
        unit="pct", window="season", min_sample=150, phase=2, available=False,
        source_category="statcast", measurement=REJECTION,
        narrative="sends out the starter who misses more bats",
    ),
    FeatureSpec(
        "sc_sp_chase_pct_diff", "Starter chase rate",
        FeatureCategory.STARTING_PITCHING,
        "Swings induced on pitches outside the zone, per pitch outside the zone.",
        unit="pct", window="season", min_sample=150, phase=2, available=False,
        source_category="statcast", measurement=REJECTION,
        narrative="gets hitters to chase more often",
    ),
    FeatureSpec(
        "sc_sp_csw_pct_diff", "Starter called-strike-plus-whiff rate",
        FeatureCategory.STARTING_PITCHING,
        "Called strikes plus whiffs per pitch — command and stuff in one rate, "
        "and the fastest-stabilizing of the three.",
        unit="pct", window="season", min_sample=300, phase=2, available=False,
        source_category="statcast", measurement=REJECTION,
        narrative="has the better combination of command and swing-and-miss",
    ),
    FeatureSpec(
        "sc_sp_fastball_velocity_diff", "Starter fastball velocity",
        FeatureCategory.STARTING_PITCHING,
        "Mean four-seam velocity. Four-seam only, so a change in pitch usage "
        "cannot be mistaken for a change in stuff.",
        unit="mph", window="season", min_sample=100, phase=2, available=False,
        source_category="statcast", measurement=REJECTION,
        narrative="throws harder",
    ),
    FeatureSpec(
        "sc_sp_velocity_delta_30d_diff", "Starter velocity trend",
        FeatureCategory.STARTING_PITCHING,
        "Last 30 days of fastball velocity minus the pitcher's own season "
        "average. A delta rather than a level, so recent form moves the "
        "prediction without erasing the season behind it.",
        unit="mph", window="w30", min_sample=60, phase=2, available=False,
        source_category="statcast", measurement=REJECTION,
        narrative="is throwing harder lately than his own season baseline",
    ),
]


# --- Phase 2B: projected lineup and arsenal matchup (fs_v3) ----------------
#
# The two hypotheses MODELING_PLAN.md left open when the starting-pitcher
# Statcast group was rejected: that the matchup matters where the pitcher alone
# did not, and that nine lineup slots are a larger surface than one arm.
#
# Every input is a completed game cut at as_of. The lineup is *projected* from a
# team's own recent starts, never read from a posted one — LEAKAGE_PREVENTION.md
# §15 measured that no posted lineup is knowable at this snapshot, and this group
# does not pretend otherwise.
#
# BUILT, MEASURED, REJECTED — twice, and the second time is the interesting one.
#
# Measured together as fs_v3 the seven came in marginally worse than fs_v1. The
# ablation then showed why the average was the wrong thing to look at: the two
# arsenal features beat a coin flip on their own by 0.0038 — more per feature
# than any other group in this model — while the five projected-lineup features
# came in 0.0061 WORSE than a coin flip.
#
# So the arsenal pair was re-measured alone as fs_v4. That flipped the delta from
# -0.000444 to +0.000084, an improvement of 0.000528 from dropping the lineup
# half. The ablation had independently estimated that half was worth -0.00053.
# Two different methods, agreeing to two parts in a million — which is the best
# evidence available that the comparison machinery measures what it claims.
#
# It still does not clear the bar. +0.000084 with an interval of
# [-0.00028, +0.00046] is a group that cannot be distinguished from nothing.
LINEUP_REJECTION = (
    "Walk-forward over 2025, 2,363 games, trained from 2024. fs_v3 (all seven): "
    "delta log loss -0.000444, paired 95% CI [-0.00102, +0.00014]. fs_v4 (the "
    "two arsenal features alone): +0.000084 [-0.00028, +0.00046]. Both spanning "
    "zero. Group-alone: arsenal +0.0038 vs a coin flip, projected lineup -0.0061. "
    "Rejected. See MODELING_PLAN.md, Projected lineups and the arsenal matchup."
)

LINEUP: list[FeatureSpec] = [
    FeatureSpec(
        "lineup_xwoba_weighted_diff", "Projected lineup quality",
        FeatureCategory.OFFENSE,
        "Expected wOBA of the nine most likely starters, weighted by the plate "
        "appearances their batting-order slots actually receive, each shrunk "
        "toward the league.",
        unit="xwOBA", window="season", min_sample=80, phase=2,
        available=False, source_category="statcast",
        measurement=LINEUP_REJECTION, narrative="projects the stronger lineup by expected plate appearances",
    ),
    FeatureSpec(
        "lineup_xwoba_vs_hand_diff", "Projected lineup vs. this hand",
        FeatureCategory.OFFENSE,
        "The same weighted lineup quality, but each hitter measured against the "
        "handedness of the pitcher he will face tonight.",
        unit="xwOBA", window="season", min_sample=80, phase=2,
        available=False, source_category="statcast",
        measurement=LINEUP_REJECTION, narrative="holds the platoon edge against tonight's starter",
    ),
    FeatureSpec(
        "lineup_whiff_pct_weighted_diff", "Projected lineup swing-and-miss",
        FeatureCategory.OFFENSE,
        "Weighted rate at which the projected lineup misses when it swings.",
        unit="pct", window="season", min_sample=80, phase=2,
        available=False, higher_favors_home=False,
        source_category="statcast", measurement=LINEUP_REJECTION,
        narrative="puts more bats on the ball",
    ),
    FeatureSpec(
        "arsenal_xwoba_edge_diff", "Arsenal matchup, contact",
        FeatureCategory.OFFENSE,
        "How this lineup fares against the pitch mix tonight's starter actually "
        "throws, NET of how it fares generally. A raw figure would restate "
        "lineup quality, which is already the feature beside it; the edge is "
        "what the mix is worth given the lineup.",
        unit="xwOBA", window="season", min_sample=80, phase=2,
        available=False, source_category="statcast",
        measurement=LINEUP_REJECTION, narrative="matches up well against what this starter throws",
    ),
    FeatureSpec(
        "arsenal_whiff_edge_diff", "Arsenal matchup, swing-and-miss",
        FeatureCategory.OFFENSE,
        "The same edge on whiff rate: how much more, or less, this lineup misses "
        "against his particular mix than against pitching in general.",
        unit="pct", window="season", min_sample=80, phase=2,
        available=False, higher_favors_home=False,
        source_category="statcast", measurement=LINEUP_REJECTION,
        narrative="misses less often against this starter's mix",
    ),
    FeatureSpec(
        "lineup_continuity_home", "Home lineup stability", FeatureCategory.OFFENSE,
        "Share of the projected nine who started the team's most recent game. A "
        "projection is a guess; this is how good a guess it is.",
        unit="pct", window="w21", min_sample=5, phase=2, is_absolute=True,
        available=False, source_category="results",
        measurement=LINEUP_REJECTION,
    ),
    FeatureSpec(
        "lineup_continuity_away", "Away lineup stability", FeatureCategory.OFFENSE,
        "Same, for the away side.",
        unit="pct", window="w21", min_sample=5, phase=2, is_absolute=True,
        available=False, source_category="results",
        measurement=LINEUP_REJECTION,
    ),
]


#: The two matchup features on their own, for the isolating comparison below.
ARSENAL_ONLY: list[FeatureSpec] = [
    s for s in LINEUP if s.key.startswith("arsenal_")
]


# --- Phase 2A step 5: individual bullpen availability (fs_v5) ---------------
#
# The third hypothesis the starting-pitcher rejection left open, and the one
# that has waited longest. It is a different SHAPE from the three groups already
# rejected: not another season aggregate over the same population, but a
# per-pitcher constraint the aggregate provably cannot express.
#
# The bullpen features already in fs_v1 are team totals — relief innings over
# three days, a fatigue index, a thirty-day relief ERA. A pen that threw four
# innings yesterday across four pitchers and one that threw four innings out of
# its two best arms produce the SAME value on every one of them, and are in
# completely different states tonight. That gap is what this group addresses.
#
# The rest thresholds are conventions written down in `features/bullpen.py`, not
# parameters fitted here. Tuning a rest threshold against the win outcome it is
# about to be scored on is how a feature group manufactures its own
# significance, and the four rejections on record are honest partly because
# nothing like that happened.
#
# BUILT, MEASURED, REJECTED — and the way it failed is worth more than the
# result. Six comparisons were run: two seasons by three regularisation
# settings. One returned ADOPT, one returned REJECT, four returned NO_EFFECT,
# and the sign of the difference flips between seasons at EVERY setting.
#
# The ADOPT is the instructive one. At C=0.01 on 2024 the group clears the bar
# outright — +0.001932, interval [+0.00035, +0.00358], zero excluded. At C=0.03
# on the same season, same features, same games, it is +0.000578 and nothing.
# Run the same C=0.01 comparison on 2025 and the difference is NEGATIVE.
#
# That is the whole hazard of this protocol in one group: a verdict reachable by
# choosing a nuisance constant nobody has a principled reason to set either way.
# The per-set C selection made it worse rather than better — on 2024 it handed
# the baseline C=0.03 and the candidate C=0.01, crediting the candidate with
# 0.00050 of the baseline being worse at its own C plus 0.00085 of itself being
# better, neither of which is a feature effect.
BULLPEN_REJECTION = (
    "Walk-forward on two seasons at three regularisation settings, coverage 100% "
    "on all three features. 2024: +0.001434 (C selected), +0.000578 (C=0.03), "
    "+0.001932 (C=0.01, interval excluding zero). 2025: -0.000013 (C selected), "
    "-0.000621 (C=0.03, interval excluding zero, REJECT), -0.000395 (C=0.01). "
    "The sign flips between seasons at every setting, and the one ADOPT does not "
    "replicate at its own C on the larger season. Rejected. See MODELING_PLAN.md, "
    "Individual bullpen availability."
)

BULLPEN_AVAILABILITY: list[FeatureSpec] = [
    FeatureSpec(
        "bp_available_count_diff", "Available relievers",
        FeatureCategory.BULLPEN,
        "Relievers in the team's thirty-day corps who are neither on three "
        "straight days nor coming off a heavy outing.",
        unit="count", window="3d", min_sample=3, phase=2,
        source_category="bullpen_availability",
        available=False, measurement=BULLPEN_REJECTION,
        narrative="has more of its bullpen available tonight",
    ),
    FeatureSpec(
        "bp_available_quality_diff", "Available bullpen quality",
        FeatureCategory.BULLPEN,
        "Mean K−BB% of the relievers who can pitch, each shrunk toward the "
        "league relief rate. Unavailable arms are excluded rather than zeroed.",
        unit="%", window="season", min_sample=3, phase=2,
        source_category="bullpen_availability",
        available=False, measurement=BULLPEN_REJECTION,
        narrative="can call on the better relievers tonight",
    ),
    FeatureSpec(
        "bp_best_reliever_available_diff", "Best reliever available",
        FeatureCategory.BULLPEN,
        "Whether the team's best-rated reliever can pitch: 1 available, 0.5 "
        "limited, 0 unavailable. Not a closer — no save or leverage data exists "
        "in this database, and a closer inferred from appearance counts would be "
        "a guess presented as a fact.",
        unit="0-1", window="3d", min_sample=3, phase=2,
        source_category="bullpen_availability",
        available=False, measurement=BULLPEN_REJECTION,
        narrative="has its best reliever available tonight",
    ),
]

# --- Phase 2 #5: forecast weather (fs_v6) ----------------------------------
#
# The park measurement decided the shape of this group before it was built. A
# condition both teams share moves the total and not the margin, so a "the ball
# carries tonight" feature is a totals input against a win target. The
# interaction is the part that can move a margin: the air is shared, the two
# staffs' exposure to it is not.
#
# Both shapes are registered so the ablation can separate them rather than
# reporting their average — which is the mistake the fs_v3 measurement caught
# and had to be re-run to undo.
# BUILT, MEASURED, REJECTED. Negative in both seasons, and on the larger one the
# interval excludes zero -- the group measurably hurts. Both arms drew the same
# regularisation in both seasons, so unlike the bullpen group there is no C
# confound to unpick: this is the features.
#
# The park measurement predicted it and the interaction did not rescue it. A
# condition both teams share moves the total, not the margin, and the fly-ball
# exposure gap between two staffs is apparently too small a lever to recover
# what the shared part cannot say.
#
# The strongest part of this result is the data it failed on. Backfilled
# forecasts come from an archive that does not expose which model run produced
# each value, so they are probably BETTER than what was available at T-3h. The
# group failed with better information than production would ever have.
WEATHER_REJECTION = (
    "Walk-forward on two seasons, coverage 99.9% on carry and 99.6% on the "
    "interaction. 2024: delta log loss -0.002244, paired 95% CI "
    "[-0.00537, +0.00088], n=1,741. 2025: -0.000151, CI [-0.00027, -0.00003], "
    "zero excluded, REJECT, n=2,363. Negative in both, same regularisation in "
    "both arms, and measured on optimistically-biased archived forecasts. "
    "Rejected. See MODELING_PLAN.md, Forecast weather."
)

WEATHER: list[FeatureSpec] = [
    FeatureSpec(
        "wx_carry_index", "Ball carry conditions",
        FeatureCategory.ENVIRONMENT,
        "How far the ball carries tonight against a standard evening, from "
        "forecast air density and the wind's out-to-centre component. Shared "
        "by both teams, so it is a totals input against a win target.",
        unit="index", window="game", min_sample=1, phase=2, is_absolute=True,
        source_category="weather",
        available=False, measurement=WEATHER_REJECTION,
        narrative="is playing in air that carries the ball further",
    ),
    FeatureSpec(
        "wx_carry_x_flyball_diff", "Carry against staff fly-ball tendency",
        FeatureCategory.ENVIRONMENT,
        "Carry conditions times the gap between the two staffs' fly-ball "
        "tendency. The air is shared; exposure to it is not, which is the only "
        "way weather can move a margin rather than a total.",
        unit="index", window="season", min_sample=300, phase=2,
        source_category="weather",
        available=False, measurement=WEATHER_REJECTION,
        narrative="has the staff better suited to tonight's air",
    ),
    FeatureSpec(
        "wx_precip_prob", "Rain probability",
        FeatureCategory.ENVIRONMENT,
        "Forecast chance of precipitation at first pitch. Shared, and included "
        "because rain changes how much baseball is played before a result "
        "stands rather than who is better.",
        unit="probability", window="game", min_sample=1, phase=2, is_absolute=True,
        source_category="weather",
        available=False, measurement=WEATHER_REJECTION,
        narrative="faces a higher chance of rain",
    ),
]

# --- Phase 2 #4: roster availability (fs_v7) -------------------------------
#
# Six rejections, one diagnosis: every group so far has been a DECOMPOSITION of
# team strength. A season rate is a sufficient statistic for any rearrangement
# of the players who produced it, so rearranging them says nothing new. The
# starting-pitcher split is the cleanest demonstration — it disagreed with the
# base model about one game in five and was not the more accurate of the two.
#
# This group performs a different operation. It does not redistribute the
# season rate, it reports that the rate is STALE: it was accumulated by a roster
# that included somebody who is not playing tonight. That fact cannot be inside
# the team rate, because the team rate contains his contribution precisely on
# account of his having made it.
#
# The prediction that distinguishes it from the six: a decomposition averages
# back to the team rate over a season (each rotation slot comes up equally
# often), which is why the pitching split was worth nothing in aggregate. An
# availability loss does not average back — a player lost for the year holds the
# feature away from zero until the window rolls past him.
#
# `IL_RECENCY_DAYS` is fitted, and fitted against ABSENCE — whether the player
# subsequently appeared — not against the win outcome this group is about to be
# scored on. See `features/availability.py` for the table. The bullpen group's
# warning is about a nuisance constant tuned on the target; this is deliberately
# the other thing.
#
# BUILT, MEASURED, NOT ADOPTED — and it is the first candidate group whose sign
# does not flip. Four comparisons, two seasons by two regularisation settings,
# and log loss and Brier are POSITIVE in all four. Every previous group either
# flipped between seasons (bullpen, at every C) or was negative in both
# (weather). This one does neither.
#
# It is still not adoption, because every interval spans zero. Pooled across
# both seasons at C=0.03 the estimate is +0.000314 with an interval of
# [-0.00046, +0.00108] — an effect of about a twentieth of what the whole model
# is worth, in a sample that would need roughly 24,600 games to resolve it. Ten
# seasons. The honest verdict is not "measured and absent" but "smaller than two
# seasons of baseball can see", which is a different sentence and a more useful
# one.
AVAILABILITY_NO_EFFECT = (
    "Walk-forward on two seasons at two regularisation settings, coverage 100% "
    "on both features in both seasons. 2024: +0.000091 (C=0.03), +0.000034 "
    "(C=0.01). 2025: +0.000385 (C=0.03), +0.000310 (C=0.01). Positive in all "
    "four on log loss and Brier -- the first candidate group that does not flip "
    "sign -- but every interval spans zero. Pooled at C=0.03: +0.000314, "
    "[-0.00046, +0.00108]; excluding zero would take about 24,600 games. Not "
    "adopted. See MODELING_PLAN.md, Roster availability."
)

AVAILABILITY: list[FeatureSpec] = [
    FeatureSpec(
        "il_offense_lost_diff", "Bats on the injured list",
        FeatureCategory.OFFENSE,
        "Share of the team's last forty-five days of weighted offensive "
        "production belonging to players placed on the injured list within the "
        "last four weeks. A share, not a headcount: losing a cleanup hitter and "
        "losing a bench bat are not the same event.",
        unit="share", window="w45", min_sample=200, phase=2,
        source_category="injuries", higher_favors_home=False,
        available=False, measurement=AVAILABILITY_NO_EFFECT,
        narrative="has lost less of its recent batting to the injured list",
    ),
    FeatureSpec(
        "il_pitching_lost_diff", "Arms on the injured list",
        FeatureCategory.BULLPEN,
        "The same, for pitching: share of the team's batters faced over the "
        "last forty-five days thrown by pitchers now on the injured list.",
        unit="share", window="w45", min_sample=200, phase=2,
        source_category="injuries", higher_favors_home=False,
        available=False, measurement=AVAILABILITY_NO_EFFECT,
        narrative="has lost less of its recent pitching to the injured list",
    ),
]

# --- Streaks (fs_v8) --------------------------------------------------------
#
# Raw streak length never enters the model directly — the owner's own
# instruction, and the right one: a streak is mostly a restatement of team
# strength plus noise. What is offered to the gate is the processed story:
# capped lengths, the shrunk historical continuation probability in win
# direction, that probability minus what the pre-game expectation already said
# (the only part that is not already team strength), and the run differential
# and opponent quality across the current streak's games. All of it computed
# from strictly-earlier results via knowledge_time; see features/streaks.py.
#
# The display section (/streaks) exists regardless of this group's verdict —
# it is research and reading material. The verdict on the FEATURES is recorded
# in `measurement` below once compare-feature-sets has run.
STREAKS_MEASUREMENT = (
    "Walk-forward fs_v1 vs fs_v8 at pinned regularisation on three seasons, "
    "both C=0.03 and C=0.01. 2024: +0.001224 and +0.002021, intervals spanning "
    "zero. 2025: +0.000039 and -0.000363, spanning zero. 2026 to date (1,569 "
    "games, trained on 2023-25): -0.002511 [-0.00375, -0.00125] and -0.002179 "
    "[-0.00335, -0.00104] -- the group measurably HURTS, on log loss and Brier "
    "alike, at both regularisations. Sign decays with training-window size, "
    "which is what noise the model can no longer exploit looks like. REJECTED; "
    "the /streaks section is research and display only. See MODELING_PLAN.md, "
    "Streak features."
)

STREAKS: list[FeatureSpec] = [
    FeatureSpec(
        "sk_win_streak_capped_diff", "Winning streak", FeatureCategory.STREAKS,
        "Current winning streak entering the game, capped at five so one "
        "outlier run cannot dominate a linear term.",
        unit="games", window="season", phase=2, available=False,
        measurement=STREAKS_MEASUREMENT,
        narrative="carries the longer winning streak into tonight",
    ),
    FeatureSpec(
        "sk_loss_streak_capped_diff", "Losing streak", FeatureCategory.STREAKS,
        "Current losing streak entering the game, capped at five.",
        unit="games", window="season", phase=2, available=False,
        higher_favors_home=False, measurement=STREAKS_MEASUREMENT,
        narrative="carries the shorter losing streak into tonight",
    ),
    FeatureSpec(
        "sk_continue_prob_diff", "Streak history, win direction",
        FeatureCategory.STREAKS,
        "The team's historical next-game win rate at exactly this streak "
        "length, shrunk toward the league rate at the same length; both sides "
        "computed from strictly earlier games. Missing when no streak of two "
        "or more is active.",
        unit="prob", window="all", min_sample=10, phase=2, available=False,
        measurement=STREAKS_MEASUREMENT,
        narrative="has historically fared better after streaks like its current one",
    ),
    FeatureSpec(
        "sk_adjusted_effect_diff", "Adjusted streak effect", FeatureCategory.STREAKS,
        "The shrunk streak-history win rate minus the pre-game expectation "
        "for those same historical games — the part of the streak story that "
        "is not already team strength. Missing when no streak of two or more "
        "is active.",
        unit="pp", window="all", min_sample=10, phase=2, available=False,
        measurement=STREAKS_MEASUREMENT,
        narrative="has outrun expectations after streaks like its current one",
    ),
    FeatureSpec(
        "sk_streak_run_diff_diff", "Run differential during the streak",
        FeatureCategory.STREAKS,
        "Average run differential across the current streak's games; zero "
        "when no streak is active, which is the true value of no streak.",
        unit="runs/g", window="streak", phase=2, available=False,
        measurement=STREAKS_MEASUREMENT,
        narrative="has won or lost by more during its current run",
    ),
    FeatureSpec(
        "sk_streak_opp_elo_diff", "Opponent quality during the streak",
        FeatureCategory.STREAKS,
        "Average pre-game Elo of the opponents faced during the current "
        "streak, relative to 1500; zero when no streak is active.",
        unit="pts", window="streak", phase=2, available=False,
        measurement=STREAKS_MEASUREMENT,
        narrative="has built its current run against tougher opposition",
    ),
]

# --- Multi-season projections (fs_v9) ----------------------------------------
#
# Every rate in fs_v1 is season-to-date and shrunk toward the LEAGUE. The only
# feature with a memory across seasons is Elo, and calibrated Elo on its own
# matches the whole model — which says the other forty-one features spend the
# spring relearning what the previous season already knew. FEATURE_DICTIONARY.md
# §1 rule 2 has always required prior-season baselines as the prior; this is
# the first group to build them. See features/projections.py for the pooling
# and for why none of its constants were fitted on the win outcome.
#
# BUILT, MEASURED, ADOPTED — the first candidate group to clear the gate.
# fs_v1 against fs_v9 on the same 6,900 games (2024 through 2026 to date),
# regularisation pinned, with each of two training histories:
#
#   trained from 2023, C=0.03: delta log loss +0.001330 [+0.00052, +0.00215]
#   trained from 2023, C=0.01:                +0.001500 [+0.00070, +0.00234]
#   trained from 2021, C=0.03:                +0.000998 [+0.00022, +0.00177]
#   trained from 2021, C=0.01:                +0.001220 [+0.00040, +0.00201]
#
# Every interval excludes zero, Brier agrees in every arm, calibration error
# is unchanged, and the sign holds in every season of every arm. Every earlier
# candidate either flipped sign between seasons or hurt.
PROJECTIONS_ADOPTED = (
    "Walk-forward fs_v1 vs fs_v9 on 6,900 games, 2024-2026, regularisation "
    "pinned. Trained from 2023: +0.001330 [+0.00052, +0.00215] at C=0.03, "
    "+0.001500 [+0.00070, +0.00234] at C=0.01. Trained from 2021: +0.000998 "
    "[+0.00022, +0.00177] and +0.001220 [+0.00040, +0.00201]. Brier agrees "
    "everywhere, calibration unchanged, positive in every season of every "
    "arm. ADOPTED. See MODELING_PLAN.md, Multi-season projections."
)

PROJECTIONS: list[FeatureSpec] = [
    FeatureSpec(
        "proj_off_rpg_diff", "Projected scoring edge", FeatureCategory.OFFENSE,
        "Runs per game projected from the last two seasons and the season in "
        "progress, each season measured against its own league rate, regressed "
        "toward the league.",
        unit="runs/g", window="3 seasons", min_sample=10, phase=2,
        measurement=PROJECTIONS_ADOPTED,
        narrative="projects as the stronger offense once prior seasons are weighed",
    ),
    FeatureSpec(
        "proj_ra_rpg_diff", "Projected run-prevention edge", FeatureCategory.DEFENSE,
        "Runs allowed per game, projected the same way. Sign is inverted so a "
        "positive value favors the home team.",
        unit="runs/g", window="3 seasons", min_sample=10, phase=2,
        measurement=PROJECTIONS_ADOPTED,
        narrative="projects to allow fewer runs once prior seasons are weighed",
    ),
    FeatureSpec(
        "proj_sp_k_minus_bb_pct_diff", "Projected starter K−BB% edge",
        FeatureCategory.STARTING_PITCHING,
        "Strikeout rate minus walk rate for tonight's starter, pooled over up to "
        "three prior seasons of starts plus this one, regressed toward the league.",
        unit="%", window="4 seasons", min_sample=100, phase=2,
        measurement=PROJECTIONS_ADOPTED,
        narrative="has the starter with the better projected command-and-miss profile",
    ),
    FeatureSpec(
        "proj_sp_fip_diff", "Projected starter FIP edge",
        FeatureCategory.STARTING_PITCHING,
        "Fielding-independent pitching projected from up to three prior seasons "
        "of starts plus this one, on this season's run environment. Inverted sign.",
        unit="FIP", window="4 seasons", min_sample=100, phase=2,
        measurement=PROJECTIONS_ADOPTED,
        narrative="has the better projected fielding-independent starter",
    ),
]

REGISTRY: dict[str, FeatureSpec] = {
    s.key: s
    for s in FS_V1 + SC_SP + LINEUP + BULLPEN_AVAILABILITY + WEATHER + AVAILABILITY
    + STREAKS + PROJECTIONS + DEFERRED
}

FEATURE_SET_VERSIONS: dict[str, list[str]] = {
    "fs_v1": [s.key for s in FS_V1],
    "fs_v2": [s.key for s in FS_V1 + SC_SP],
    # Deliberately fs_v1 + LINEUP, not fs_v2 + LINEUP: fs_v2 was measured and
    # rejected, and stacking a new group on a rejected one would measure the
    # pair rather than the group.
    "fs_v3": [s.key for s in FS_V1 + LINEUP],
    # The arsenal matchup alone. The fs_v3 ablation separated the two halves of
    # that group and they behaved nothing alike: the two arsenal features beat a
    # coin flip on their own by 0.0038 — more per feature than any other group in
    # the model — while the five projected-lineup features came in 0.0061 WORSE
    # than a coin flip. Measuring them together measured their average, which is
    # not a quantity anyone wants. This isolates the half that showed something.
    "fs_v4": [s.key for s in FS_V1 + ARSENAL_ONLY],
    # fs_v1 + individual bullpen availability. On fs_v1 rather than fs_v4 for
    # the same reason fs_v3 was: fs_v4 did not clear the bar either, and
    # stacking on a group that failed measures the pair, not the group.
    #
    # Kept after rejection so the six-way measurement can be reproduced rather
    # than taken on trust — the same reason fs_v2, fs_v3 and fs_v4 are still
    # here. Reproduce with `--C 0.01` and `--C 0.03` as well as the default, or
    # the sensitivity that decided this is invisible.
    "fs_v5": [s.key for s in FS_V1 + BULLPEN_AVAILABILITY],
    # fs_v1 + forecast weather. Phase 2's acceptance criterion names the weather
    # group explicitly: it improves measurably or it is removed.
    "fs_v6": [s.key for s in FS_V1 + WEATHER],
    # fs_v1 + roster availability. On fs_v1 for the same reason as every
    # candidate since fs_v3: stacking on a rejected group measures the pair.
    "fs_v7": [s.key for s in FS_V1 + AVAILABILITY],
    # fs_v1 + the processed streak story. On fs_v1 for the same reason as every
    # candidate since fs_v3: stacking on a rejected group measures the pair.
    "fs_v8": [s.key for s in FS_V1 + STREAKS],
    # fs_v1 + multi-season projections. On fs_v1 for the same reason as every
    # candidate since fs_v3.
    "fs_v9": [s.key for s in FS_V1 + PROJECTIONS],
}


def feature_keys(feature_set_version: str = "fs_v1") -> list[str]:
    try:
        return FEATURE_SET_VERSIONS[feature_set_version]
    except KeyError as exc:
        raise KeyError(
            f"Unknown feature set {feature_set_version!r}. Known: "
            f"{sorted(FEATURE_SET_VERSIONS)}"
        ) from exc


def spec(key: str) -> FeatureSpec:
    try:
        return REGISTRY[key]
    except KeyError as exc:
        raise KeyError(
            f"Feature {key!r} is not registered. A feature that is not registered "
            f"cannot enter a model (FEATURE_DICTIONARY.md §10)."
        ) from exc


def deferred_by_source() -> dict[str, list[FeatureSpec]]:
    grouped: dict[str, list[FeatureSpec]] = {}
    for item in DEFERRED:
        grouped.setdefault(item.source_category, []).append(item)
    return grouped
