"""Feature vector assembly.

Produces the ``fs_v1`` vector for a game at a given ``as_of``. Every value is
computed from as-of-filtered game logs, carries a sample size, and is flagged
when it was shrunk toward a prior rather than observed outright.

Sign convention: a positive feature value always favors the HOME team.
Features where a lower raw value is better (ERA, WHIP, travel, fatigue) are
assembled as ``away - home`` so the convention holds throughout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

from app.core.config import settings
from app.core.logging import get_logger
from app.features import aggregates as agg
from app.features import availability as av
from app.features import bullpen as bp
from app.features import lineup_features as lf
from app.features import projections as pj
from app.features import statcast_features as sc
from app.features import streaks as sk
from app.features import weather_features as wx
from app.features.asof import AsOfStore, season_start_utc
from app.features.context import GameContext
from app.features.elo import AsOfElo
from app.features.registry import REGISTRY, FeatureSpec, feature_keys
from app.features.shrinkage import (
    K_H2H,
    K_PITCHER_BB_PCT,
    K_PITCHER_K_PCT,
    K_TEAM_RUNS_PER_GAME,
    MIN_RELIEF_APPEARANCES,
    MIN_STARTS,
    MIN_TEAM_GAMES,
    FeatureValue,
    shrink_mean,
)

log = get_logger(__name__)

# The default feature set. `fs_v2` adds the starting-pitcher Statcast group;
# it becomes the default only once the walk-forward comparison says it earns it,
# and until then FEATURE_SET_VERSION selects it via configuration rather than by
# an edit here. Either way the builder computes both — the version decides what
# reaches the model.
FEATURE_SET_VERSION = settings.feature_set_version

# Innings of relief work a rested bullpen absorbs per day, used only as the
# denominator of the fatigue index (a ratio), never presented as observed data.
RELIEF_IP_PER_DAY_FLOOR = 1.5

# Beyond this many days, a starter's layoff says nothing more about tonight.
MAX_MEANINGFUL_REST = 10.0

# A league rate for the season in progress is shrunk toward the previous
# season's completed rate by this many team-games: the two carry equal weight
# at 150 games, about ten days into April, and the previous season is a
# twentieth of the answer by October. League rates move a tenth of a run a
# year and a season's first week of them is noise; before the first pitch of
# a season there is no current rate at all, and every prior that reads one —
# every shrunk feature, the projections' league anchors, the run model's
# league mean — was undefined until the store started counting spring
# training, which is not a fix. Pre-registered, not fitted.
LEAGUE_PRIOR_K_TEAM_GAMES = 300.0


@dataclass(frozen=True, slots=True)
class LeagueBaseline:
    """As-of league rates used as shrinkage priors."""

    runs_per_game: float | None
    era: float | None
    fip_constant: float | None
    whip: float | None
    k_pct: float | None
    bb_pct: float | None
    hr_per_9: float | None
    woba_proxy: float | None
    batter_k_pct: float | None
    errors_per_game: float | None
    def_efficiency: float | None
    relief_era: float | None
    relief_k_minus_bb_pct: float | None
    #: Team-games of the season in progress behind these rates. Zero before
    #: the season's first pitch, when the rates are the previous season's.
    team_games: int

    RATE_FIELDS = (
        "runs_per_game", "era", "fip_constant", "whip", "k_pct", "bb_pct", "hr_per_9",
        "woba_proxy", "batter_k_pct", "errors_per_game", "def_efficiency",
        "relief_era", "relief_k_minus_bb_pct",
    )

    def shrunk_toward(self, prior: LeagueBaseline | None, k: float) -> LeagueBaseline:
        """These rates, regressed toward ``prior``'s by this season's sample.

        Field by field: ``(rate × n + prior × k) / (n + k)`` with ``n`` this
        season's team-games. A rate this season has not produced yet is the
        prior's; a rate the prior lacks is this season's alone.
        """
        if prior is None:
            return self
        n = float(self.team_games)
        values: dict[str, Any] = {}
        for name in self.RATE_FIELDS:
            now, then = getattr(self, name), getattr(prior, name)
            if then is None:
                values[name] = now
            elif now is None or n <= 0:
                values[name] = then
            else:
                values[name] = (float(now) * n + float(then) * k) / (n + k)
        return LeagueBaseline(team_games=self.team_games, **values)


@dataclass(frozen=True, slots=True)
class SideFeatures:
    """Per-team feature values for one side of a matchup."""

    values: dict[str, FeatureValue]
    starter_id: int | None
    starter_status: str
    starter_hand: str | None
    team_games_sample: int

    def get(self, key: str) -> FeatureValue:
        return self.values.get(key, FeatureValue.missing(f"{key} not computed"))


@dataclass(slots=True)
class FeatureVector:
    game_id: int
    as_of: datetime
    feature_set_version: str
    features: dict[str, float | None]
    sample_sizes: dict[str, int]
    estimated_flags: dict[str, bool]
    missing_features: list[str]
    completeness: float
    home: SideFeatures
    away: SideFeatures
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def is_usable(self) -> bool:
        """A vector with no team history at all cannot support a prediction.

        History is this season's regular-season games, or the prior seasons
        the projections pool. Opening day has none of the former and plenty of
        the latter, and a side whose projection stands on a real sample is
        predictable — the season-to-date features are simply missing, as they
        are for any team a week into April. A side with neither is not.
        """
        return (
            self._has_history(self.home)
            and self._has_history(self.away)
            and self.completeness > 0.0
        )

    @staticmethod
    def _has_history(side: SideFeatures) -> bool:
        return side.team_games_sample > 0 or side.get("proj_off_rpg").sample_size > 0


class FeatureBuilder:
    """Builds as-of feature vectors. One instance per store; safe to reuse."""

    def __init__(
        self,
        store: AsOfStore,
        elo: AsOfElo | None = None,
        feature_set_version: str | None = None,
    ) -> None:
        self.feature_set_version = feature_set_version or FEATURE_SET_VERSION
        self.store = store
        self.elo = elo if elo is not None else AsOfElo(store.games)
        self._league_cache: dict[tuple[int, str], LeagueBaseline] = {}
        self._statcast_league_cache: dict[str, sc.StatcastBaseline] = {}
        self._batting_league_cache: dict[str, lf.LeagueBatting] = {}
        self._team_rate_cache: dict[tuple[int, str], dict[int, tuple[float, float, int]]] = {}
        self._streak_index: sk.StreakIndex | None = None
        self.projections = pj.Projections(store)

    # -- league baselines --------------------------------------------------
    def league_baseline(self, season: int, as_of: datetime) -> LeagueBaseline:
        """League rates as of the START of ``as_of``'s calendar day.

        Using the day boundary rather than the exact timestamp is deliberate:
        it is strictly earlier than any prediction made that day, so a game
        played earlier the same day can never influence the prior used for a
        later game. Conservative by construction.

        The season in progress is shrunk toward the previous season's
        completed rates by `LEAGUE_PRIOR_K_TEAM_GAMES`, so the rates exist on
        opening day and settle rather than lurch through April. The previous
        season is read as of the first day of this one — every game of it is
        knowable then and none of this one is.
        """
        day = as_of.astimezone(UTC).date()
        key = (season, day.isoformat())
        cached = self._league_cache.get(key)
        if cached is not None:
            return cached

        cut = datetime(day.year, day.month, day.day, tzinfo=UTC)
        current = self._season_league_rates(season, cut)
        previous = self._season_league_rates(season - 1, season_start_utc(season))
        baseline = current.shrunk_toward(previous, LEAGUE_PRIOR_K_TEAM_GAMES)
        self._league_cache[key] = baseline
        return baseline

    def _season_league_rates(self, season: int, cut: datetime) -> LeagueBaseline:
        """One season's league rates from its regular-season games before ``cut``."""
        key = (season, f"raw:{cut.isoformat()}")
        cached = self._league_cache.get(key)
        if cached is not None:
            return cached
        start = season_start_utc(season)
        team_frame = self.store.league_team_games_asof(cut, start)
        pitch_frame = self.store.league_pitcher_games_asof(cut, start)

        team_stats = agg.team_aggregate(team_frame)
        constant = agg.fip_constant(pitch_frame)
        all_pitching = agg.pitching_aggregate(pitch_frame, constant)
        relief = agg.pitching_aggregate(
            pitch_frame[~pitch_frame["is_starter"].astype(bool)] if not pitch_frame.empty
            else pitch_frame,
            constant,
        )

        baseline = LeagueBaseline(
            runs_per_game=team_stats.runs_per_game,
            era=all_pitching.era,
            fip_constant=constant,
            whip=all_pitching.whip,
            k_pct=all_pitching.k_pct,
            bb_pct=all_pitching.bb_pct,
            hr_per_9=all_pitching.hr_per_9,
            woba_proxy=team_stats.woba_proxy,
            batter_k_pct=team_stats.k_pct,
            errors_per_game=team_stats.errors_per_game,
            def_efficiency=team_stats.def_efficiency,
            relief_era=relief.era,
            relief_k_minus_bb_pct=relief.k_minus_bb_pct,
            team_games=team_stats.games,
        )
        self._league_cache[key] = baseline
        return baseline

    def team_season_rates(
        self, season: int, as_of: datetime
    ) -> dict[int, tuple[float, float, int]]:
        """team_id -> (runs/game, runs allowed/game, games), as of the day boundary."""
        day = as_of.astimezone(UTC).date()
        key = (season, day.isoformat())
        cached = self._team_rate_cache.get(key)
        if cached is not None:
            return cached

        cut = datetime(day.year, day.month, day.day, tzinfo=UTC)
        frame = self.store.league_team_games_asof(cut, season_start_utc(season))
        rates: dict[int, tuple[float, float, int]] = {}
        if not frame.empty:
            grouped = frame.groupby("team_id").agg(
                runs=("runs", "sum"), runs_allowed=("runs_allowed", "sum"),
                games=("game_id", "count"),
            )
            for team_id, row in grouped.iterrows():
                n = int(row["games"])
                if n:
                    rates[int(team_id)] = (
                        float(row["runs"]) / n, float(row["runs_allowed"]) / n, n
                    )
        self._team_rate_cache[key] = rates
        return rates

    # -- side features -----------------------------------------------------
    def build_side(
        self,
        team_id: int,
        opponent_id: int,
        starter_id: int | None,
        starter_status: str,
        opponent_starter_hand: str | None,
        ctx: GameContext,
        as_of: datetime,
        baseline: LeagueBaseline,
        opponent_starter_id: int | None = None,
    ) -> SideFeatures:
        season_start = season_start_utc(ctx.season)
        values: dict[str, FeatureValue] = {}

        season_games = self.store.team_games_asof(team_id, as_of, season_start)
        self.store.assert_as_of(season_games, as_of, f"team {team_id} season window")
        w30_games = self.store.team_games_asof(team_id, as_of, as_of - timedelta(days=30))
        w14_games = self.store.team_games_asof(team_id, as_of, as_of - timedelta(days=14))

        season_stats = agg.team_aggregate(season_games)
        w30_stats = agg.team_aggregate(w30_games)
        w14_stats = agg.team_aggregate(w14_games)

        values.update(
            self._team_strength_values(team_id, ctx, as_of, season_games, season_stats, baseline)
        )
        values.update(
            self._offense_values(
                season_stats, w30_stats, w14_stats, season_games,
                opponent_starter_hand, baseline,
            )
        )
        values.update(self._defense_values(season_stats, baseline))
        values.update(
            self._pitching_values(team_id, starter_id, ctx, as_of, season_start, baseline)
        )
        values.update(self._statcast_starter_values(starter_id, ctx, as_of, season_start))
        values.update(
            self._lineup_values(
                team_id, opponent_starter_id, opponent_starter_hand, ctx, as_of, season_start
            )
        )
        values.update(self._schedule_values(team_id, ctx, as_of))
        values.update(self._history_values(team_id, opponent_id, ctx, as_of, season_start))
        values.update(self._availability_values(team_id, as_of))
        values.update(self._streak_values(team_id, ctx, as_of))
        values.update(self._projection_values(team_id, starter_id, ctx, as_of, baseline))

        return SideFeatures(
            values=values,
            starter_id=starter_id,
            starter_status=starter_status,
            starter_hand=self.store.pitcher_hand(starter_id),
            team_games_sample=season_stats.games,
        )

    # -- component groups --------------------------------------------------
    def _team_strength_values(
        self,
        team_id: int,
        ctx: GameContext,
        as_of: datetime,
        season_games: pd.DataFrame,
        stats: agg.TeamAggregate,
        baseline: LeagueBaseline,
    ) -> dict[str, FeatureValue]:
        as_of_ts = pd.Timestamp(as_of)
        elo_rating = self.elo.rating_at(team_id, as_of_ts)
        elo_n = self.elo.games_rated(team_id, as_of_ts)

        out: dict[str, FeatureValue] = {
            "elo": FeatureValue(elo_rating, elo_n, elo_n < 20),
            "team_win_pct_season": shrink_mean(
                stats.win_pct, stats.games, 0.5, 12, min_sample=MIN_TEAM_GAMES
            ),
            "team_run_diff_per_game": shrink_mean(
                stats.run_diff_per_game, stats.games, 0.0, K_TEAM_RUNS_PER_GAME,
                min_sample=MIN_TEAM_GAMES,
            ),
            "team_pythag_win_pct": shrink_mean(
                stats.pythag_win_pct, stats.games, 0.5, 12, min_sample=MIN_TEAM_GAMES
            ),
        }

        # Home/road split: each side is judged on the split it will play in.
        is_home = team_id == ctx.home_team_id
        if not season_games.empty and "is_home" in season_games.columns:
            split = season_games[season_games["is_home"].astype(bool) == is_home]
            split_stats = agg.team_aggregate(split)
            out["team_home_away_split"] = shrink_mean(
                split_stats.win_pct, split_stats.games, 0.5, 15, min_sample=8
            )
        else:
            out["team_home_away_split"] = FeatureValue.missing("no split history")

        # Strength of schedule: average Elo of opponents faced.
        if not season_games.empty and "opponent_team_id" in season_games.columns:
            opponents = season_games["opponent_team_id"].dropna().astype(int).tolist()
            if opponents:
                ratings = [self.elo.rating_at(o, as_of_ts) for o in opponents]
                out["team_sos"] = FeatureValue(
                    sum(ratings) / len(ratings), len(ratings), len(ratings) < MIN_TEAM_GAMES
                )
            else:
                out["team_sos"] = FeatureValue.missing("no opponents faced")
        else:
            out["team_sos"] = FeatureValue.missing("no opponents faced")

        # Opponent-adjusted offense and run prevention (first-order adjustment).
        rates = self.team_season_rates(ctx.season, as_of)
        league_rpg = baseline.runs_per_game
        if not season_games.empty and league_rpg and rates:
            opponents = season_games["opponent_team_id"].dropna().astype(int).tolist()
            opp_ra = [rates[o][1] for o in opponents if o in rates]
            opp_rs = [rates[o][0] for o in opponents if o in rates]
            if opp_ra and stats.runs_per_game is not None:
                faced = sum(opp_ra) / len(opp_ra)
                out["team_opp_adj_offense"] = FeatureValue(
                    stats.runs_per_game - (faced - league_rpg),
                    stats.games,
                    stats.games < MIN_TEAM_GAMES,
                )
            if opp_rs and stats.runs_allowed_per_game is not None:
                faced = sum(opp_rs) / len(opp_rs)
                out["team_opp_adj_pitching"] = FeatureValue(
                    stats.runs_allowed_per_game - (faced - league_rpg),
                    stats.games,
                    stats.games < MIN_TEAM_GAMES,
                )
        out.setdefault("team_opp_adj_offense", FeatureValue.missing("insufficient history"))
        out.setdefault("team_opp_adj_pitching", FeatureValue.missing("insufficient history"))
        return out

    def _offense_values(
        self,
        season: agg.TeamAggregate,
        w30: agg.TeamAggregate,
        w14: agg.TeamAggregate,
        season_games: pd.DataFrame,
        opponent_starter_hand: str | None,
        baseline: LeagueBaseline,
    ) -> dict[str, FeatureValue]:
        out = {
            "off_runs_per_game_season": shrink_mean(
                season.runs_per_game, season.games, baseline.runs_per_game,
                K_TEAM_RUNS_PER_GAME, min_sample=MIN_TEAM_GAMES,
            ),
            "off_runs_per_game_w30": shrink_mean(
                w30.runs_per_game, w30.games, baseline.runs_per_game,
                K_TEAM_RUNS_PER_GAME, min_sample=MIN_TEAM_GAMES,
            ),
            "off_woba_proxy_season": shrink_mean(
                season.woba_proxy, season.games, baseline.woba_proxy, 20,
                min_sample=MIN_TEAM_GAMES,
            ),
            "off_k_pct_season": shrink_mean(
                season.k_pct, season.games, baseline.batter_k_pct, 15,
                min_sample=MIN_TEAM_GAMES,
            ),
        }

        # Recent form as a bounded deviation from the team's own baseline, so a
        # hot stretch can move but never replace long-run ability.
        baseline_rpg = out["off_runs_per_game_season"].value
        if w14.runs_per_game is not None and baseline_rpg is not None and w14.games >= 3:
            delta = w14.runs_per_game - baseline_rpg
            damped = max(min(delta, 2.0), -2.0) * min(w14.games / 10.0, 1.0)
            out["off_form_delta_w14"] = FeatureValue(damped, w14.games, w14.games < 10)
        else:
            out["off_form_delta_w14"] = FeatureValue.missing("insufficient recent games")

        # Performance against the handedness of tonight's opposing starter.
        if (
            opponent_starter_hand
            and not season_games.empty
            and "opp_starter_hand" in season_games.columns
        ):
            matched = season_games[season_games["opp_starter_hand"] == opponent_starter_hand]
            matched_stats = agg.team_aggregate(matched)
            out["off_vs_hand"] = shrink_mean(
                matched_stats.runs_per_game, matched_stats.games,
                out["off_runs_per_game_season"].value, 15, min_sample=8,
            )
        else:
            out["off_vs_hand"] = FeatureValue.missing(
                "opposing starter handedness unknown"
                if not opponent_starter_hand
                else "no games against this handedness"
            )
        return out

    def _defense_values(
        self, season: agg.TeamAggregate, baseline: LeagueBaseline
    ) -> dict[str, FeatureValue]:
        return {
            "def_errors_per_game": shrink_mean(
                season.errors_per_game, season.games, baseline.errors_per_game, 20,
                min_sample=MIN_TEAM_GAMES,
            ),
            "def_efficiency_proxy": shrink_mean(
                season.def_efficiency, season.games, baseline.def_efficiency, 20,
                min_sample=MIN_TEAM_GAMES,
            ),
        }

    def _pitching_values(
        self,
        team_id: int,
        starter_id: int | None,
        ctx: GameContext,
        as_of: datetime,
        season_start: datetime,
        baseline: LeagueBaseline,
    ) -> dict[str, FeatureValue]:
        out: dict[str, FeatureValue] = {}

        # --- starter ---
        if starter_id is None:
            missing = FeatureValue.missing("starting pitcher not identified")
            for key in ("sp_era_season", "sp_fip_season", "sp_whip_season", "sp_k_pct_season",
                        "sp_bb_pct_season", "sp_k_minus_bb_pct", "sp_hr_per_9",
                        "sp_ip_per_start", "sp_days_rest", "sp_short_rest", "sp_experience"):
                out[key] = missing
            out["sp_identified"] = FeatureValue(0.0, 0, False)
        else:
            season_starts = self.store.pitcher_games_asof(
                starter_id, as_of, season_start, starters_only=True
            )
            self.store.assert_as_of(season_starts, as_of, f"starter {starter_id}")
            career_starts = self.store.pitcher_games_asof(
                starter_id, as_of, None, starters_only=True
            )
            stats = agg.pitching_aggregate(season_starts, baseline.fip_constant)
            n = stats.starts

            out["sp_identified"] = FeatureValue(1.0, 0, False)
            out["sp_era_season"] = shrink_mean(
                stats.era, n, baseline.era, 6, min_sample=MIN_STARTS
            )
            out["sp_fip_season"] = shrink_mean(
                stats.fip, n, baseline.era, 6, min_sample=MIN_STARTS
            )
            out["sp_whip_season"] = shrink_mean(
                stats.whip, n, baseline.whip, 6, min_sample=MIN_STARTS
            )
            out["sp_k_pct_season"] = shrink_mean(
                stats.k_pct, stats.batters_faced, baseline.k_pct, K_PITCHER_K_PCT,
                min_sample=100,
            )
            out["sp_bb_pct_season"] = shrink_mean(
                stats.bb_pct, stats.batters_faced, baseline.bb_pct, K_PITCHER_BB_PCT,
                min_sample=100,
            )
            k_val, bb_val = out["sp_k_pct_season"].value, out["sp_bb_pct_season"].value
            out["sp_k_minus_bb_pct"] = (
                FeatureValue(
                    k_val - bb_val,
                    int(stats.batters_faced),
                    out["sp_k_pct_season"].is_estimated or out["sp_bb_pct_season"].is_estimated,
                )
                if k_val is not None and bb_val is not None
                else FeatureValue.missing("starter rate unavailable")
            )
            out["sp_hr_per_9"] = shrink_mean(
                stats.hr_per_9, stats.innings, baseline.hr_per_9, 30, min_sample=20
            )
            out["sp_ip_per_start"] = shrink_mean(
                stats.ip_per_appearance, n, 5.2, 4, min_sample=MIN_STARTS
            )

            rest, short_rest = self._starter_rest(career_starts, ctx.first_pitch_utc)
            out["sp_days_rest"] = rest
            out["sp_short_rest"] = short_rest
            out["sp_experience"] = FeatureValue(
                float(len(career_starts)), len(career_starts), False
            )

        # --- bullpen (usage and quality, both observable from game logs) ---
        relief_season = self.store.team_pitcher_games_asof(
            team_id, as_of, season_start, relievers_only=True
        )
        relief_w30 = self.store.team_pitcher_games_asof(
            team_id, as_of, as_of - timedelta(days=30), relievers_only=True
        )
        relief_3d = self.store.team_pitcher_games_asof(
            team_id, as_of, as_of - timedelta(days=3), relievers_only=True
        )
        relief_7d = self.store.team_pitcher_games_asof(
            team_id, as_of, as_of - timedelta(days=7), relievers_only=True
        )

        season_relief = agg.pitching_aggregate(relief_season, baseline.fip_constant)
        w30_relief = agg.pitching_aggregate(relief_w30, baseline.fip_constant)

        out["bp_era_w30"] = shrink_mean(
            w30_relief.era, w30_relief.appearances, baseline.relief_era, 25,
            min_sample=MIN_RELIEF_APPEARANCES,
        )
        out["bp_k_minus_bb_pct_season"] = shrink_mean(
            season_relief.k_minus_bb_pct, season_relief.batters_faced,
            baseline.relief_k_minus_bb_pct, 150, min_sample=150,
        )

        ip_3d = agg.sum_column(relief_3d, "outs_pitched") / 3.0
        ip_7d = agg.sum_column(relief_7d, "outs_pitched") / 3.0
        out["bp_ip_last_3d"] = FeatureValue(ip_3d, int(len(relief_3d)), False)

        # Fatigue index: recent workload relative to the team's own season rate.
        season_days = max((as_of - season_start).days, 1)
        per_day = max(season_relief.innings / season_days, RELIEF_IP_PER_DAY_FLOOR)
        fatigue = 0.6 * (ip_3d / (3 * per_day)) + 0.4 * (ip_7d / (7 * per_day))
        out["bp_fatigue_index"] = FeatureValue(
            min(fatigue, 3.0),
            int(len(relief_7d)),
            season_relief.appearances < MIN_RELIEF_APPEARANCES,
        )

        # --- individual availability -------------------------------------
        #
        # Everything above is a team total, and a team total cannot distinguish
        # four innings spread across four arms from four innings out of the two
        # best. This can.
        availability = bp.summarize(
            bp.bullpen_status(
                self.store, team_id, as_of, season_start,
                baseline.relief_k_minus_bb_pct,
            )
        )
        if availability is not None:
            out["bp_available_count"] = FeatureValue(
                float(availability.available_count), availability.corps_size, False
            )
            if availability.available_quality is not None:
                out["bp_available_quality"] = FeatureValue(
                    availability.available_quality, availability.corps_size, False
                )
            if availability.best_reliever_available is not None:
                out["bp_best_reliever_available"] = FeatureValue(
                    availability.best_reliever_available, availability.corps_size, False
                )
        return out

    def _statcast_starter_values(
        self,
        starter_id: int | None,
        ctx: GameContext,
        as_of: datetime,
        season_start: datetime,
    ) -> dict[str, FeatureValue]:
        """Contact quality and stuff for tonight's starter.

        Reports every key as missing — never zero — when Statcast has not been
        ingested for the window, when no starter is named, or when the pitcher
        has nothing on record. A barrel rate of 0.0 would claim a pitcher has
        never allowed hard contact; the absence of a measurement is a different
        state and stays one.
        """
        if not self.store.has_statcast:
            return {
                key: FeatureValue.missing("Statcast not ingested for this window")
                for key in sc.FEATURE_KEYS
            }
        if starter_id is None:
            return {
                key: FeatureValue.missing("starting pitcher not identified")
                for key in sc.FEATURE_KEYS
            }

        season_slice = self.store.pitcher_statcast_asof(starter_id, as_of, season_start)
        self.store.assert_as_of(season_slice, as_of, f"statcast starter {starter_id}")
        prior_slice = self.store.pitcher_statcast_asof(
            starter_id, as_of, season_start_utc(ctx.season - 1)
        )
        # The prior-season window ends where this season begins.
        if not prior_slice.empty:
            prior_slice = prior_slice[prior_slice["game_date_utc"] < season_start]
        recent_slice = self.store.pitcher_statcast_asof(
            starter_id, as_of, sc.recent_window_start(as_of)
        )

        return sc.starter_values(
            season_slice, prior_slice, recent_slice,
            self.statcast_league_baseline(as_of, season_start),
        )

    def _lineup_values(
        self,
        team_id: int,
        opponent_starter_id: int | None,
        opponent_starter_hand: str | None,
        ctx: GameContext,
        as_of: datetime,
        season_start: datetime,
    ) -> dict[str, FeatureValue]:
        """Projected-lineup quality, and the matchup against tonight's arsenal.

        Every input is a completed game cut at ``as_of``. The lineup is projected
        from the team's own recent starts rather than read from a posted one,
        because no posted lineup is knowable at this snapshot
        (LEAKAGE_PREVENTION.md §15).
        """
        if not self.store.has_batter_statcast:
            return {
                key: FeatureValue.missing("Statcast not ingested for this window")
                for key in lf.FEATURE_KEYS
            }

        orders = self.store.batting_orders_asof(team_id, as_of)
        self.store.assert_as_of(orders, as_of, f"team {team_id} batting orders")
        lineup = lf.project_lineup(orders, as_of)
        if lineup.is_empty:
            return {
                key: FeatureValue.missing("no recent starts to project a lineup from")
                for key in lf.FEATURE_KEYS
            }

        league = self.batting_league_baseline(as_of, season_start)
        profiles = {
            slot.player_id: lf.batter_profile(
                self.store.batter_statcast_asof(slot.player_id, as_of, season_start),
                opponent_starter_hand,
                league,
            )
            for slot in lineup.slots
        }

        usage = (
            lf.arsenal_usage(
                self.store.arsenal_asof(
                    opponent_starter_id, as_of, season_start, starters_only=True
                )
            )
            if opponent_starter_id is not None
            else None
        )
        return lf.lineup_values(lineup, profiles, usage, league)

    def batting_league_baseline(
        self, as_of: datetime, season_start: datetime
    ) -> lf.LeagueBatting:
        """League batting rates as of the START of ``as_of``'s calendar day.

        Same day boundary, same cache, same reason as the pitching baseline: an
        afternoon result must not move the prior an evening game on the same
        slate is measured against, and recomputing a league-wide sum once per
        side per game is thousands of full scans a season.
        """
        day = as_of.astimezone(UTC).date()
        key = day.isoformat()
        cached = self._batting_league_cache.get(key)
        if cached is not None:
            return cached

        cut = datetime(day.year, day.month, day.day, tzinfo=UTC)
        baseline = lf.league_batting(
            self.store.league_batter_statcast_asof(cut, season_start)
        )
        self._batting_league_cache[key] = baseline
        return baseline

    def statcast_league_baseline(
        self, as_of: datetime, season_start: datetime
    ) -> sc.StatcastBaseline:
        """League Statcast rates as of the START of ``as_of``'s calendar day.

        The day boundary, not the exact timestamp, for the same reason
        `league_baseline` uses it: it is strictly earlier than any prediction
        made that day, so an afternoon game cannot move the prior that an
        evening game on the same slate is measured against. Cached on that key,
        which also takes the cost from once per side per game — a full sum over
        every starter's season, thousands of times — to once per day.
        """
        day = as_of.astimezone(UTC).date()
        key = day.isoformat()
        cached = self._statcast_league_cache.get(key)
        if cached is not None:
            return cached

        cut = datetime(day.year, day.month, day.day, tzinfo=UTC)
        league = self.store.league_pitcher_statcast_asof(
            cut, season_start, starters_only=True
        )
        baseline = sc.StatcastBaseline.from_rates(sc.summarize(league))
        self._statcast_league_cache[key] = baseline
        return baseline

    @staticmethod
    def _starter_rest(
        career_starts: pd.DataFrame, first_pitch: datetime
    ) -> tuple[FeatureValue, FeatureValue]:
        """Days since the starter's previous start, and a short-rest flag.

        Capped at MAX_MEANINGFUL_REST because beyond that the gap is an injured
        list return or a debut, and the marginal information about tonight is
        nil — leaving it uncapped let a rookie with one start on record show a
        24-day "rest edge", which is noise dressed up as a finding.
        """
        if career_starts.empty:
            return (
                FeatureValue.missing("no prior starts on record"),
                FeatureValue.missing("no prior starts on record"),
            )
        n = len(career_starts)
        last = career_starts["game_date_utc"].max()
        days = (pd.Timestamp(first_pitch) - pd.Timestamp(last)).total_seconds() / 86400.0
        raw_days = max(days, 0.0)
        capped = min(raw_days, MAX_MEANINGFUL_REST)
        # One start on record cannot establish a rotation cadence.
        estimated = n < 2 or raw_days > MAX_MEANINGFUL_REST
        return (
            FeatureValue(capped, n, estimated),
            FeatureValue(1.0 if capped < 4.0 else 0.0, n, estimated),
        )

    def _schedule_values(
        self, team_id: int, ctx: GameContext, as_of: datetime
    ) -> dict[str, FeatureValue]:
        schedule = self.store.team_schedule_before(team_id, as_of)
        played = (
            schedule[schedule["is_final"].fillna(False).astype(bool)]
            if not schedule.empty
            else schedule
        )

        out: dict[str, FeatureValue] = {}
        if played.empty:
            for key in ("sched_days_rest", "sched_travel_km", "sched_timezone_shift",
                        "sched_day_after_night"):
                out[key] = FeatureValue.missing("no previous game on record")
            out["sched_games_last_7d"] = FeatureValue(0.0, 0, True)
            return out

        previous = played.iloc[-1]
        prev_time = pd.Timestamp(previous["game_date_utc"])
        rest_days = (pd.Timestamp(ctx.first_pitch_utc) - prev_time).total_seconds() / 86400.0
        out["sched_days_rest"] = FeatureValue(
            max(min(rest_days, 14.0), 0.0), int(len(played)), False
        )

        window_start = pd.Timestamp(as_of) - pd.Timedelta(days=7)
        recent = played[played["game_date_utc"] >= window_start]
        out["sched_games_last_7d"] = FeatureValue(float(len(recent)), int(len(played)), False)

        # Travel and time-zone shift from the previous venue.
        prev_park = self.store.ballpark(
            int(previous["venue_id"]) if pd.notna(previous.get("venue_id")) else None
        )
        this_park = self.store.ballpark(ctx.venue_id)
        if (
            prev_park is not None and this_park is not None
            and pd.notna(prev_park.get("latitude")) and pd.notna(this_park.get("latitude"))
        ):
            km = agg.haversine_km(
                float(prev_park["latitude"]), float(prev_park["longitude"]),
                float(this_park["latitude"]), float(this_park["longitude"]),
            )
            out["sched_travel_km"] = FeatureValue(km, int(len(played)), False)
            prev_off = agg.utc_offset_hours(prev_park.get("timezone"), ctx.first_pitch_utc)
            this_off = agg.utc_offset_hours(this_park.get("timezone"), ctx.first_pitch_utc)
            out["sched_timezone_shift"] = (
                FeatureValue(abs(this_off - prev_off), int(len(played)), False)
                if prev_off is not None and this_off is not None
                else FeatureValue.missing("ballpark timezone unavailable")
            )
        else:
            out["sched_travel_km"] = FeatureValue.missing("ballpark coordinates unavailable")
            out["sched_timezone_shift"] = FeatureValue.missing(
                "ballpark coordinates unavailable"
            )

        day_after_night = (
            1.0
            if (ctx.day_night == "day" and previous.get("day_night") == "night"
                and (pd.Timestamp(ctx.first_pitch_utc) - prev_time) <= pd.Timedelta(days=1.5))
            else 0.0
        )
        out["sched_day_after_night"] = FeatureValue(day_after_night, int(len(played)), False)
        return out

    def _history_values(
        self,
        team_id: int,
        opponent_id: int,
        ctx: GameContext,
        as_of: datetime,
        season_start: datetime,
    ) -> dict[str, FeatureValue]:
        season_games = self.store.team_games_asof(team_id, as_of, season_start)
        if season_games.empty or "opponent_team_id" not in season_games.columns:
            return {"h2h_season_series": FeatureValue.missing("no season series on record")}
        head_to_head = season_games[season_games["opponent_team_id"] == opponent_id]
        if head_to_head.empty:
            return {"h2h_season_series": FeatureValue.missing("no season series on record")}
        # Shrunk hard (k=40): a six-game series moves this ~13% toward observed.
        return {
            "h2h_season_series": shrink_mean(
                float(head_to_head["won"].mean()), len(head_to_head), 0.5, K_H2H, min_sample=6
            )
        }

    def _streak_values(
        self, team_id: int, ctx: GameContext, as_of: datetime
    ) -> dict[str, FeatureValue]:
        """Candidate streak features (fs_v8). Index built once, lazily —
        it replays the same games frame every other feature reads."""
        if self._streak_index is None:
            self._streak_index = sk.StreakIndex(self.store.games)
        return self._streak_index.side_values(team_id, ctx.season, as_of)

    def _projection_values(
        self,
        team_id: int,
        starter_id: int | None,
        ctx: GameContext,
        as_of: datetime,
        baseline: LeagueBaseline,
    ) -> dict[str, FeatureValue]:
        """Multi-season projections (fs_v9): team scoring and prevention, and
        the starter's K−BB% and FIP, each pooled over prior seasons and the
        season in progress. See features/projections.py."""
        out = self.projections.team_values(
            team_id, ctx.season, as_of, baseline.runs_per_game
        )
        # The league FIP constant is league ERA minus the raw numerator rate, so
        # the raw rate the projection regresses toward is their difference.
        numerator_now = (
            baseline.era - baseline.fip_constant
            if baseline.era is not None and baseline.fip_constant is not None
            else None
        )
        out.update(
            self.projections.starter_values(
                starter_id, ctx.season, as_of,
                baseline.k_pct, baseline.bb_pct, baseline.fip_constant, numerator_now,
            )
        )
        return out

    def _availability_values(
        self, team_id: int, as_of: datetime
    ) -> dict[str, FeatureValue]:
        """How much of the team's recent record is on the injured list.

        Reports missing rather than zero when the transaction feed is empty or
        the window is too short to divide. Nobody injured and no idea who is
        injured are different states, and a zero would say the first when the
        truth is the second.
        """
        loss = av.availability_loss(self.store, team_id, as_of)
        out: dict[str, FeatureValue] = {}
        out["il_offense_lost"] = (
            FeatureValue(loss.offense, loss.team_pa, False)
            if loss.offense is not None
            else FeatureValue.missing("no batting window to divide")
        )
        out["il_pitching_lost"] = (
            FeatureValue(loss.pitching, loss.team_batters_faced, False)
            if loss.pitching is not None
            else FeatureValue.missing("no pitching window to divide")
        )
        return out

    # -- assembly ----------------------------------------------------------
    def build(self, ctx: GameContext, as_of: datetime) -> FeatureVector:
        if as_of >= ctx.first_pitch_utc:
            raise ValueError(
                f"as_of {as_of.isoformat()} is not before first pitch "
                f"{ctx.first_pitch_utc.isoformat()}."
            )

        baseline = self.league_baseline(ctx.season, as_of)
        home_hand = self.store.pitcher_hand(ctx.home_starter_id)
        away_hand = self.store.pitcher_hand(ctx.away_starter_id)

        home = self.build_side(
            ctx.home_team_id, ctx.away_team_id, ctx.home_starter_id,
            ctx.home_starter_status, away_hand, ctx, as_of, baseline,
            opponent_starter_id=ctx.away_starter_id,
        )
        away = self.build_side(
            ctx.away_team_id, ctx.home_team_id, ctx.away_starter_id,
            ctx.away_starter_status, home_hand, ctx, as_of, baseline,
            opponent_starter_id=ctx.home_starter_id,
        )

        features: dict[str, float | None] = {}
        samples: dict[str, int] = {}
        estimated: dict[str, bool] = {}
        missing: list[str] = []

        def emit(key: str, value: FeatureValue) -> None:
            features[key] = None if value.value is None else float(value.value)
            samples[key] = value.sample_size
            estimated[key] = value.is_estimated
            if value.value is None:
                missing.append(key)

        # Differences where a HIGHER raw value favors the team.
        for key, source in (
            ("elo_diff", "elo"),
            ("team_win_pct_season_diff", "team_win_pct_season"),
            ("team_run_diff_per_game_diff", "team_run_diff_per_game"),
            ("team_pythag_win_pct_diff", "team_pythag_win_pct"),
            ("team_home_away_split_diff", "team_home_away_split"),
            ("team_sos_diff", "team_sos"),
            ("team_opp_adj_offense_diff", "team_opp_adj_offense"),
            ("off_runs_per_game_w30_diff", "off_runs_per_game_w30"),
            ("off_runs_per_game_season_diff", "off_runs_per_game_season"),
            ("off_form_delta_w14_diff", "off_form_delta_w14"),
            ("off_woba_proxy_season_diff", "off_woba_proxy_season"),
            ("off_vs_hand_diff", "off_vs_hand"),
            ("sp_k_pct_season_diff", "sp_k_pct_season"),
            ("sp_k_minus_bb_pct_diff", "sp_k_minus_bb_pct"),
            ("sp_ip_per_start_diff", "sp_ip_per_start"),
            ("sp_days_rest_diff", "sp_days_rest"),
            ("sp_experience_diff", "sp_experience"),
            ("bp_k_minus_bb_pct_season_diff", "bp_k_minus_bb_pct_season"),
            ("bp_available_count_diff", "bp_available_count"),
            ("bp_available_quality_diff", "bp_available_quality"),
            ("bp_best_reliever_available_diff", "bp_best_reliever_available"),
            ("def_efficiency_proxy_diff", "def_efficiency_proxy"),
            ("sched_days_rest_diff", "sched_days_rest"),
            ("h2h_season_series_shrunk_diff", "h2h_season_series"),
            ("sc_sp_whiff_pct_diff", "sc_sp_whiff_pct"),
            ("sc_sp_chase_pct_diff", "sc_sp_chase_pct"),
            ("sc_sp_csw_pct_diff", "sc_sp_csw_pct"),
            ("sc_sp_fastball_velocity_diff", "sc_sp_fastball_velocity"),
            ("sc_sp_velocity_delta_30d_diff", "sc_sp_velocity_delta_30d"),
            ("lineup_xwoba_weighted_diff", "lineup_xwoba_weighted"),
            ("lineup_xwoba_vs_hand_diff", "lineup_xwoba_vs_hand"),
            ("arsenal_xwoba_edge_diff", "arsenal_xwoba_edge"),
            ("sk_win_streak_capped_diff", "sk_win_streak"),
            ("sk_continue_prob_diff", "sk_continue_prob"),
            ("sk_adjusted_effect_diff", "sk_adjusted_effect"),
            ("sk_streak_run_diff_diff", "sk_streak_run_diff"),
            ("sk_streak_opp_elo_diff", "sk_streak_opp_elo"),
            ("proj_off_rpg_diff", "proj_off_rpg"),
            ("proj_sp_k_minus_bb_pct_diff", "proj_sp_k_minus_bb_pct"),
        ):
            emit(key, _diff(home.get(source), away.get(source)))

        # Differences where a LOWER raw value favors the team: away minus home.
        for key, source in (
            ("team_opp_adj_pitching_diff", "team_opp_adj_pitching"),
            ("off_k_pct_season_diff", "off_k_pct_season"),
            ("sp_era_season_diff", "sp_era_season"),
            ("sp_fip_season_diff", "sp_fip_season"),
            ("sp_whip_season_diff", "sp_whip_season"),
            ("sp_bb_pct_season_diff", "sp_bb_pct_season"),
            ("sp_hr_per_9_diff", "sp_hr_per_9"),
            ("sp_short_rest_diff", "sp_short_rest"),
            ("bp_era_w30_diff", "bp_era_w30"),
            ("bp_fatigue_index_diff", "bp_fatigue_index"),
            ("bp_ip_last_3d_diff", "bp_ip_last_3d"),
            ("def_errors_per_game_diff", "def_errors_per_game"),
            ("sched_travel_km_diff", "sched_travel_km"),
            ("sched_timezone_shift_diff", "sched_timezone_shift"),
            ("sched_games_last_7d_diff", "sched_games_last_7d"),
            ("sched_day_after_night_diff", "sched_day_after_night"),
            ("sc_sp_xwoba_allowed_diff", "sc_sp_xwoba_allowed"),
            ("sc_sp_barrel_pct_allowed_diff", "sc_sp_barrel_pct_allowed"),
            ("sc_sp_hard_hit_pct_allowed_diff", "sc_sp_hard_hit_pct_allowed"),
            ("sc_sp_avg_exit_velocity_allowed_diff", "sc_sp_avg_exit_velocity_allowed"),
            ("lineup_whiff_pct_weighted_diff", "lineup_whiff_pct_weighted"),
            ("arsenal_whiff_edge_diff", "arsenal_whiff_edge"),
            ("il_offense_lost_diff", "il_offense_lost"),
            ("il_pitching_lost_diff", "il_pitching_lost"),
            ("sk_loss_streak_capped_diff", "sk_loss_streak"),
            ("proj_ra_rpg_diff", "proj_ra_rpg"),
            ("proj_sp_fip_diff", "proj_sp_fip"),
        ):
            emit(key, _diff(away.get(source), home.get(source)))

        # Absolute (non-differenced) features.
        emit("lineup_continuity_home", home.get("lineup_continuity"))
        emit("lineup_continuity_away", away.get("lineup_continuity"))
        emit("sp_identified_home", home.get("sp_identified"))
        emit("sp_identified_away", away.get("sp_identified"))
        emit("env_home_field", FeatureValue(1.0, 0, False))

        # Forecast weather. Game-level rather than per-side: the air is shared,
        # which is exactly why two of the three are absolute and only the
        # interaction with each staff's fly-ball tendency can move a margin.
        season_start = season_start_utc(ctx.season)
        for key, value in wx.weather_values(
            self.store,
            ctx.game_id,
            as_of,
            self.store.team_pitcher_games_asof(ctx.home_team_id, as_of, season_start),
            self.store.team_pitcher_games_asof(ctx.away_team_id, as_of, season_start),
        ).items():
            emit(key, value)

        park = self.store.ballpark(ctx.venue_id)
        if park is not None:
            roof = (park.get("roof_type") or "").lower()
            emit("env_is_dome", FeatureValue(1.0 if "dome" in roof or "closed" in roof else 0.0,
                                             0, False))
            elevation = park.get("elevation_ft")
            emit(
                "env_venue_elevation_km",
                FeatureValue(float(elevation) * 0.0003048, 0, False)
                if pd.notna(elevation)
                else FeatureValue.missing("ballpark elevation unavailable"),
            )
        else:
            emit("env_is_dome", FeatureValue.missing("ballpark not on record"))
            emit("env_venue_elevation_km", FeatureValue.missing("ballpark not on record"))

        expected = feature_keys(self.feature_set_version)
        present = [k for k in expected if features.get(k) is not None]
        completeness = self._completeness(home, away, features)

        return FeatureVector(
            game_id=ctx.game_id,
            as_of=as_of,
            feature_set_version=self.feature_set_version,
            features={k: features.get(k) for k in expected},
            sample_sizes={k: samples.get(k, 0) for k in expected},
            estimated_flags={k: estimated.get(k, True) for k in expected},
            missing_features=[k for k in expected if features.get(k) is None],
            completeness=completeness,
            home=home,
            away=away,
            context={
                "n_features": len(expected),
                "n_present": len(present),
                "home_starter_id": ctx.home_starter_id,
                "away_starter_id": ctx.away_starter_id,
                "home_starter_hand": home.starter_hand,
                "away_starter_hand": away.starter_hand,
            },
        )

    @staticmethod
    def _completeness(
        home: SideFeatures, away: SideFeatures, features: dict[str, float | None]
    ) -> float:
        """Weighted coverage of the categories the active model consumes.

        Weights follow DATA_SOURCES.md §6 and re-normalize automatically as
        later phases add categories.
        """
        components = {
            "schedule_teams_venue": (
                0.20,
                1.0 if features.get("env_home_field") is not None else 0.0,
            ),
            "starters_identified": (
                0.25,
                0.5 * float(features.get("sp_identified_home") or 0.0)
                + 0.5 * float(features.get("sp_identified_away") or 0.0),
            ),
            "starter_history": (
                0.20,
                1.0 if features.get("sp_fip_season_diff") is not None else 0.0,
            ),
            "team_form_history": (
                0.25,
                min(home.team_games_sample, away.team_games_sample) / MIN_TEAM_GAMES
                if MIN_TEAM_GAMES
                else 1.0,
            ),
            "rest_travel": (
                0.10,
                1.0 if features.get("sched_days_rest_diff") is not None else 0.0,
            ),
        }
        total_weight = sum(w for w, _ in components.values())
        score = sum(w * min(max(v, 0.0), 1.0) for w, v in components.values())
        return round(score / total_weight, 4) if total_weight else 0.0


def _diff(a: FeatureValue, b: FeatureValue) -> FeatureValue:
    if a.value is None or b.value is None:
        return FeatureValue.missing(a.detail or b.detail or "one side unavailable")
    return FeatureValue(
        value=a.value - b.value,
        sample_size=min(a.sample_size, b.sample_size),
        is_estimated=a.is_estimated or b.is_estimated,
    )


def active_specs(feature_set_version: str | None = None) -> list[FeatureSpec]:
    """The specs of the feature set the model is configured to consume.

    Read from the configured version rather than from the `FS_V1` constant, so
    promoting a feature set changes what the feature dictionary and the
    diagnostics screen report about the model actually being served.
    """
    return [REGISTRY[key] for key in feature_keys(feature_set_version or FEATURE_SET_VERSION)]
