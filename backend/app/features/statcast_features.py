"""Starting-pitcher features derived from Statcast.

What these add that the box score cannot: contact quality allowed and the
underlying stuff behind it. A box score records that a ball was caught; Statcast
records that it left the bat at 104 mph. Over a start those diverge often enough
that ERA and FIP are noisy estimates of how a pitcher actually threw.

Three rules hold throughout:

* **Every window is cut at ``as_of``.** The aggregates are per-game and carry the
  game's own ``knowledge_time``, so a start that finished after the prediction
  moment is not in the slice at all.
* **A rate with no denominator is missing, not zero.** A pitcher with no balls
  in play on record has an unknown barrel rate. Reporting 0.0 would say he has
  never allowed hard contact, which is the opposite of what is known.
* **Nothing is compared to a constant.** The league baselines used as shrinkage
  priors are computed from the same as-of slice, so an April prior is April's
  league, not the finished season's.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

from app.features.shrinkage import FeatureValue, safe_div, shrink, shrink_mean

# Stabilization constants. Contact-quality rates settle faster than outcome
# rates because the measurement is of the batted ball itself rather than of
# where eight fielders happened to be, so `k` is smaller than the wOBA-against
# equivalents in FEATURE_DICTIONARY.md §1.
K_XWOBA = 250          # plate appearances
K_BARREL = 80          # batted balls
K_HARD_HIT = 50        # batted balls
K_EXIT_VELOCITY = 40   # batted balls
K_WHIFF = 150          # swings
K_CHASE = 200          # pitches out of the zone
K_CSW = 250            # pitches
K_VELOCITY = 150       # four-seam fastballs

# Below these, a value is flagged estimated and its sample travels with it.
MIN_PA = 100
MIN_BIP = 40
MIN_PITCHES = 300
MIN_FASTBALLS = 100

# A velocity trend needs a recent window with enough fastballs in it to mean
# anything. One start's worth is not a trend.
VELOCITY_TREND_DAYS = 30
MIN_TREND_FASTBALLS = 60

# Windows the spec asks for, shortest first.
WINDOW_DAYS = (14, 30, 60)


@dataclass(frozen=True, slots=True)
class StatcastRates:
    """Rates over one as-of window, with the denominators that produced them."""

    plate_appearances: float = 0.0
    balls_in_play: float = 0.0
    pitches: float = 0.0
    swings: float = 0.0
    out_of_zone: float = 0.0
    fastballs: float = 0.0

    xwoba: float | None = None
    barrel_pct: float | None = None
    hard_hit_pct: float | None = None
    avg_exit_velocity: float | None = None
    whiff_pct: float | None = None
    chase_pct: float | None = None
    csw_pct: float | None = None
    fastball_velocity: float | None = None

    @property
    def is_empty(self) -> bool:
        return self.pitches <= 0


def _sum(frame: pd.DataFrame, column: str) -> float:
    if frame.empty or column not in frame.columns:
        return 0.0
    total = frame[column].sum()
    return 0.0 if pd.isna(total) else float(total)


def summarize(frame: pd.DataFrame) -> StatcastRates:
    """Collapse an as-of slice of per-game aggregates into rates.

    Every rate is a ratio of two sums over whole games, so a window is exactly
    the games it contains — no per-game rate is averaged into another, which
    would silently weight a two-inning relief outing like a complete game.
    """
    if frame.empty:
        return StatcastRates()

    pa = _sum(frame, "woba_denom")
    bip = _sum(frame, "balls_in_play")
    pitches = _sum(frame, "pitches")
    swings = _sum(frame, "swings")
    out_of_zone = _sum(frame, "out_of_zone")
    fastballs = _sum(frame, "ff_count")
    ev_count = _sum(frame, "ev_count")

    return StatcastRates(
        plate_appearances=pa,
        balls_in_play=bip,
        pitches=pitches,
        swings=swings,
        out_of_zone=out_of_zone,
        fastballs=fastballs,
        xwoba=safe_div(_sum(frame, "xwoba_num"), pa),
        barrel_pct=safe_div(_sum(frame, "barrels"), bip),
        hard_hit_pct=safe_div(_sum(frame, "hard_hit"), bip),
        avg_exit_velocity=safe_div(_sum(frame, "ev_sum"), ev_count),
        whiff_pct=safe_div(_sum(frame, "whiffs"), swings),
        chase_pct=safe_div(_sum(frame, "chases"), out_of_zone),
        csw_pct=safe_div(
            _sum(frame, "called_strikes") + _sum(frame, "whiffs"), pitches
        ),
        fastball_velocity=safe_div(_sum(frame, "ff_speed_sum"), fastballs),
    )


@dataclass(frozen=True, slots=True)
class StatcastBaseline:
    """League rates over the same as-of window, used as shrinkage priors."""

    xwoba: float | None = None
    barrel_pct: float | None = None
    hard_hit_pct: float | None = None
    avg_exit_velocity: float | None = None
    whiff_pct: float | None = None
    chase_pct: float | None = None
    csw_pct: float | None = None
    fastball_velocity: float | None = None

    @classmethod
    def from_rates(cls, rates: StatcastRates) -> StatcastBaseline:
        return cls(
            xwoba=rates.xwoba,
            barrel_pct=rates.barrel_pct,
            hard_hit_pct=rates.hard_hit_pct,
            avg_exit_velocity=rates.avg_exit_velocity,
            whiff_pct=rates.whiff_pct,
            chase_pct=rates.chase_pct,
            csw_pct=rates.csw_pct,
            fastball_velocity=rates.fastball_velocity,
        )


# The keys this module emits, per side. The builder differences them.
FEATURE_KEYS = (
    "sc_sp_xwoba_allowed",
    "sc_sp_barrel_pct_allowed",
    "sc_sp_hard_hit_pct_allowed",
    "sc_sp_avg_exit_velocity_allowed",
    "sc_sp_whiff_pct",
    "sc_sp_chase_pct",
    "sc_sp_csw_pct",
    "sc_sp_fastball_velocity",
    "sc_sp_velocity_delta_30d",
)


def _missing(detail: str) -> dict[str, FeatureValue]:
    return {key: FeatureValue.missing(detail) for key in FEATURE_KEYS}


def starter_values(
    season_slice: pd.DataFrame,
    prior_season_slice: pd.DataFrame,
    recent_slice: pd.DataFrame,
    baseline: StatcastBaseline,
) -> dict[str, FeatureValue]:
    """Statcast features for one starting pitcher.

    ``season_slice`` is this season to date, ``prior_season_slice`` is last
    season, and ``recent_slice`` is the trailing 30 days. All three are already
    cut at ``as_of`` by the caller.

    The prior season is used the way FEATURE_DICTIONARY.md §1 rule 2 requires:
    as the *prior* that this season's rate is shrunk toward, itself first shrunk
    toward the league. In April that makes a pitcher mostly last year's pitcher;
    by August it makes him this year's. A pitcher with no prior season falls
    back to the league baseline directly, and is flagged estimated for as long
    as the sample stays small.
    """
    season = summarize(season_slice)
    if season.is_empty:
        return _missing("no Statcast on record for this pitcher")

    prior = summarize(prior_season_slice)

    def prior_for(attribute: str, denominator: float, k: float) -> float | None:
        """Last season regressed to league, or league alone when there is none."""
        league = getattr(baseline, attribute)
        observed = getattr(prior, attribute)
        if observed is None:
            return league
        if league is None:
            return observed
        return (observed * denominator + league * k) / (denominator + k)

    out: dict[str, FeatureValue] = {}

    out["sc_sp_xwoba_allowed"] = shrink(
        (season.xwoba or 0.0) * season.plate_appearances,
        season.plate_appearances,
        prior_for("xwoba", prior.plate_appearances, K_XWOBA),
        K_XWOBA,
        min_sample=MIN_PA,
    )
    out["sc_sp_barrel_pct_allowed"] = shrink(
        (season.barrel_pct or 0.0) * season.balls_in_play,
        season.balls_in_play,
        prior_for("barrel_pct", prior.balls_in_play, K_BARREL),
        K_BARREL,
        min_sample=MIN_BIP,
    )
    out["sc_sp_hard_hit_pct_allowed"] = shrink(
        (season.hard_hit_pct or 0.0) * season.balls_in_play,
        season.balls_in_play,
        prior_for("hard_hit_pct", prior.balls_in_play, K_HARD_HIT),
        K_HARD_HIT,
        min_sample=MIN_BIP,
    )
    out["sc_sp_avg_exit_velocity_allowed"] = shrink_mean(
        season.avg_exit_velocity,
        season.balls_in_play,
        prior_for("avg_exit_velocity", prior.balls_in_play, K_EXIT_VELOCITY),
        K_EXIT_VELOCITY,
        min_sample=MIN_BIP,
    )
    out["sc_sp_whiff_pct"] = shrink(
        (season.whiff_pct or 0.0) * season.swings,
        season.swings,
        prior_for("whiff_pct", prior.swings, K_WHIFF),
        K_WHIFF,
        min_sample=MIN_PITCHES / 2,
    )
    out["sc_sp_chase_pct"] = shrink(
        (season.chase_pct or 0.0) * season.out_of_zone,
        season.out_of_zone,
        prior_for("chase_pct", prior.out_of_zone, K_CHASE),
        K_CHASE,
        min_sample=MIN_PITCHES / 2,
    )
    out["sc_sp_csw_pct"] = shrink(
        (season.csw_pct or 0.0) * season.pitches,
        season.pitches,
        prior_for("csw_pct", prior.pitches, K_CSW),
        K_CSW,
        min_sample=MIN_PITCHES,
    )
    out["sc_sp_fastball_velocity"] = shrink_mean(
        season.fastball_velocity,
        season.fastballs,
        prior_for("fastball_velocity", prior.fastballs, K_VELOCITY),
        K_VELOCITY,
        min_sample=MIN_FASTBALLS,
    )
    out["sc_sp_velocity_delta_30d"] = _velocity_trend(recent_slice, season)
    return out


def _velocity_trend(recent_slice: pd.DataFrame, season: StatcastRates) -> FeatureValue:
    """Recent fastball velocity minus the season's own, in mph.

    Deliberately a *delta from the pitcher's own baseline* rather than a level
    (FEATURE_DICTIONARY.md §1 rule 3): a 92 mph fastball says one thing about a
    pitcher who has always thrown 92 and something entirely different about one
    who threw 95 in April. Unshrunk, because the shrinkage lives in the two
    quantities being differenced, and because the whole point of the feature is
    to move when something has changed.
    """
    recent = summarize(recent_slice)
    if recent.fastball_velocity is None or season.fastball_velocity is None:
        return FeatureValue.missing("no fastball velocity on record")
    if recent.fastballs < MIN_TREND_FASTBALLS:
        return FeatureValue(
            recent.fastball_velocity - season.fastball_velocity,
            int(recent.fastballs),
            True,
            detail=f"only {int(recent.fastballs)} recent fastballs",
        )
    return FeatureValue(
        recent.fastball_velocity - season.fastball_velocity,
        int(recent.fastballs),
        False,
    )


def recent_window_start(as_of: datetime) -> datetime:
    return as_of - timedelta(days=VELOCITY_TREND_DAYS)


__all__ = [
    "FEATURE_KEYS",
    "StatcastBaseline",
    "StatcastRates",
    "recent_window_start",
    "starter_values",
    "summarize",
]
