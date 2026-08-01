"""Prediction explanations.

Model contributions are converted into probability points and then into
baseball language. The same vocabulary is used regardless of which model
produced the number, so adding the GBDT in Phase 2 does not change the UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from app.features.builder import FeatureVector
from app.features.registry import CATEGORY_LABELS, spec
from app.modeling.logistic import LogisticWinModel

TOP_N = 5

# Formatting per unit so a reader sees "+0.42 runs/g", not "0.4213".
UNIT_FORMAT: dict[str, tuple[str, float, str]] = {
    "pct": ("{:+.1%}", 1.0, ""),
    "%": ("{:+.1f}", 100.0, " pts"),
    "runs/g": ("{:+.2f}", 1.0, " runs/g"),
    "ERA": ("{:+.2f}", 1.0, " ERA"),
    "FIP": ("{:+.2f}", 1.0, " FIP"),
    "WHIP": ("{:+.2f}", 1.0, " WHIP"),
    "HR/9": ("{:+.2f}", 1.0, " HR/9"),
    "IP": ("{:+.2f}", 1.0, " IP"),
    "days": ("{:+.1f}", 1.0, " days"),
    "hours": ("{:+.1f}", 1.0, " h"),
    "km": ("{:+.0f}", 1.0, " km"),
    "pts": ("{:+.0f}", 1.0, " Elo"),
    "games": ("{:+.0f}", 1.0, " games"),
    "starts": ("{:+.0f}", 1.0, " starts"),
    "E/g": ("{:+.2f}", 1.0, " E/g"),
    "index": ("{:+.2f}", 1.0, ""),
    "wOBA": ("{:+.3f}", 1.0, " wOBA"),
    "flag": ("{:+.0f}", 1.0, ""),
}


@dataclass(slots=True)
class Contribution:
    rank: int
    feature_key: str
    display_name: str
    category: str
    category_label: str
    favors: str            # 'H' or 'A'
    contribution_pp: float
    feature_value: float | None
    feature_display: str | None
    sample_size: int | None
    is_estimated: bool
    narrative: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "feature_key": self.feature_key,
            "display_name": self.display_name,
            "category": self.category,
            "category_label": self.category_label,
            "favors": self.favors,
            "contribution_pp": round(self.contribution_pp, 3),
            "feature_value": self.feature_value,
            "feature_display": self.feature_display,
            "sample_size": self.sample_size,
            "is_estimated": self.is_estimated,
            "narrative": self.narrative,
        }


def _format_value(key: str, value: float | None) -> str | None:
    """Format a value in its declared unit."""
    if value is None:
        return None
    unit = spec(key).unit
    fmt, scale, suffix = UNIT_FORMAT.get(unit, ("{:+.3f}", 1.0, ""))
    try:
        return fmt.format(value * scale) + suffix
    except (ValueError, TypeError):
        return str(value)


def _narrative(
    key: str, favors_home: bool, home_name: str, away_name: str, value: float | None
) -> str:
    """Translate a contribution into a sentence about the side that benefits.

    Difference features report the size of the edge as a magnitude, because the
    raw signed value is negative for every inverted feature and reading "+6.2
    points, −0.41 ERA" as an advantage takes a second look. Absolute features
    (a dome, an elevation, a starter being announced) get a factual sentence
    rather than being phrased as though the ballpark were choosing a side.
    """
    meta = spec(key)
    team = home_name if favors_home else away_name

    if meta.is_absolute:
        return f"{team} {meta.narrative}." if meta.narrative else (
            f"{meta.display_name} contributes to {team} in the fitted model."
        )

    if meta.narrative:
        edge = _format_value(key, abs(value)) if value is not None else None
        return f"{team} {meta.narrative}" + (f" ({edge} edge)." if edge else ".")

    edge = _format_value(key, abs(value)) if value is not None else None
    return (
        f"{meta.display_name} favors {team}" + (f" by {edge}." if edge else ".")
    )


def build_contributions(
    model: LogisticWinModel,
    frame: pd.DataFrame,
    vector: FeatureVector,
    home_name: str,
    away_name: str,
) -> list[Contribution]:
    raw = model.contributions(frame)[0]
    items: list[Contribution] = []

    for key, pp in raw.items():
        if vector.features.get(key) is None:
            continue
        if abs(pp) < 1e-9:
            continue
        meta = spec(key)
        favors_home = pp > 0
        items.append(
            Contribution(
                rank=0,
                feature_key=key,
                display_name=meta.display_name,
                category=str(meta.category),
                category_label=CATEGORY_LABELS.get(str(meta.category), str(meta.category)),
                favors="H" if favors_home else "A",
                contribution_pp=abs(pp),
                feature_value=vector.features.get(key),
                feature_display=_format_value(key, vector.features.get(key)),
                sample_size=vector.sample_sizes.get(key),
                is_estimated=bool(vector.estimated_flags.get(key)),
                narrative=_narrative(
                    key, favors_home, home_name, away_name, vector.features.get(key)
                ),
            )
        )

    items.sort(key=lambda c: c.contribution_pp, reverse=True)
    for position, item in enumerate(items, start=1):
        item.rank = position
    return items


def split_for_and_against(
    contributions: list[Contribution], favored_side: str, top_n: int = TOP_N
) -> tuple[list[Contribution], list[Contribution]]:
    """Top drivers for the favored team, and the top counterweights."""
    for_side = [c for c in contributions if c.favors == favored_side][:top_n]
    against = [c for c in contributions if c.favors != favored_side][:top_n]
    return for_side, against


def category_totals(contributions: list[Contribution]) -> list[dict[str, Any]]:
    """Net probability points by category, for the matchup bars."""
    totals: dict[str, dict[str, Any]] = {}
    for item in contributions:
        entry = totals.setdefault(
            item.category,
            {"category": item.category, "label": item.category_label, "home_pp": 0.0,
             "away_pp": 0.0},
        )
        if item.favors == "H":
            entry["home_pp"] += item.contribution_pp
        else:
            entry["away_pp"] += item.contribution_pp
    for entry in totals.values():
        entry["net_pp"] = round(entry["home_pp"] - entry["away_pp"], 3)
        entry["home_pp"] = round(entry["home_pp"], 3)
        entry["away_pp"] = round(entry["away_pp"], 3)
    return sorted(totals.values(), key=lambda e: abs(e["net_pp"]), reverse=True)


def build_warnings(
    vector: FeatureVector,
    freshness: dict[str, str] | None = None,
    lineup_confirmed: bool = False,
    model_agreement: float | None = None,
) -> list[dict[str, str]]:
    """Risks and uncertainties, in the order a reader should weigh them."""
    warnings: list[dict[str, str]] = []

    if not vector.features.get("sp_identified_home"):
        warnings.append({
            "code": "HOME_STARTER_UNCONFIRMED",
            "severity": "high",
            "message": "Home starting pitcher is not yet announced. The model is using a "
                       "replacement-level prior in its place.",
        })
    if not vector.features.get("sp_identified_away"):
        warnings.append({
            "code": "AWAY_STARTER_UNCONFIRMED",
            "severity": "high",
            "message": "Away starting pitcher is not yet announced. The model is using a "
                       "replacement-level prior in its place.",
        })
    if not lineup_confirmed:
        warnings.append({
            "code": "LINEUP_UNCONFIRMED",
            "severity": "medium",
            "message": "Batting orders are not confirmed. Lineup-weighted features are "
                       "unavailable until the Phase 2 lineup feed is enabled.",
        })

    estimated = [k for k, flag in vector.estimated_flags.items() if flag
                 and vector.features.get(k) is not None]
    if len(estimated) >= 8:
        warnings.append({
            "code": "HEAVY_SHRINKAGE",
            "severity": "medium",
            "message": f"{len(estimated)} inputs are still small-sample and are shrunk "
                       f"toward league baselines.",
        })

    if vector.missing_features:
        warnings.append({
            "code": "MISSING_INPUTS",
            "severity": "medium" if len(vector.missing_features) > 3 else "low",
            "message": f"{len(vector.missing_features)} model inputs could not be computed: "
                       f"{', '.join(vector.missing_features[:5])}"
                       + ("…" if len(vector.missing_features) > 5 else ""),
        })

    if model_agreement is not None and model_agreement < 0.5:
        warnings.append({
            "code": "MODEL_DISAGREEMENT",
            "severity": "medium",
            "message": "Model disagreement is elevated for this game.",
        })

    for category, state in (freshness or {}).items():
        if state == "STALE":
            warnings.append({
                "code": f"STALE_{category.upper()}",
                "severity": "medium",
                "message": f"The {category.replace('_', ' ')} feed is stale.",
            })
        elif state == "UNAVAILABLE":
            warnings.append({
                "code": f"UNAVAILABLE_{category.upper()}",
                "severity": "low",
                "message": f"No {category.replace('_', ' ')} data is available; features "
                           f"in that group are not used.",
            })
    return warnings
