"""Who is missing, and how much of the team's record left with them.

Six feature groups have now been measured and rejected, and MODELING_PLAN.md
gives the same diagnosis every time: the group is a *decomposition* of team
strength. A team's season rate is a sufficient statistic for any rearrangement
of the players who produced it, so rearranging them adds nothing. The starting
pitcher split is the clearest case — `R_team = s·R_sp + (1−s)·R_pen` is an
identity, and swapping the named starter for the average one moved 83% of games
and flipped 17.8% of them without being any more accurate.

This group is a different operation. It does not redistribute the season rate,
it says the season rate is **stale**: it was accumulated by a roster that
included a player who is not playing tonight. That information is not inside the
team rate by construction — the team rate contains his contribution precisely
because he made it, and no rearrangement of it can express his absence.

**The recency window is measured, and it is measured against absence.** An
injuries row is an event, not an interval: an `IL` transaction is followed
eventually by an `ACTIVE` one, and roughly 1,700 stints across three seasons
never received their closing row. Taking the latest status at face value marks
1,095 players unavailable on a single midsummer day, which is obviously wrong.
So the flag was checked against what it claims — did the player actually appear
in his team's next seven days — over eight probe dates in 2024 and 2025,
restricted to batters with at least twenty plate appearances in the previous
thirty days:

| Days since the IL placement was knowable | players | played within 7 days |
|---|---|---|
| 0–14   | 161 | **22.4%** |
| 15–28  |  40 | 35.0% |
| 46–59  |   8 | 87.5% |
| 71+    | 146 | 91.1% |

A fresh placement more than halves the chance of appearing; one older than about
six weeks carries no information at all, because those are the stints whose
closing row is missing. `IL_RECENCY_DAYS` is where that signal has gone.

That constant is fitted, and it matters where. It was chosen against *did the
player appear*, which is not the prediction target and is not the quantity this
group is about to be scored on. Nothing about who won any game entered it. The
bullpen group's warning in MODELING_PLAN.md is about the opposite — a nuisance
constant tuned against the win outcome — and this is deliberately not that.

**Absence is weighted by what the absent player contributed**, so the feature is
a share of the team's own accumulated production rather than a headcount. A
bench player going down is not the same event as a cleanup hitter going down,
and a count would call them equal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from app.features.aggregates import WOBA_WEIGHTS

# How long an IL transaction still says something about tonight. Measured
# against subsequent appearances (see the module docstring), never against the
# win outcome. Beyond this the record is stale rather than informative: those
# are overwhelmingly stints whose closing ACTIVE row was never ingested.
IL_RECENCY_DAYS = 28

# The window the production share is taken over. Shorter than a season on
# purpose: the question is how much of the team's *current* form is missing, and
# a player lost in April has already left the June rate on his own.
PRODUCTION_WINDOW_DAYS = 45

# A team needs a real window before a share of it means anything.
MIN_TEAM_PA = 200
MIN_TEAM_BATTERS_FACED = 200


@dataclass(frozen=True, slots=True)
class AvailabilityLoss:
    """The share of a team's recent production that is currently unavailable."""

    #: Share of the window's weighted offensive production, in [0, 1].
    offense: float | None
    #: Share of the window's batters faced, in [0, 1].
    pitching: float | None
    #: How many players contributed each loss, for reporting.
    batters_out: int
    pitchers_out: int
    #: Sample sizes behind each share.
    team_pa: int
    team_batters_faced: int

    @property
    def is_measured(self) -> bool:
        return self.offense is not None or self.pitching is not None


NO_LOSS = AvailabilityLoss(None, None, 0, 0, 0, 0)


def unavailable_as_of(
    injuries: pd.DataFrame, knowledge_ns: np.ndarray, as_of: datetime
) -> set[int]:
    """Players whose latest knowable transaction is a recent IL placement.

    Only the window `[as_of − IL_RECENCY_DAYS, as_of)` is read, and that is not
    an optimisation — it is the definition. A player's state is the last row in
    that window: an IL placement followed by an `ACTIVE` return is superseded,
    because the return is in the window too. A placement with nothing after it
    stands. A placement older than the window is dropped whatever came after it,
    which is exactly what the absence measurement says to do with those rows.

    `injuries` must be sorted by `knowledge_time`, and `knowledge_ns` is that
    column as integer nanoseconds so the window can be found by binary search.

    Nothing here reads `effective_from`. A transaction is knowable when it is
    reported, which is what `knowledge_time` carries; `effective_from` is
    routinely backdated to the last game played, and using it would let a
    Tuesday prediction see a placement not announced until Thursday.
    """
    if injuries.empty:
        return set()

    lo = int(np.searchsorted(knowledge_ns, _epoch_ns(as_of - timedelta(days=IL_RECENCY_DAYS)),
                             side="left"))
    hi = int(np.searchsorted(knowledge_ns, _epoch_ns(as_of), side="right"))
    if hi <= lo:
        return set()

    window = injuries.iloc[lo:hi]
    players = window["player_id"].to_numpy()
    # Sorted ascending, so "not duplicated keeping the last" is the latest row
    # for each player inside the window.
    latest = ~pd.Index(players).duplicated(keep="last")
    is_il = (window["status"].to_numpy() == "IL") & latest
    return set(players[is_il].astype(int).tolist())


def _epoch_ns(moment: datetime) -> int:
    return int(pd.Timestamp(moment).value)


def _weighted_offense(frame: pd.DataFrame) -> pd.Series:
    """Linear-weights run value per batter row, using the repository's weights.

    The same coefficients the team-level `woba_proxy` already uses, so a
    player's share of the team is taken on the team's own scale rather than a
    second, differently-weighted one.
    """
    hits = frame["hits"].fillna(0)
    doubles = frame["doubles"].fillna(0)
    triples = frame["triples"].fillna(0)
    hr = frame["home_runs"].fillna(0)
    singles = (hits - doubles - triples - hr).clip(lower=0)
    return (
        WOBA_WEIGHTS["bb"] * (frame["bb"].fillna(0) - frame["ibb"].fillna(0)).clip(lower=0)
        + WOBA_WEIGHTS["hbp"] * frame["hbp"].fillna(0)
        + WOBA_WEIGHTS["1b"] * singles
        + WOBA_WEIGHTS["2b"] * doubles
        + WOBA_WEIGHTS["3b"] * triples
        + WOBA_WEIGHTS["hr"] * hr
    )


def availability_loss(
    store,  # AsOfStore; untyped to avoid a circular import
    team_id: int,
    as_of: datetime,
) -> AvailabilityLoss:
    """How much of this team's recent production is on the injured list.

    Both halves read through the as-of store, so a placement reported after the
    prediction cannot reach it and neither can a game that has not been played.

    Returns `None` for a half whose window is too small to divide, rather than
    zero. A team with no record is not a team that has lost nobody.
    """
    injuries = getattr(store, "injuries", None)
    if injuries is None or injuries.empty:
        return NO_LOSS

    out = store.unavailable_asof(as_of)
    window_start = as_of - timedelta(days=PRODUCTION_WINDOW_DAYS)

    batting = store.team_batter_games_asof(team_id, as_of, window_start)
    offense: float | None = None
    batters_out = 0
    team_pa = 0
    if not batting.empty:
        team_pa = int(batting["pa"].fillna(0).sum())
        if team_pa >= MIN_TEAM_PA:
            value = _weighted_offense(batting)
            total = float(value.sum())
            if total > 0:
                lost_rows = batting["player_id"].astype(int).isin(out)
                offense = float(value[lost_rows].sum()) / total
                batters_out = int(batting.loc[lost_rows, "player_id"].nunique())

    pitching_frame = store.team_pitcher_games_asof(team_id, as_of, window_start)
    pitching: float | None = None
    pitchers_out = 0
    team_bf = 0
    if not pitching_frame.empty and "batters_faced" in pitching_frame:
        faced = pitching_frame["batters_faced"].fillna(0)
        team_bf = int(faced.sum())
        if team_bf >= MIN_TEAM_BATTERS_FACED:
            lost_rows = pitching_frame["player_id"].astype(int).isin(out)
            pitching = float(faced[lost_rows].sum()) / float(team_bf)
            pitchers_out = int(pitching_frame.loc[lost_rows, "player_id"].nunique())

    return AvailabilityLoss(
        offense=offense,
        pitching=pitching,
        batters_out=batters_out,
        pitchers_out=pitchers_out,
        team_pa=team_pa,
        team_batters_faced=team_bf,
    )


__all__ = [
    "IL_RECENCY_DAYS",
    "MIN_TEAM_BATTERS_FACED",
    "MIN_TEAM_PA",
    "NO_LOSS",
    "PRODUCTION_WINDOW_DAYS",
    "AvailabilityLoss",
    "availability_loss",
    "unavailable_as_of",
]
