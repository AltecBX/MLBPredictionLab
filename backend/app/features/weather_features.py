"""Weather as a feature, and the reason the obvious version cannot work.

The park-factor measurement in MODELING_PLAN.md settled something that applies
here directly: **a condition shared by both teams moves the total and not the
margin.** A park factor multiplies both sides' expected runs by the same scalar
and is inert on a win probability by construction, not by measurement.

Temperature, air density and wind are shared conditions. Both teams hit in the
same air. So a feature that says "tonight the ball carries" is a totals input
wearing a win model's clothes, and expecting it to move a win probability is
expecting the arithmetic to behave differently than it did for parks.

What can move a margin is an **interaction**: the same air is shared, but the
two pitching staffs are not equally exposed to it. A staff that gives up fly
balls suffers more in carrying air than a staff that keeps the ball on the
ground, and that difference is asymmetric between the teams. That is the
hypothesis worth testing, and it is the one this module is built around.

Three features, and their shapes are deliberate:

* `wx_carry_index` — shared, absolute. Included precisely so the ablation can
  say whether shared conditions do anything at all against this target, rather
  than the claim resting on the park argument alone.
* `wx_carry_x_flyball_diff` — the interaction. Carry times the gap between the
  two staffs' fly-ball tendency, signed so a positive value favours the home
  team.
* `wx_precip_prob` — shared, absolute. Rain does not change who is better; it
  changes how much baseball gets played before a result stands.

An enclosed roof makes the outdoor forecast irrelevant, so carry is neutral
there and the feature says so through its sample size rather than by pretending
the dome has weather.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from app.features.shrinkage import FeatureValue
from app.features.weather_physics import REFERENCE_DENSITY

#: Wind at which the out-to-centre component is treated as a full unit of carry.
#: Fifteen mph is a strong steady wind; the index saturates rather than letting
#: one gusty forecast dominate a season of games.
WIND_SCALE_MPH = 15.0

#: How much of the carry index the wind can contribute against density. Density
#: is the larger and steadier effect, so it carries the majority weight. Both
#: are pre-registered rather than fitted — nothing here is tuned against the
#: outcome it is scored on.
WIND_WEIGHT = 0.4
DENSITY_WEIGHT = 0.6

#: Innings of team pitching before a fly-ball tendency means anything.
MIN_OUTS_FOR_TENDENCY = 300


@dataclass(frozen=True, slots=True)
class Carry:
    """How much tonight's air helps a struck ball, and whether it is known."""

    index: float
    is_measured: bool
    reason: str | None = None


def carry_index(weather: dict[str, Any] | None) -> Carry:
    """Positive when the ball carries further than a standard evening.

    Density is inverted — *lower* density means more carry — and expressed
    against the standard reference so zero is a neutral night rather than an
    arbitrary origin.
    """
    if not weather:
        return Carry(0.0, False, "no forecast for this game at prediction time")
    if str(weather.get("roof_status") or "").upper() == "ENCLOSED":
        # Not missing — measured, and genuinely neutral. A dome is a finding.
        return Carry(0.0, True, None)

    density = weather.get("air_density_kg_m3")
    if density is None or pd.isna(density):
        return Carry(0.0, False, "forecast carries no air density")
    density_term = (REFERENCE_DENSITY - float(density)) / REFERENCE_DENSITY

    wind_term = 0.0
    label = weather.get("wind_field_relative")
    speed = weather.get("wind_speed_mph")
    if label and speed is not None and not pd.isna(speed):
        signed = {"OUT_TO_CENTRE": 1.0, "IN_FROM_CENTRE": -1.0}.get(str(label), 0.0)
        wind_term = signed * min(float(speed) / WIND_SCALE_MPH, 1.0)

    return Carry(DENSITY_WEIGHT * density_term + WIND_WEIGHT * wind_term, True)


def flyball_tendency(pitching: pd.DataFrame) -> tuple[float | None, int]:
    """Share of a staff's batted-ball outs that were in the air, season to date.

    Outs rather than batted balls because outs are what the box score records.
    It is a tendency, not a rate against the league, and it is compared only to
    the other team's — so a systematic bias in the denominator cancels.
    """
    if pitching.empty:
        return None, 0
    air = pd.to_numeric(pitching.get("air_outs_pitched"), errors="coerce").fillna(0).sum()
    ground = pd.to_numeric(
        pitching.get("ground_outs_pitched"), errors="coerce"
    ).fillna(0).sum()
    total = float(air) + float(ground)
    if total < MIN_OUTS_FOR_TENDENCY:
        return None, int(total)
    return float(air) / total, int(total)


def weather_values(
    store: Any,
    game_id: int,
    as_of: datetime,
    home_pitching: pd.DataFrame,
    away_pitching: pd.DataFrame,
) -> dict[str, FeatureValue]:
    """The three weather features for one game, as-of.

    Returns missing values rather than zeros when the forecast or either staff's
    tendency is unavailable — a game with no forecast is not a game played in
    neutral air.
    """
    forecast = store.weather_asof(game_id, as_of)
    carry = carry_index(forecast)

    out: dict[str, FeatureValue] = {}
    if not carry.is_measured:
        reason = carry.reason or "no forecast"
        return {
            "wx_carry_index": FeatureValue.missing(reason),
            "wx_carry_x_flyball_diff": FeatureValue.missing(reason),
            "wx_precip_prob": FeatureValue.missing(reason),
        }

    out["wx_carry_index"] = FeatureValue(carry.index, 1, True)

    precip = (forecast or {}).get("precipitation_prob")
    out["wx_precip_prob"] = (
        FeatureValue(float(precip) / 100.0, 1, True)
        if precip is not None and not pd.isna(precip)
        else FeatureValue.missing("forecast carries no precipitation probability")
    )

    home_fb, home_n = flyball_tendency(home_pitching)
    away_fb, away_n = flyball_tendency(away_pitching)
    if home_fb is None or away_fb is None:
        out["wx_carry_x_flyball_diff"] = FeatureValue.missing(
            "not enough batted-ball outs on record to establish a staff tendency"
        )
    else:
        # Signed so positive favours the home team: carrying air hurts whichever
        # staff puts more balls in the air, so the home side benefits when the
        # AWAY staff is the more fly-ball prone of the two.
        out["wx_carry_x_flyball_diff"] = FeatureValue(
            carry.index * (away_fb - home_fb), min(home_n, away_n), True
        )
    return out


__all__ = [
    "DENSITY_WEIGHT",
    "MIN_OUTS_FOR_TENDENCY",
    "WIND_SCALE_MPH",
    "WIND_WEIGHT",
    "Carry",
    "carry_index",
    "flyball_tendency",
    "weather_values",
]
