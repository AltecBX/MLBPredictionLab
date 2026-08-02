"""Which relievers can actually pitch tonight, and how good the ones who can are.

The bullpen features already in the model are team-level totals: relief innings
over the last three days, a fatigue index, a thirty-day relief ERA. They answer
"how hard has this pen been worked" and they answer it well. What they cannot
answer is the question a manager is actually facing at first pitch — *which arms
are available* — and those are not the same question. A pen that threw four
innings yesterday spread over four pitchers is in a different state from one
that threw four innings out of its two best arms, and the team-level total is
identical in both cases.

This is the third hypothesis the starting-pitcher rejection left open, and the
one that has waited longest. It is a **different shape** from the three groups
already rejected: not another season aggregate of the same population, but a
per-pitcher constraint that the aggregate provably cannot express.

**The thresholds are conventions, not parameters.** Back-to-back days, three
straight days, a heavy outing yesterday — these are the rules bullpen usage
actually follows, and they are written down here as constants precisely so that
nobody is tempted to fit them. Tuning a rest threshold against the win outcome
it will be scored on is how a feature group manufactures its own significance,
and this repository has four rejections on record that were honest partly
because nothing like that happened.

**No closer.** `bullpen_availability.is_closer` stays null, and the deferred
feature `bp_closer_available_diff` stays `available=False`. Identifying a closer
needs saves or leverage index, and `player_game_stats` carries neither — it has
no save column, and no inning-level relief context. The available-quality
features below are built on what is measured. A guessed closer, inferred from
appearance counts, would be a placeholder wearing a fact's clothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

# --------------------------------------------------------------------------
# Rest conventions. Pre-registered; see the module docstring.
# --------------------------------------------------------------------------

#: A day's work heavy enough that a pitcher is rarely used again the next day.
HEAVY_OUTING_PITCHES = 45
#: Two days' combined work at which the same is true.
HEAVY_TWO_DAY_PITCHES = 60
#: A day's work heavy enough to make a back-to-back appearance a limited one.
MODERATE_OUTING_PITCHES = 30
#: Consecutive days pitched at which a reliever is treated as unavailable.
MAX_CONSECUTIVE_DAYS = 3

#: Relief appearances in the trailing window below which a pitcher is not
#: considered part of the bullpen at all. Keeps a position player's mop-up
#: inning and a September call-up's debut out of the availability count.
MIN_APPEARANCES_FOR_CORPS = 3
CORPS_WINDOW_DAYS = 30

#: Batters faced at which a reliever's own K−BB% is trusted halfway against the
#: league relief rate. Matches the constant the team-level bullpen features use.
K_QUALITY_BATTERS = 150

AVAILABLE = "AVAILABLE"
LIMITED = "LIMITED"
UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class RelieverStatus:
    """One reliever's workload and what it implies about tonight."""

    pitcher_id: int
    availability: str
    pitches_last_1d: int
    pitches_last_2d: int
    pitches_last_3d: int
    appearances_last_3d: int
    appearances_last_7d: int
    consecutive_days_pitched: int
    #: Shrunk K−BB%. None when the pitcher has faced too few batters to say.
    quality: float | None

    @property
    def can_pitch(self) -> bool:
        return self.availability != UNAVAILABLE


def _day(value) -> object:
    return pd.Timestamp(value).date()


def _classify(
    pitches_1d: int, pitches_2d: int, consecutive_days: int
) -> str:
    """Availability from rest alone. Deterministic and explainable."""
    if (
        consecutive_days >= MAX_CONSECUTIVE_DAYS
        or pitches_1d >= HEAVY_OUTING_PITCHES
        or pitches_2d >= HEAVY_TWO_DAY_PITCHES
    ):
        return UNAVAILABLE
    if consecutive_days >= 2 or pitches_1d >= MODERATE_OUTING_PITCHES:
        return LIMITED
    return AVAILABLE


def _consecutive_days(dates: set, reference) -> int:
    """Days pitched in an unbroken run ending yesterday.

    Counted back from the day before ``reference`` rather than from the day
    itself: a game today has not been played at the moment the prediction is
    made, and a run that ends two days ago is a rested pitcher, not a tired one.
    """
    run = 0
    day = reference - timedelta(days=1)
    while day in dates:
        run += 1
        day -= timedelta(days=1)
    return run


def bullpen_status(
    store,  # AsOfStore; untyped to avoid a circular import
    team_id: int,
    as_of: datetime,
    season_start: datetime,
    league_k_minus_bb: float | None,
) -> list[RelieverStatus]:
    """Every reliever in the team's recent corps, with tonight's availability.

    Read entirely through the as-of store, so an appearance in the game being
    predicted cannot reach this — which matters more here than almost anywhere
    else in the feature layer, because tonight's relief outing is exactly the
    fact that would make an availability feature look prescient.
    """
    season = store.team_pitcher_games_asof(team_id, as_of, season_start, relievers_only=True)
    if season.empty:
        return []

    window_start = as_of - timedelta(days=CORPS_WINDOW_DAYS)
    recent = season[season["game_date_utc"] >= window_start]
    if recent.empty:
        return []

    reference = _day(as_of)
    statuses: list[RelieverStatus] = []
    for pitcher_id, appearances in recent.groupby("player_id"):
        if len(appearances) < MIN_APPEARANCES_FOR_CORPS:
            continue
        days = {_day(d) for d in appearances["game_date_utc"]}

        def pitches_within(frame: pd.DataFrame, days_back: int) -> int:
            cut = as_of - timedelta(days=days_back)
            recent_rows = frame[frame["game_date_utc"] >= cut]
            if recent_rows.empty or "pitches_thrown" not in recent_rows:
                return 0
            return int(
                pd.to_numeric(recent_rows["pitches_thrown"], errors="coerce").fillna(0).sum()
            )

        pitches_1d = pitches_within(appearances, 1)
        pitches_2d = pitches_within(appearances, 2)
        pitches_3d = pitches_within(appearances, 3)
        consecutive = _consecutive_days(days, reference)

        season_rows = season[season["player_id"] == pitcher_id]
        statuses.append(
            RelieverStatus(
                pitcher_id=int(pitcher_id),
                availability=_classify(pitches_1d, pitches_2d, consecutive),
                pitches_last_1d=pitches_1d,
                pitches_last_2d=pitches_2d,
                pitches_last_3d=pitches_3d,
                appearances_last_3d=int(
                    (appearances["game_date_utc"] >= as_of - timedelta(days=3)).sum()
                ),
                appearances_last_7d=int(
                    (appearances["game_date_utc"] >= as_of - timedelta(days=7)).sum()
                ),
                consecutive_days_pitched=consecutive,
                quality=_quality(season_rows, league_k_minus_bb),
            )
        )
    return statuses


def _quality(rows: pd.DataFrame, league: float | None) -> float | None:
    """Season K−BB% for one reliever, shrunk toward the league relief rate."""
    if rows.empty or league is None:
        return None
    faced = float(pd.to_numeric(rows["batters_faced"], errors="coerce").fillna(0).sum())
    if faced <= 0:
        return None
    strikeouts = float(pd.to_numeric(rows["so_pitched"], errors="coerce").fillna(0).sum())
    walks = float(pd.to_numeric(rows["bb_allowed"], errors="coerce").fillna(0).sum())
    raw = (strikeouts - walks) / faced
    return (raw * faced + league * K_QUALITY_BATTERS) / (faced + K_QUALITY_BATTERS)


@dataclass(frozen=True, slots=True)
class BullpenAvailability:
    """The team-level summary the feature layer consumes."""

    corps_size: int
    available_count: int
    #: Mean shrunk K−BB% over the relievers who can pitch. None if none can be
    #: rated — which is an absence, and is emitted as one.
    available_quality: float | None
    #: Availability of the best-rated reliever: 1.0 available, 0.5 limited, 0.0
    #: unavailable. Not a "closer" — see the module docstring.
    best_reliever_available: float | None
    statuses: list[RelieverStatus]


def summarize(statuses: list[RelieverStatus]) -> BullpenAvailability | None:
    if not statuses:
        return None
    usable = [s for s in statuses if s.can_pitch]
    rated = [s for s in usable if s.quality is not None]
    ranked = sorted(
        (s for s in statuses if s.quality is not None),
        key=lambda s: (-s.quality, s.pitcher_id),
    )
    best = ranked[0] if ranked else None
    return BullpenAvailability(
        corps_size=len(statuses),
        available_count=sum(1 for s in statuses if s.availability == AVAILABLE),
        available_quality=(
            sum(s.quality for s in rated) / len(rated) if rated else None
        ),
        best_reliever_available=(
            None
            if best is None
            else {AVAILABLE: 1.0, LIMITED: 0.5, UNAVAILABLE: 0.0}[best.availability]
        ),
        statuses=statuses,
    )


__all__ = [
    "AVAILABLE",
    "CORPS_WINDOW_DAYS",
    "HEAVY_OUTING_PITCHES",
    "HEAVY_TWO_DAY_PITCHES",
    "LIMITED",
    "MAX_CONSECUTIVE_DAYS",
    "MIN_APPEARANCES_FOR_CORPS",
    "MODERATE_OUTING_PITCHES",
    "UNAVAILABLE",
    "BullpenAvailability",
    "RelieverStatus",
    "bullpen_status",
    "summarize",
]
