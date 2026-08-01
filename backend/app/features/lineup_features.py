"""Projected lineups, and what a starter's arsenal is worth against one.

LEAKAGE_PREVENTION.md §15 established that no *posted* lineup is knowable before
first pitch: all 188,604 ingested lineups come from completed box scores and
carry `knowledge_time = first pitch + 3h30m`. That rules out confirmed-lineup
features at the T−3h snapshot, and nothing here tries to smuggle one in.

What is knowable is who has been starting. A lineup projected from a team's own
recent starts is built entirely from completed games, carries their knowledge
times, and is exactly what a reader could work out for themselves at the same
moment. That is the substrate here.

The two ideas being tested, in the order MODELING_PLAN.md ranked them after the
starting-pitcher Statcast group was rejected:

1. **The matchup, not the pitcher.** A starter's arsenal against *this* lineup's
   weaknesses is a different quantity from his arsenal in general — and the
   general version is the one that was already measured and found wanting.
2. **The batters, not the pitcher.** A starter is a minority of a team's run
   prevention and none of its scoring; nine lineup slots is a larger surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from app.features.batter_agg import PITCH_FAMILIES
from app.features.shrinkage import FeatureValue, safe_div

# Expected plate appearances by batting-order slot, measured over 16,314 starts
# per slot in nine-inning regular-season games (FEATURE_DICTIONARY.md). These are
# the PA of the player who *starts* in the slot, not of the slot — a starter
# lifted in the seventh contributes his own three trips and no more — which is
# exactly the weight a projected lineup wants, because it already prices in the
# chance of being lifted.
EXPECTED_PA: dict[int, float] = {
    1: 4.448, 2: 4.356, 3: 4.252, 4: 4.157, 5: 4.022,
    6: 3.889, 7: 3.752, 8: 3.595, 9: 3.442,
}

# How far back a projection looks. Short enough that a call-up who has taken over
# a slot is reflected, long enough that one rest day does not drop a regular.
PROJECTION_DAYS = 21
MIN_STARTS_TO_PROJECT = 2

# Stabilization. A batter's season is shrunk toward the league; his record
# against one pitch family is shrunk toward his own overall record first, then
# that toward the league — the hierarchy is what keeps 40 plate appearances
# against sliders from reading as a discovery.
K_BATTER_XWOBA = 220
K_BATTER_FAMILY = 130
K_BATTER_WHIFF = 180
K_FAMILY_WHIFF = 120
MIN_PA = 80


@dataclass(frozen=True, slots=True)
class LineupSlot:
    player_id: int
    slot: int
    weight: float


@dataclass(frozen=True, slots=True)
class ProjectedLineup:
    slots: tuple[LineupSlot, ...]
    #: Share of the projected nine who started the team's most recent game. A
    #: projection is a guess, and this is how good a guess it is.
    continuity: float | None
    #: Games the projection was built from.
    games: int

    @property
    def is_empty(self) -> bool:
        return not self.slots


EMPTY_LINEUP = ProjectedLineup(slots=(), continuity=None, games=0)


def project_lineup(orders: pd.DataFrame, as_of: datetime) -> ProjectedLineup:
    """The nine most likely starters, in their most likely order.

    ``orders`` must already be cut at ``as_of`` by the caller. Selection is by
    recent starts; the order is by each player's median recent slot, which is
    stabler than his last one — a leadoff hitter dropped to sixth for a night is
    still the leadoff hitter.
    """
    if orders.empty:
        return EMPTY_LINEUP

    window = orders[orders["game_date_utc"] >= as_of - timedelta(days=PROJECTION_DAYS)]
    if window.empty:
        return EMPTY_LINEUP

    grouped = window.groupby("player_id").agg(
        starts=("game_id", "size"), median_slot=("batting_order_slot", "median")
    )
    grouped = grouped[grouped["starts"] >= MIN_STARTS_TO_PROJECT]
    if grouped.empty:
        # Early in a window nobody clears the floor; fall back to whoever played,
        # rather than reporting no lineup at all.
        grouped = window.groupby("player_id").agg(
            starts=("game_id", "size"), median_slot=("batting_order_slot", "median")
        )

    # Most frequent starters first; ties broken by the higher (earlier) slot, so
    # a regular beats a platoon partner with the same count.
    chosen = grouped.sort_values(
        ["starts", "median_slot"], ascending=[False, True]
    ).head(9)
    if chosen.empty:
        return EMPTY_LINEUP

    ordered = chosen.sort_values("median_slot")
    slots = tuple(
        LineupSlot(
            player_id=int(player_id),
            slot=i + 1,
            weight=EXPECTED_PA[i + 1],
        )
        for i, player_id in enumerate(ordered.index)
    )

    last_game = window["game_id"].iloc[-1] if len(window) else None
    last_nine = set(window[window["game_id"] == last_game]["player_id"]) if last_game else set()
    continuity = (
        len({s.player_id for s in slots} & last_nine) / len(slots) if last_nine else None
    )

    return ProjectedLineup(
        slots=slots,
        continuity=continuity,
        games=int(window["game_id"].nunique()),
    )


@dataclass(frozen=True, slots=True)
class LeagueBatting:
    """League rates over the same as-of window, used as shrinkage priors."""

    xwoba: float | None = None
    whiff_pct: float | None = None
    family_xwoba: tuple[tuple[str, float | None], ...] = ()
    family_whiff: tuple[tuple[str, float | None], ...] = ()

    def xwoba_for(self, family: str) -> float | None:
        return dict(self.family_xwoba).get(family)

    def whiff_for(self, family: str) -> float | None:
        return dict(self.family_whiff).get(family)


def league_batting(frame: pd.DataFrame) -> LeagueBatting:
    if frame.empty:
        return LeagueBatting()

    def col(name: str) -> float:
        return float(frame[name].sum()) if name in frame.columns else 0.0

    return LeagueBatting(
        xwoba=safe_div(col("xwoba_num"), col("woba_denom")),
        whiff_pct=safe_div(col("whiffs"), col("swings")),
        family_xwoba=tuple(
            (key, safe_div(col(f"{key}_xwoba_num"), col(f"{key}_pa")))
            for key in PITCH_FAMILIES
        ),
        family_whiff=tuple(
            (key, safe_div(col(f"{key}_whiffs"), col(f"{key}_swings")))
            for key in PITCH_FAMILIES
        ),
    )


def _shrink(events: float, denom: float, prior: float | None, k: float) -> float | None:
    if prior is None:
        return safe_div(events, denom)
    return (events + prior * k) / (denom + k)


def _shrunk_edge(
    part: float | None, whole: float | None, denom: float, k: float
) -> float:
    """A deviation from a player's own rate, regressed toward no deviation.

    Both sides are raw. The null hypothesis for a matchup is that there is no
    matchup, so the estimate shrinks to exactly zero as the sample vanishes, and
    the result depends only on the size of the deviation and the evidence for it
    — never on the level the player sits at.
    """
    if part is None or whole is None or denom <= 0:
        return 0.0
    return denom * (part - whole) / (denom + k)


@dataclass(frozen=True, slots=True)
class BatterProfile:
    """One batter's shrunk rates, as-of.

    ``family_xwoba_edge`` and ``family_whiff_edge`` are **differentials**, not
    levels: how far this hitter deviates from his own overall rate against each
    pitch family, regressed toward no deviation on a small sample.

    Storing the edge directly rather than deriving it from two levels is not a
    convenience. Subtracting a shrunk overall rate from a raw family rate leaves
    the difference contaminated by how far the hitter sits from league average —
    the shrinkage moves the anchor but not the family number — so an identical
    relative weakness reads bigger for a poor hitter than for a good one. A test
    asserts the two are now the same.
    """

    xwoba: float | None
    xwoba_vs_hand: float | None
    whiff_pct: float | None
    family_xwoba: dict[str, float | None]
    family_whiff: dict[str, float | None]
    family_xwoba_edge: dict[str, float]
    family_whiff_edge: dict[str, float]
    plate_appearances: float


def batter_profile(
    rows: pd.DataFrame, hand: str | None, league: LeagueBatting
) -> BatterProfile:
    """Collapse a batter's as-of slice into shrunk rates.

    Family rates are shrunk toward **this batter's own overall rate**, not toward
    the league. That is the hierarchy that matters: the question a matchup asks
    is whether this hitter is unusually bad against sliders *for him*, and
    shrinking toward the league would answer a different question and answer it
    with the lineup's overall quality, which is already its own feature.
    """
    if rows.empty:
        return BatterProfile(None, None, None, {}, {}, {}, {}, 0.0)

    def total(name: str) -> float:
        return float(rows[name].sum()) if name in rows.columns else 0.0

    pa = total("woba_denom")
    xwoba = _shrink(total("xwoba_num"), pa, league.xwoba, K_BATTER_XWOBA)
    whiff = _shrink(total("whiffs"), total("swings"), league.whiff_pct, K_BATTER_WHIFF)

    hand_key = "l" if hand == "L" else "r" if hand == "R" else None
    xwoba_vs_hand = (
        _shrink(
            total(f"xwoba_num_vs_{hand_key}"),
            total(f"pa_vs_{hand_key}"),
            xwoba,
            K_BATTER_XWOBA / 2,
        )
        if hand_key
        else None
    )

    raw_xwoba = safe_div(total("xwoba_num"), pa)
    raw_whiff = safe_div(total("whiffs"), total("swings"))

    family_xwoba: dict[str, float | None] = {}
    family_whiff: dict[str, float | None] = {}
    family_xwoba_edge: dict[str, float] = {}
    family_whiff_edge: dict[str, float] = {}
    for key in PITCH_FAMILIES:
        family_xwoba[key] = _shrink(
            total(f"{key}_xwoba_num"), total(f"{key}_pa"), xwoba, K_BATTER_FAMILY
        )
        family_whiff[key] = _shrink(
            total(f"{key}_whiffs"), total(f"{key}_swings"), whiff, K_FAMILY_WHIFF
        )
        family_xwoba_edge[key] = _shrunk_edge(
            safe_div(total(f"{key}_xwoba_num"), total(f"{key}_pa")),
            raw_xwoba,
            total(f"{key}_pa"),
            K_BATTER_FAMILY,
        )
        family_whiff_edge[key] = _shrunk_edge(
            safe_div(total(f"{key}_whiffs"), total(f"{key}_swings")),
            raw_whiff,
            total(f"{key}_swings"),
            K_FAMILY_WHIFF,
        )

    return BatterProfile(
        xwoba, xwoba_vs_hand, whiff, family_xwoba, family_whiff,
        family_xwoba_edge, family_whiff_edge, pa,
    )


def arsenal_usage(rows: pd.DataFrame) -> dict[str, float] | None:
    """A pitcher's pitch mix as shares that sum to one."""
    if rows.empty:
        return None
    counts = {
        key: float(rows[f"{key}_pitches"].sum())
        for key in PITCH_FAMILIES
        if f"{key}_pitches" in rows.columns
    }
    total = sum(counts.values())
    if total <= 0:
        return None
    return {key: value / total for key, value in counts.items()}


FEATURE_KEYS = (
    "lineup_xwoba_weighted",
    "lineup_xwoba_vs_hand",
    "lineup_whiff_pct_weighted",
    "lineup_continuity",
    "arsenal_xwoba_edge",
    "arsenal_whiff_edge",
)


def lineup_values(
    lineup: ProjectedLineup,
    profiles: dict[int, BatterProfile],
    opposing_usage: dict[str, float] | None,
    league: LeagueBatting,
) -> dict[str, FeatureValue]:
    """Lineup quality and arsenal matchup for one side.

    ``opposing_usage`` is the pitch mix of the starter this lineup will face.
    """
    if lineup.is_empty:
        return {k: FeatureValue.missing("no lineup could be projected") for k in FEATURE_KEYS}

    weights, xwobas, vs_hand, whiffs, pa = [], [], [], [], 0.0
    for slot in lineup.slots:
        profile = profiles.get(slot.player_id)
        if profile is None or profile.xwoba is None:
            continue
        weights.append(slot.weight)
        xwobas.append(profile.xwoba)
        vs_hand.append(
            profile.xwoba_vs_hand if profile.xwoba_vs_hand is not None else profile.xwoba
        )
        whiffs.append(profile.whiff_pct if profile.whiff_pct is not None else np.nan)
        pa += profile.plate_appearances

    if not weights:
        return {
            k: FeatureValue.missing("no projected starter has Statcast on record")
            for k in FEATURE_KEYS
        }

    w = np.asarray(weights, dtype=float)
    covered = len(weights)
    estimated = covered < len(lineup.slots) or pa < MIN_PA * covered

    out: dict[str, FeatureValue] = {
        "lineup_xwoba_weighted": FeatureValue(
            float(np.average(xwobas, weights=w)), int(pa), estimated
        ),
        "lineup_xwoba_vs_hand": FeatureValue(
            float(np.average(vs_hand, weights=w)), int(pa), estimated
        ),
        "lineup_continuity": (
            FeatureValue(lineup.continuity, lineup.games, lineup.games < 5)
            if lineup.continuity is not None
            else FeatureValue.missing("no prior game to compare the projection with")
        ),
    }

    whiff_array = np.asarray(whiffs, dtype=float)
    finite = np.isfinite(whiff_array)
    out["lineup_whiff_pct_weighted"] = (
        FeatureValue(
            float(np.average(whiff_array[finite], weights=w[finite])), int(pa), estimated
        )
        if finite.any()
        else FeatureValue.missing("no swing data for the projected lineup")
    )

    out.update(_arsenal_edges(lineup, profiles, opposing_usage, w, covered, pa, estimated))
    return out


def _arsenal_edges(
    lineup: ProjectedLineup,
    profiles: dict[int, BatterProfile],
    usage: dict[str, float] | None,
    weights: np.ndarray,
    covered: int,
    pa: float,
    estimated: bool,
) -> dict[str, FeatureValue]:
    """How this lineup fares against *this* mix, net of how it fares generally.

    Both edges are differences from the lineup's own overall rate, deliberately.
    A raw "lineup xwOBA against his mix" is mostly a restatement of lineup
    quality, which is already the feature next to it, and two collinear features
    is one feature and one source of variance. Expressed as an edge, the quantity
    is what the mix is worth *given* the lineup — which is the thing nothing else
    in the model measures.
    """
    if usage is None:
        missing = FeatureValue.missing("no arsenal on record for the opposing starter")
        return {"arsenal_xwoba_edge": missing, "arsenal_whiff_edge": missing}

    xwoba_edges, whiff_edges, used = [], [], []
    for i, slot in enumerate(lineup.slots):
        profile = profiles.get(slot.player_id)
        if profile is None or profile.xwoba is None:
            continue
        xwoba_edges.append(
            sum(
                share * profile.family_xwoba_edge.get(key, 0.0)
                for key, share in usage.items()
            )
        )
        if profile.whiff_pct is not None:
            whiff_edges.append(
                sum(
                    share * profile.family_whiff_edge.get(key, 0.0)
                    for key, share in usage.items()
                )
            )
        else:
            whiff_edges.append(np.nan)
        used.append(i)

    if not xwoba_edges:
        missing = FeatureValue.missing("no projected starter has Statcast on record")
        return {"arsenal_xwoba_edge": missing, "arsenal_whiff_edge": missing}

    w = weights[: len(xwoba_edges)] if len(weights) >= len(xwoba_edges) else weights
    whiff_array = np.asarray(whiff_edges, dtype=float)
    finite = np.isfinite(whiff_array)

    return {
        "arsenal_xwoba_edge": FeatureValue(
            float(np.average(xwoba_edges, weights=w)), int(pa), estimated
        ),
        "arsenal_whiff_edge": (
            FeatureValue(
                float(np.average(whiff_array[finite], weights=w[finite])), int(pa), estimated
            )
            if finite.any()
            else FeatureValue.missing("no swing data for the projected lineup")
        ),
    }


__all__ = [
    "EXPECTED_PA",
    "FEATURE_KEYS",
    "MIN_STARTS_TO_PROJECT",
    "PROJECTION_DAYS",
    "BatterProfile",
    "LeagueBatting",
    "LineupSlot",
    "ProjectedLineup",
    "arsenal_usage",
    "batter_profile",
    "league_batting",
    "lineup_values",
    "project_lineup",
]
