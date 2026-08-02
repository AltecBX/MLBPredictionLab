"""Park factors, computed in-house and as-of.

The `park_factors` table is empty. DATA_SOURCES.md files it under Phase 2 and no
provider is enabled, which under this repository's rules means every park feature
renders UNAVAILABLE — correctly, because a park factor invented from nothing is
exactly the placeholder the product rules forbid.

But a park factor does not need a provider. It is a ratio of runs the database
already holds, and the way to stop rendering UNAVAILABLE is to measure the thing
rather than to buy it. The classic construction uses the same team home and away
so that team quality cancels:

    PF(T) = runs per team-game in T's home games
          ÷ runs per team-game in T's road games

A good offence inflates the numerator and the denominator alike and drops out.
What does not cancel is the opponent slate, which differs slightly between a
team's home and road schedules; across a season that is close to balanced and the
residual is far smaller than the park effects being measured. Interleague and
unbalanced divisional schedules are the reason this is a ratio of ratios rather
than a comparison against the league.

Two rules a naive version would break:

* **As-of.** Only games knowable strictly before the prediction are counted, so a
  park factor never contains a game it is about to help predict.
* **All history, not season-to-date.** A park is a building; last season's games
  in it are knowable and are used. That matters most in April, where the
  season-to-date version is pure noise at exactly the moment a park factor would
  otherwise be doing its most useful work.

**Applying it takes two steps, not one.** A team that plays half its games in
Denver has an inflated scoring rate *because* of Denver, so multiplying its rate
by Denver's factor again counts the park twice. Every rate is first divided by
the average factor of the parks that team has actually played in — its exposure —
and the game's own park is then applied once. Where every park is neutral both
steps are identities and the model is unchanged, which is the property that makes
this a refinement rather than a rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from app.core.logging import get_logger

log = get_logger(__name__)

# Home-and-road games at which a raw factor is trusted halfway. A park is a
# slow-moving quantity measured on a very noisy one — a full season of 81 home
# games still moves a raw factor by several points on luck alone — so the
# constant is deliberately large relative to the sample.
#
# Pre-registered. Nothing downstream chooses it by looking at the answer, and the
# ablation that judges the park model does not search over it.
K_PARK_GAMES = 50

NEUTRAL = 1.0

# Below this, a park factor is not reported at all. Two games at a neutral site
# cannot say anything about the site, and shrinkage alone would quietly return
# something near 1.0 that reads as a measurement.
MIN_GAMES_EACH_WAY = 10


@dataclass(frozen=True, slots=True)
class ParkFactor:
    """One team's home park, as of a moment."""

    team_id: int
    #: Unshrunk ratio. None when there is not enough of a sample to form one.
    raw: float | None
    #: What callers should use. Shrunk toward 1.0; exactly 1.0 when unmeasured.
    value: float
    home_games: int
    road_games: int

    @property
    def is_measured(self) -> bool:
        return self.raw is not None

    def to_dict(self) -> dict[str, float | int | None]:
        return {
            "team_id": self.team_id,
            "raw": None if self.raw is None else round(self.raw, 4),
            "value": round(self.value, 4),
            "home_games": self.home_games,
            "road_games": self.road_games,
        }


class ParkFactors:
    """As-of park factors for all thirty parks, and each team's exposure to them.

    Built once per backtest from the same team-game frame everything else reads,
    then queried per prediction. Every query is a binary search into a cumulative
    sum, because a walk-forward asks this question a few thousand times and a
    groupby per question would dominate the run.
    """

    def __init__(self, games: pd.DataFrame, team_games: pd.DataFrame) -> None:
        self._teams: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = {}
        self._home_venue: dict[int, int] = {}
        self._exposure_cache: dict[tuple[int, int], float] = {}
        self._factor_cache: dict[int, dict[int, float]] = {}

        if team_games.empty:
            return

        frame = team_games[["team_id", "is_home", "runs", "runs_allowed", "knowledge_time"]]
        frame = frame.dropna(subset=["runs", "runs_allowed"])
        if frame.empty:
            return

        # Total runs in the game, which is what a park acts on. Both rows of a
        # game carry the same total, so either side may be used.
        total = frame["runs"].to_numpy(dtype=float) + frame["runs_allowed"].to_numpy(dtype=float)
        frame = frame.assign(_total=total)

        for team_id, group in frame.groupby("team_id", sort=False):
            group = group.sort_values("knowledge_time")
            is_home = group["is_home"].to_numpy(dtype=bool)
            totals = group["_total"].to_numpy(dtype=float)
            self._teams[int(team_id)] = (
                pd.DatetimeIndex(group["knowledge_time"]).tz_convert("UTC").asi8,
                np.cumsum(np.where(is_home, totals, 0.0)),
                np.cumsum(is_home.astype(np.int64)),
                np.cumsum(np.where(is_home, 0.0, totals)),
                np.cumsum((~is_home).astype(np.int64)),
            )

        # A team's own park, so a neutral-site game can be recognised as one. The
        # modal venue of a team's home games is that park by definition; anything
        # else is a neutral site or a temporary ground, and neither is something
        # this team's home/road split measures.
        if not games.empty and "venue_id" in games.columns:
            played = games.dropna(subset=["venue_id"])
            for team_id, group in played.groupby("home_team_id", sort=False):
                venues = group["venue_id"].value_counts()
                if not venues.empty:
                    self._home_venue[int(team_id)] = int(venues.index[0])

    # -- queries -----------------------------------------------------------
    @property
    def is_available(self) -> bool:
        return bool(self._teams)

    def factor(self, team_id: int, as_of: datetime) -> ParkFactor:
        """The factor for ``team_id``'s home park, using only prior games."""
        entry = self._teams.get(int(team_id))
        if entry is None:
            return ParkFactor(int(team_id), None, NEUTRAL, 0, 0)

        knowledge, home_runs, home_n, road_runs, road_n = entry
        cut = int(np.searchsorted(knowledge, _ns(as_of), side="right"))
        if cut == 0:
            return ParkFactor(int(team_id), None, NEUTRAL, 0, 0)

        hg = int(home_n[cut - 1])
        rg = int(road_n[cut - 1])
        if hg < MIN_GAMES_EACH_WAY or rg < MIN_GAMES_EACH_WAY:
            return ParkFactor(int(team_id), None, NEUTRAL, hg, rg)

        home_rate = float(home_runs[cut - 1]) / hg
        road_rate = float(road_runs[cut - 1]) / rg
        if road_rate <= 0:
            return ParkFactor(int(team_id), None, NEUTRAL, hg, rg)

        raw = home_rate / road_rate
        weight = min(hg, rg) / (min(hg, rg) + K_PARK_GAMES)
        return ParkFactor(int(team_id), raw, 1.0 + (raw - 1.0) * weight, hg, rg)

    def for_game(
        self, home_team_id: int, venue_id: int | None, as_of: datetime
    ) -> ParkFactor:
        """The factor for the park a specific game is played in.

        A game at a venue that is not the home team's own park — a neutral site,
        a series abroad, a temporary ground — is not described by that team's
        home/road split, so it gets the neutral value rather than the wrong one.
        """
        own = self._home_venue.get(int(home_team_id))
        if venue_id is not None and own is not None and int(venue_id) != own:
            return ParkFactor(int(home_team_id), None, NEUTRAL, 0, 0)
        return self.factor(home_team_id, as_of)

    def all_factors(self, as_of: datetime) -> dict[int, float]:
        """Every team's park factor at one moment, cached — exposure needs them all."""
        key = _ns(as_of)
        cached = self._factor_cache.get(key)
        if cached is None:
            cached = {t: self.factor(t, as_of).value for t in self._teams}
            self._factor_cache[key] = cached
        return cached

    def exposure(self, team_id: int, played: pd.DataFrame, as_of: datetime) -> float:
        """The average park factor of the games ``team_id`` has already played.

        This is what makes the adjustment a deviation rather than a second
        helping. A team's scoring rate was accumulated in specific buildings; the
        rate is divided by their average effect before the current building is
        applied. ``played`` is the team's own as-of game frame, which the caller
        already holds.
        """
        if played.empty or not self._teams:
            return NEUTRAL
        factors = self.all_factors(as_of)
        own = factors.get(int(team_id), NEUTRAL)

        is_home = played["is_home"].to_numpy(dtype=bool)
        opponents = played["opponent_team_id"].to_numpy()
        values = np.fromiter(
            (own if h else factors.get(int(o), NEUTRAL)
             for h, o in zip(is_home, opponents, strict=False)),
            dtype=float,
            count=len(played),
        )
        mean = float(values.mean())
        # A mean of park factors cannot legitimately be zero or negative, and a
        # divide by one would be a silent catastrophe rather than a loud one.
        return mean if mean > 0 else NEUTRAL


def _ns(moment: datetime) -> int:
    return pd.Timestamp(moment).tz_convert("UTC").value


__all__ = [
    "K_PARK_GAMES",
    "MIN_GAMES_EACH_WAY",
    "NEUTRAL",
    "ParkFactor",
    "ParkFactors",
]
