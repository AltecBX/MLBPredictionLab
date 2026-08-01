"""As-of aggregate computations over game logs.

Every aggregate here is a filtered sum over dated rows. No season-total endpoint
is ever consulted (LEAKAGE_PREVENTION.md §3, §10).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

# Linear weights for the box-score wOBA proxy. Standard published coefficients;
# labelled a proxy because it lacks reached-on-error and some rare events.
WOBA_WEIGHTS = {
    "bb": 0.690, "hbp": 0.720, "1b": 0.890, "2b": 1.270, "3b": 1.620, "hr": 2.100,
}

# Earth radius for great-circle travel distance.
EARTH_RADIUS_KM = 6371.0


def sum_column(frame: pd.DataFrame, column: str) -> float:
    """Total of a column, treating missing values as zero."""
    if frame.empty or column not in frame.columns:
        return 0.0
    return float(frame[column].fillna(0).sum())


_sum = sum_column


@dataclass(frozen=True, slots=True)
class TeamAggregate:
    games: int
    runs_per_game: float | None
    runs_allowed_per_game: float | None
    run_diff_per_game: float | None
    win_pct: float | None
    pythag_win_pct: float | None
    woba_proxy: float | None
    k_pct: float | None
    bb_pct: float | None
    errors_per_game: float | None
    def_efficiency: float | None
    runs_scored: float
    runs_allowed: float

    @property
    def empty(self) -> bool:
        return self.games == 0


def team_aggregate(frame: pd.DataFrame) -> TeamAggregate:
    n = int(len(frame))
    if n == 0:
        return TeamAggregate(0, None, None, None, None, None, None, None, None,
                             None, None, 0.0, 0.0)

    runs = _sum(frame, "runs")
    runs_allowed = _sum(frame, "runs_allowed")
    pa = _sum(frame, "plate_appearances")
    ab = _sum(frame, "at_bats")
    hits = _sum(frame, "hits")
    doubles = _sum(frame, "doubles")
    triples = _sum(frame, "triples")
    hr = _sum(frame, "home_runs")
    bb = _sum(frame, "walks")
    ibb = _sum(frame, "intentional_walks")
    hbp = _sum(frame, "hit_by_pitch")
    so = _sum(frame, "strikeouts")
    sf = _sum(frame, "sac_flies")
    singles = max(hits - doubles - triples - hr, 0.0)

    woba_den = ab + (bb - ibb) + sf + hbp
    woba = None
    if woba_den > 0:
        woba = (
            WOBA_WEIGHTS["bb"] * (bb - ibb)
            + WOBA_WEIGHTS["hbp"] * hbp
            + WOBA_WEIGHTS["1b"] * singles
            + WOBA_WEIGHTS["2b"] * doubles
            + WOBA_WEIGHTS["3b"] * triples
            + WOBA_WEIGHTS["hr"] * hr
        ) / woba_den

    pythag = None
    if runs > 0 or runs_allowed > 0:
        rs, ra = max(runs, 0.0), max(runs_allowed, 0.0)
        denom = rs**1.83 + ra**1.83
        pythag = (rs**1.83) / denom if denom > 0 else None

    # Defensive efficiency proxy: share of balls in play converted to outs.
    bf = _sum(frame, "batters_faced")
    so_p = _sum(frame, "strikeouts_pitched")
    bb_p = _sum(frame, "walks_allowed")
    hr_p = _sum(frame, "home_runs_allowed")
    hits_p = _sum(frame, "hits_allowed")
    bip = bf - so_p - bb_p - hr_p
    def_eff = None
    if bip > 0:
        def_eff = 1.0 - max(hits_p - hr_p, 0.0) / bip

    return TeamAggregate(
        games=n,
        runs_per_game=runs / n,
        runs_allowed_per_game=runs_allowed / n,
        run_diff_per_game=(runs - runs_allowed) / n,
        win_pct=float(frame["won"].mean()) if "won" in frame.columns else None,
        pythag_win_pct=pythag,
        woba_proxy=woba,
        k_pct=(so / pa) if pa > 0 else None,
        bb_pct=(bb / pa) if pa > 0 else None,
        errors_per_game=(_sum(frame, "errors") / n),
        def_efficiency=def_eff,
        runs_scored=runs,
        runs_allowed=runs_allowed,
    )


@dataclass(frozen=True, slots=True)
class PitchingAggregate:
    appearances: int
    starts: int
    outs: float
    innings: float
    era: float | None
    fip: float | None
    whip: float | None
    k_pct: float | None
    bb_pct: float | None
    k_minus_bb_pct: float | None
    hr_per_9: float | None
    gb_pct: float | None
    ip_per_appearance: float | None
    pitches_per_appearance: float | None
    batters_faced: float
    earned_runs: float
    home_runs: float
    walks: float
    strikeouts: float
    hbp: float

    @property
    def empty(self) -> bool:
        return self.appearances == 0


def pitching_aggregate(frame: pd.DataFrame, fip_constant: float | None = None) -> PitchingAggregate:
    n = int(len(frame))
    if n == 0:
        return PitchingAggregate(0, 0, 0.0, 0.0, None, None, None, None, None, None,
                                 None, None, None, None, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    outs = _sum(frame, "outs_pitched")
    innings = outs / 3.0
    er = _sum(frame, "earned_runs")
    hr = _sum(frame, "hr_allowed")
    bb = _sum(frame, "bb_allowed")
    hbp = _sum(frame, "hbp_allowed")
    so = _sum(frame, "so_pitched")
    hits = _sum(frame, "hits_allowed")
    bf = _sum(frame, "batters_faced")
    pitches = _sum(frame, "pitches_thrown")
    go = _sum(frame, "ground_outs_pitched")
    ao = _sum(frame, "air_outs_pitched")
    starts = int(frame["is_starter"].fillna(False).astype(bool).sum()) if "is_starter" in frame else 0

    fip = None
    if innings > 0 and fip_constant is not None:
        fip = (13 * hr + 3 * (bb + hbp) - 2 * so) / innings + fip_constant

    return PitchingAggregate(
        appearances=n,
        starts=starts,
        outs=outs,
        innings=innings,
        era=(9.0 * er / innings) if innings > 0 else None,
        fip=fip,
        whip=((bb + hits) / innings) if innings > 0 else None,
        k_pct=(so / bf) if bf > 0 else None,
        bb_pct=(bb / bf) if bf > 0 else None,
        k_minus_bb_pct=((so - bb) / bf) if bf > 0 else None,
        hr_per_9=(9.0 * hr / innings) if innings > 0 else None,
        gb_pct=(go / (go + ao)) if (go + ao) > 0 else None,
        ip_per_appearance=(innings / n) if n > 0 else None,
        pitches_per_appearance=(pitches / n) if n > 0 else None,
        batters_faced=bf,
        earned_runs=er,
        home_runs=hr,
        walks=bb,
        strikeouts=so,
        hbp=hbp,
    )


def fip_constant(league: pd.DataFrame) -> float | None:
    """League FIP constant: league ERA minus the raw FIP numerator rate."""
    if league.empty:
        return None
    outs = _sum(league, "outs_pitched")
    innings = outs / 3.0
    if innings <= 0:
        return None
    er = _sum(league, "earned_runs")
    hr = _sum(league, "hr_allowed")
    bb = _sum(league, "bb_allowed")
    hbp = _sum(league, "hbp_allowed")
    so = _sum(league, "so_pitched")
    league_era = 9.0 * er / innings
    raw = (13 * hr + 3 * (bb + hbp) - 2 * so) / innings
    return league_era - raw


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def utc_offset_hours(timezone: str | None, when: datetime) -> float | None:
    if not timezone:
        return None
    try:
        from zoneinfo import ZoneInfo

        offset = when.astimezone(ZoneInfo(timezone)).utcoffset()
    except Exception:
        return None
    return offset.total_seconds() / 3600.0 if offset is not None else None
