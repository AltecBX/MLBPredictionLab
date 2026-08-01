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


CATEGORY_LABELS: dict[str, str] = {
    FeatureCategory.STARTING_PITCHING: "Starting pitching",
    FeatureCategory.OFFENSE: "Offense",
    FeatureCategory.BULLPEN: "Bullpen",
    FeatureCategory.DEFENSE: "Defense",
    FeatureCategory.SCHEDULE: "Rest and travel",
    FeatureCategory.ENVIRONMENT: "Ballpark and environment",
    FeatureCategory.TEAM_STRENGTH: "Team strength",
    FeatureCategory.HISTORY: "Matchup history",
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
        "Days since each starter's previous start, capped at 30 days so a long "
        "layoff cannot dominate the vector.",
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
        "Career starts before this game; rookies carry more downside variance.",
        unit="starts", window="career", min_sample=0,
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
    FeatureSpec("sp_xwoba_allowed_diff", "Starter xwOBA allowed",
                FeatureCategory.STARTING_PITCHING,
                "Expected wOBA allowed from Statcast contact quality.",
                phase=2, available=False, source_category="statcast"),
    FeatureSpec("sp_barrel_pct_allowed_diff", "Starter barrel rate allowed",
                FeatureCategory.STARTING_PITCHING,
                "Barrels per batted ball allowed.",
                phase=2, available=False, source_category="statcast"),
    FeatureSpec("sp_velocity_delta_30d_diff", "Starter velocity trend",
                FeatureCategory.STARTING_PITCHING,
                "30-day change in fastball velocity, an early injury/fatigue signal.",
                phase=2, available=False, source_category="statcast"),
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


REGISTRY: dict[str, FeatureSpec] = {s.key: s for s in FS_V1 + DEFERRED}

FEATURE_SET_VERSIONS: dict[str, list[str]] = {
    "fs_v1": [s.key for s in FS_V1],
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
