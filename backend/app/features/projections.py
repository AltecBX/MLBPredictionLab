"""Multi-season projections: what a season-to-date rate should be shrunk toward.

Every rate feature in ``fs_v1`` is season-to-date, regressed toward the
*league* mean. That is the right prior for an entity nobody has seen before
and the wrong one for everybody else: on Opening Day a team that outscored its
opponents by a run a game last year is handed the same 0.0 prior as one that
was outscored by a run a game, and a starter with three seasons of a 28% K
rate is handed the league's 22%. The only feature in the set with any memory
across seasons is Elo — which is why, measured on its own, calibrated Elo
matches the forty-two-feature model (MODELING_PLAN.md § XGBoost, LightGBM,
Elo and the stack). The other forty-one spend April and May relearning what
the previous season already said.

FEATURE_DICTIONARY.md §1 rule 2 has always said the fix — "prior-season and
three-year baselines are themselves shrunk toward league average before being
used as a prior for the current season" — and nothing had built it. This does,
in the form projection systems have used since Marcel: a weighted pool of the
entity's recent seasons plus the season in progress, regressed toward the
league.

    projection = L_now + Σᵢ wᵢ·(Eᵢ − Lᵢ·Dᵢ) + (E_now − L_now·D_now)
                         ───────────────────────────────────────────
                               Σᵢ wᵢ·Dᵢ + D_now + k

E is the event count (runs, strikeouts, the FIP numerator), D its denominator
(games, batters faced, innings), L the league rate **for that season** — so a
2022 run environment is compared with 2022's league and not 2026's — and k the
regression toward the league, in the denominator's own units. Prior seasons
carry decaying weights; the season in progress carries weight one, so the
projection converges on the observed rate exactly as the existing shrinkage
does, only from a better starting point.

Two properties are deliberate:

* **Nothing here is fitted on the win outcome.** The weights and regression
  constants are the published persistence of these rates — the year-to-year
  correlation of a team's run differential, of a starter's strikeout and walk
  rates — written down before the group was measured, for the reason
  MODELING_PLAN.md § Individual bullpen availability gives: a constant tuned
  on the target it is about to be scored on can manufacture its own verdict.
* **Every input is an as-of slice.** A prior season is read through the same
  ``knowledge_time`` cut as everything else. That a completed season is fully
  knowable by the next April is a consequence of the cut, not an assumption
  layered on top of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pandas as pd

from app.features.asof import AsOfStore, season_start_utc
from app.features.shrinkage import FeatureValue

# Weight on each prior season, most recent first. Team rosters turn over
# faster than a pitcher's own skills do, so a team's second prior season is
# worth less and a third is worth nothing.
TEAM_SEASON_WEIGHTS: tuple[float, ...] = (0.6, 0.3)
PITCHER_SEASON_WEIGHTS: tuple[float, ...] = (0.6, 0.35, 0.2)
CURRENT_SEASON_WEIGHT = 1.0

# Regression toward the league, in each denominator's own units. A team's run
# differential persists at roughly r ≈ 0.55 season to season, which at the
# weighted evidence a prior season contributes here (~100 games) is about
# eighty games of league average; a starter's K% and BB% persist at r ≈ 0.7
# and 0.55 across ~700 batters faced, and his fielding-independent rate at
# about 0.5 across ~180 innings.
K_TEAM_RUNS_PER_GAME = 80.0
K_PITCHER_BATTERS_FACED = 300.0
K_PITCHER_INNINGS = 200.0

# Below these, the season in progress alone would say little and the
# projection is flagged estimated — the same discipline every other feature
# follows, with the same consequence: shown, with its sample size beside it.
MIN_TEAM_GAMES = 10
MIN_STARTER_BATTERS_FACED = 100

FEATURE_KEYS = (
    "proj_off_rpg",
    "proj_ra_rpg",
    "proj_sp_k_minus_bb_pct",
    "proj_sp_fip",
)


@dataclass(frozen=True, slots=True)
class SeasonRates:
    """League-wide rates for one completed season, all pitchers and all teams."""

    runs_per_game: float | None
    k_pct: float | None
    bb_pct: float | None
    fip_numerator_per_inning: float | None


@dataclass(frozen=True, slots=True)
class Pooled:
    """A pooled deviation from the league, and the evidence behind it."""

    deviation: float
    evidence: float
    current_denominator: float
    seasons_used: int

    def rate(self, league_now: float) -> float:
        return league_now + self.deviation


def _sum(frame: pd.DataFrame, column: str) -> float:
    if frame.empty or column not in frame.columns:
        return 0.0
    return float(frame[column].fillna(0).sum())


def pool(
    seasons: list[tuple[float, float, float, float]],
    current: tuple[float, float, float],
    k: float,
) -> Pooled:
    """Pool prior seasons and the season in progress into one deviation.

    ``seasons`` are ``(weight, events, denominator, league_rate)`` per prior
    season; ``current`` is ``(events, denominator, league_rate)`` for the
    season in progress. A season with no denominator contributes nothing and
    is not counted.
    """
    numerator = 0.0
    evidence = 0.0
    used = 0
    for weight, events, denominator, league in seasons:
        if denominator <= 0:
            continue
        numerator += weight * (events - league * denominator)
        evidence += weight * denominator
        used += 1
    events_now, denominator_now, league_now = current
    if denominator_now > 0:
        numerator += CURRENT_SEASON_WEIGHT * (events_now - league_now * denominator_now)
        evidence += CURRENT_SEASON_WEIGHT * denominator_now
    deviation = numerator / (evidence + k) if (evidence + k) > 0 else 0.0
    return Pooled(deviation, evidence, max(denominator_now, 0.0), used)


class Projections:
    """Per-entity multi-season projections, read through the as-of store."""

    def __init__(self, store: AsOfStore) -> None:
        self.store = store
        self._season_cache: dict[int, SeasonRates] = {}

    # -- league rates for completed seasons ---------------------------------
    def season_rates(self, season: int, as_of: datetime) -> SeasonRates:
        """League rates for a completed season, cut at ``as_of``.

        Cached per season once ``as_of`` is past the season's end: a finished
        season does not change. Before that — a prior season is only ever read
        from the following one, so this is a guard rather than a path — it is
        computed uncached at the exact cut.
        """
        cached = self._season_cache.get(season)
        if cached is not None:
            return cached
        start = season_start_utc(season)
        end = season_start_utc(season + 1)
        cut = min(as_of, end)
        team = self.store.league_team_games_asof(cut, start)
        pitch = self.store.league_pitcher_games_asof(cut, start)
        rates = SeasonRates(
            runs_per_game=(_sum(team, "runs") / len(team)) if len(team) else None,
            k_pct=_rate(pitch, "so_pitched", "batters_faced"),
            bb_pct=_rate(pitch, "bb_allowed", "batters_faced"),
            fip_numerator_per_inning=_fip_numerator_rate(pitch),
        )
        if as_of >= end:
            self._season_cache[season] = rates
        return rates

    # -- teams ---------------------------------------------------------------
    def team_values(
        self,
        team_id: int,
        season: int,
        as_of: datetime,
        league_runs_per_game_now: float | None,
    ) -> dict[str, FeatureValue]:
        """Projected runs scored and allowed per game for one team."""
        if league_runs_per_game_now is None or league_runs_per_game_now <= 0:
            missing = FeatureValue.missing("league scoring rate unavailable")
            return {"proj_off_rpg": missing, "proj_ra_rpg": missing}

        earliest = season - len(TEAM_SEASON_WEIGHTS)
        frame = self.store.team_games_asof(team_id, as_of, season_start_utc(earliest))
        by_season = _by_season(frame, season)

        out: dict[str, FeatureValue] = {}
        for key, column in (("proj_off_rpg", "runs"), ("proj_ra_rpg", "runs_allowed")):
            priors = []
            for offset, weight in enumerate(TEAM_SEASON_WEIGHTS, start=1):
                prior_season = season - offset
                rows = by_season.get(prior_season)
                league = self.season_rates(prior_season, as_of).runs_per_game
                if rows is None or rows.empty or league is None:
                    continue
                priors.append((weight, _sum(rows, column), float(len(rows)), league))
            now = by_season.get(season)
            current = (
                (_sum(now, column), float(len(now)), league_runs_per_game_now)
                if now is not None and not now.empty
                else (0.0, 0.0, league_runs_per_game_now)
            )
            pooled = pool(priors, current, K_TEAM_RUNS_PER_GAME)
            out[key] = FeatureValue(
                value=pooled.rate(league_runs_per_game_now),
                sample_size=int(pooled.evidence),
                is_estimated=(
                    pooled.seasons_used == 0 and pooled.current_denominator < MIN_TEAM_GAMES
                ),
                detail=None if pooled.evidence > 0 else "league prior only",
            )
        return out

    # -- starting pitchers ---------------------------------------------------
    def starter_values(
        self,
        pitcher_id: int | None,
        season: int,
        as_of: datetime,
        league_k_pct_now: float | None,
        league_bb_pct_now: float | None,
        fip_constant_now: float | None,
        league_fip_numerator_now: float | None,
    ) -> dict[str, FeatureValue]:
        """Projected K−BB% and FIP for one starter, from his starts only."""
        if pitcher_id is None:
            missing = FeatureValue.missing("starting pitcher not identified")
            return {"proj_sp_k_minus_bb_pct": missing, "proj_sp_fip": missing}
        if None in (league_k_pct_now, league_bb_pct_now, fip_constant_now,
                    league_fip_numerator_now):
            missing = FeatureValue.missing("league pitching rates unavailable")
            return {"proj_sp_k_minus_bb_pct": missing, "proj_sp_fip": missing}
        assert league_k_pct_now is not None and league_bb_pct_now is not None
        assert fip_constant_now is not None and league_fip_numerator_now is not None

        earliest = season - len(PITCHER_SEASON_WEIGHTS)
        frame = self.store.pitcher_games_asof(
            pitcher_id, as_of, season_start_utc(earliest), starters_only=True
        )
        by_season = _by_season(frame, season)

        k_priors, bb_priors, fip_priors = [], [], []
        for offset, weight in enumerate(PITCHER_SEASON_WEIGHTS, start=1):
            prior_season = season - offset
            rows = by_season.get(prior_season)
            if rows is None or rows.empty:
                continue
            league = self.season_rates(prior_season, as_of)
            bf = _sum(rows, "batters_faced")
            innings = _sum(rows, "outs_pitched") / 3.0
            if league.k_pct is not None and league.bb_pct is not None and bf > 0:
                k_priors.append((weight, _sum(rows, "so_pitched"), bf, league.k_pct))
                bb_priors.append((weight, _sum(rows, "bb_allowed"), bf, league.bb_pct))
            if league.fip_numerator_per_inning is not None and innings > 0:
                fip_priors.append(
                    (weight, _fip_numerator(rows), innings, league.fip_numerator_per_inning)
                )

        now = by_season.get(season)
        if now is not None and not now.empty:
            bf_now = _sum(now, "batters_faced")
            innings_now = _sum(now, "outs_pitched") / 3.0
            k_now = (_sum(now, "so_pitched"), bf_now, league_k_pct_now)
            bb_now = (_sum(now, "bb_allowed"), bf_now, league_bb_pct_now)
            fip_now = (_fip_numerator(now), innings_now, league_fip_numerator_now)
        else:
            bf_now = 0.0
            k_now = (0.0, 0.0, league_k_pct_now)
            bb_now = (0.0, 0.0, league_bb_pct_now)
            fip_now = (0.0, 0.0, league_fip_numerator_now)

        k_pooled = pool(k_priors, k_now, K_PITCHER_BATTERS_FACED)
        bb_pooled = pool(bb_priors, bb_now, K_PITCHER_BATTERS_FACED)
        fip_pooled = pool(fip_priors, fip_now, K_PITCHER_INNINGS)

        estimated = k_pooled.seasons_used == 0 and bf_now < MIN_STARTER_BATTERS_FACED
        detail = None if k_pooled.evidence > 0 else "league prior only"
        return {
            "proj_sp_k_minus_bb_pct": FeatureValue(
                value=k_pooled.rate(league_k_pct_now) - bb_pooled.rate(league_bb_pct_now),
                sample_size=int(k_pooled.evidence),
                is_estimated=estimated,
                detail=detail,
            ),
            "proj_sp_fip": FeatureValue(
                value=fip_pooled.rate(league_fip_numerator_now) + fip_constant_now,
                sample_size=int(fip_pooled.evidence),
                is_estimated=estimated,
                detail=detail,
            ),
        }


# -- helpers -------------------------------------------------------------------
def _by_season(frame: pd.DataFrame, current_season: int) -> dict[int, pd.DataFrame]:
    """Split an as-of slice into calendar seasons.

    ``team_games`` carries a ``season`` column; ``pitcher_games`` does not, and
    the UTC year is the season for every game MLB has played since the
    schedule moved off New Year's Eve, which is to say always.
    """
    if frame.empty:
        return {}
    if "season" in frame.columns:
        seasons = frame["season"].astype(int)
    else:
        seasons = pd.DatetimeIndex(frame["game_date_utc"]).tz_convert(UTC).year
    return {int(s): rows for s, rows in frame.groupby(seasons.to_numpy())}


def _rate(frame: pd.DataFrame, events: str, denominator: str) -> float | None:
    d = _sum(frame, denominator)
    return (_sum(frame, events) / d) if d > 0 else None


def _fip_numerator(frame: pd.DataFrame) -> float:
    """``13·HR + 3·(BB + HBP) − 2·K`` — FIP before the constant, times innings."""
    return (
        13.0 * _sum(frame, "hr_allowed")
        + 3.0 * (_sum(frame, "bb_allowed") + _sum(frame, "hbp_allowed"))
        - 2.0 * _sum(frame, "so_pitched")
    )


def _fip_numerator_rate(frame: pd.DataFrame) -> float | None:
    innings = _sum(frame, "outs_pitched") / 3.0
    return (_fip_numerator(frame) / innings) if innings > 0 else None


__all__ = [
    "CURRENT_SEASON_WEIGHT",
    "FEATURE_KEYS",
    "K_PITCHER_BATTERS_FACED",
    "K_PITCHER_INNINGS",
    "K_TEAM_RUNS_PER_GAME",
    "PITCHER_SEASON_WEIGHTS",
    "TEAM_SEASON_WEIGHTS",
    "Pooled",
    "Projections",
    "SeasonRates",
    "pool",
]
