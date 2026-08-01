"""Savant CSV -> normalized pitch and batted-ball rows.

The description vocabulary below is the load-bearing part. `description` is a
free-text field, and swing / whiff / called-strike are derived from it once at
ingest rather than at query time — so if Savant ever adds a value, the
`test_every_description_is_classified` test fails loudly instead of history
quietly reclassifying itself.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

# Data becomes available to us as a whole game, not pitch by pitch. Same lag the
# results ingest uses, for the same reason.
STATCAST_KNOWLEDGE_LAG = timedelta(hours=3, minutes=30)

# Every `description` Savant emits, partitioned. A value outside this set is a
# vocabulary change and must fail rather than default.
SWING_DESCRIPTIONS = frozenset({
    "hit_into_play", "foul", "swinging_strike", "swinging_strike_blocked",
    "foul_tip", "foul_bunt", "missed_bunt", "bunt_foul_tip",
    "hit_into_play_score", "hit_into_play_no_out", "swinging_pitchout",
    "foul_pitchout",
})
WHIFF_DESCRIPTIONS = frozenset({
    "swinging_strike", "swinging_strike_blocked", "missed_bunt", "swinging_pitchout",
})
CALLED_STRIKE_DESCRIPTIONS = frozenset({"called_strike"})
TAKE_DESCRIPTIONS = frozenset({
    "ball", "blocked_ball", "called_strike", "hit_by_pitch", "pitchout",
    "ball_blocked", "intent_ball", "automatic_ball", "automatic_strike",
})
KNOWN_DESCRIPTIONS = (
    SWING_DESCRIPTIONS | CALLED_STRIKE_DESCRIPTIONS | TAKE_DESCRIPTIONS
)

# Rows Savant emits for a ball or strike awarded without a pitch being thrown:
# the no-pitch intentional walk, and a pitch-timer violation on either side.
# They are real events but they are not pitches, and counting them as pitches is
# not a rounding error — it inflated 14 of the first 30 games reconciled, by up
# to 20 pitches, and would silently pad the denominator of every plate-discipline
# rate. `is_pitch` is derived from this set once, at ingest.
NON_PITCH_DESCRIPTIONS = frozenset({"automatic_ball", "automatic_strike"})

# A batted ball is a ball put *in play*. Statcast measures launch speed and angle
# on foul balls too, and taking every row with a launch measurement gave 98
# batted balls per game against a true rate near 54 — which would have diluted
# barrel rate, hard-hit rate and average exit velocity with a population no
# published version of those metrics includes. Fouls stay in `pitches`, where
# they belong.
IN_PLAY_DESCRIPTIONS = frozenset({
    "hit_into_play", "hit_into_play_score", "hit_into_play_no_out",
})

# Savant's own contact classification. 6 is a barrel; reimplementing the
# launch-speed/angle table would drift from the published definition.
BARREL_CLASS = 6
HARD_HIT_MPH = 95.0

# Statcast's coordinate origin for spray angle.
_HC_X_ORIGIN, _HC_Y_ORIGIN = 125.42, 198.27


def _num(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(out) else out


def _int(value: Any) -> int | None:
    out = _num(value)
    return None if out is None else int(out)


def _str(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip()
    return text or None


def spray_angle(hc_x: Any, hc_y: Any) -> float | None:
    """Horizontal angle of the batted ball, in degrees.

    0 is straight up the middle; negative is toward the third-base line and
    positive toward first, in Statcast's own coordinate frame.
    """
    x, y = _num(hc_x), _num(hc_y)
    if x is None or y is None:
        return None
    return round(
        math.degrees(math.atan2(x - _HC_X_ORIGIN, _HC_Y_ORIGIN - y)), 2
    )


def field_direction(angle: float | None, stands: str | None) -> str | None:
    """PULL / CENT / OPPO from the batter's point of view.

    A right-handed hitter pulls to the left side, which is *negative* in this
    frame, so the sign is flipped for them. Getting this backwards would invert
    every pull/oppo feature while still looking plausible, so it is tested.
    """
    if angle is None or stands not in ("L", "R"):
        return None
    pull_side = angle if stands == "L" else -angle
    if pull_side > 15.0:
        return "PULL"
    if pull_side < -15.0:
        return "OPPO"
    return "CENT"


def is_real_pitch(description: str | None) -> bool | None:
    """Whether a ball actually left the pitcher's hand. None if unrecorded."""
    if description is None:
        return None
    return description not in NON_PITCH_DESCRIPTIONS


def classify_description(description: str | None) -> tuple[bool, bool, bool]:
    """(is_swing, is_whiff, is_called_strike). Unknown vocabulary raises."""
    if description is None:
        return (False, False, False)
    if description not in KNOWN_DESCRIPTIONS:
        raise ValueError(
            f"Unrecognised Statcast description {description!r}. Savant's "
            f"vocabulary has changed; update mappers.py rather than letting "
            f"pitches be silently misclassified."
        )
    return (
        description in SWING_DESCRIPTIONS,
        description in WHIFF_DESCRIPTIONS,
        description in CALLED_STRIKE_DESCRIPTIONS,
    )


def knowledge_time_for(game_end_utc: datetime | None, first_pitch_utc: datetime) -> datetime:
    """When this game's Statcast became available to us."""
    if game_end_utc is not None:
        return game_end_utc
    return first_pitch_utc + STATCAST_KNOWLEDGE_LAG


def pitch_rows(
    frame: pd.DataFrame,
    known_game_ids: set[int],
    knowledge_times: dict[int, datetime],
    source_name: str,
    retrieved_at: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Normalize a Statcast export into `pitches` rows.

    A pitch whose `game_pk` is not already in `games` is **rejected, not
    invented** — the schedule ingest is the sole authority on which games exist.
    """
    retrieved_at = retrieved_at or datetime.now(UTC)
    rows: list[dict[str, Any]] = []
    counts = {"rows": 0, "unknown_game": 0, "missing_ids": 0}

    for record in frame.to_dict("records"):
        game_id = _int(record.get("game_pk"))
        if game_id is None or game_id not in known_game_ids:
            counts["unknown_game"] += 1
            continue
        pitcher_id, batter_id = _int(record.get("pitcher")), _int(record.get("batter"))
        if pitcher_id is None or batter_id is None:
            counts["missing_ids"] += 1
            continue

        description = _str(record.get("description"))
        is_swing, is_whiff, is_called = classify_description(description)
        zone = _int(record.get("zone"))

        rows.append({
            "game_id": game_id,
            "at_bat_index": _int(record.get("at_bat_number")),
            "pitch_number": _int(record.get("pitch_number")),
            "pitcher_id": pitcher_id,
            "batter_id": batter_id,
            "inning": _int(record.get("inning")),
            "is_top": _str(record.get("inning_topbot")) == "Top",
            "balls": _int(record.get("balls")),
            "strikes": _int(record.get("strikes")),
            "outs": _int(record.get("outs_when_up")),
            "pitch_type": _str(record.get("pitch_type")),
            "pitch_name": _str(record.get("pitch_name")),
            "release_speed": _num(record.get("release_speed")),
            "effective_speed": _num(record.get("effective_speed")),
            "spin_rate": _int(record.get("release_spin_rate")),
            "spin_axis": _int(record.get("spin_axis")),
            "pfx_x": _num(record.get("pfx_x")),
            "pfx_z": _num(record.get("pfx_z")),
            "plate_x": _num(record.get("plate_x")),
            "plate_z": _num(record.get("plate_z")),
            "release_extension": _num(record.get("release_extension")),
            "zone": zone,
            "is_in_zone": None if zone is None else 1 <= zone <= 9,
            "description": description,
            "call": _str(record.get("type")),
            "batter_stands": _str(record.get("stand")),
            "pitcher_throws": _str(record.get("p_throws")),
            "is_pitch": is_real_pitch(description),
            "is_swing": is_swing,
            "is_whiff": is_whiff,
            "is_called_strike": is_called,
            "times_through_order": _int(record.get("n_thruorder_pitcher")),
            "pitcher_days_since_prev": _int(record.get("pitcher_days_since_prev_game")),
            "bat_speed": _num(record.get("bat_speed")),
            "swing_length": _num(record.get("swing_length")),
            # Present only on a plate appearance's final pitch, which is exactly
            # what makes it usable as an exact PA count.
            "pa_event": _str(record.get("events")),
            "woba_value": _num(record.get("woba_value")),
            "woba_denom": _int(record.get("woba_denom")),
            "source_name": source_name,
            "retrieved_at": retrieved_at,
            "knowledge_time": knowledge_times[game_id],
        })
        counts["rows"] += 1
    return rows, counts


def batted_ball_rows(
    frame: pd.DataFrame,
    known_game_ids: set[int],
    knowledge_times: dict[int, datetime],
    source_name: str,
    retrieved_at: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Normalize balls in play into `batted_ball_events` rows."""
    retrieved_at = retrieved_at or datetime.now(UTC)
    rows: list[dict[str, Any]] = []
    counts = {"rows": 0, "skipped": 0}

    for record in frame.to_dict("records"):
        # Membership is decided by the description, not by the presence of a
        # launch measurement — fouls carry one. A ball in play with no tracking
        # data is still a batted ball and is stored with nulls; recording it as
        # missing is the point.
        if _str(record.get("description")) not in IN_PLAY_DESCRIPTIONS:
            continue

        launch_speed = _num(record.get("launch_speed"))
        bb_type = _str(record.get("bb_type"))
        game_id = _int(record.get("game_pk"))
        batter_id, pitcher_id = _int(record.get("batter")), _int(record.get("pitcher"))
        if game_id is None or game_id not in known_game_ids or batter_id is None or pitcher_id is None:
            counts["skipped"] += 1
            continue

        stands = _str(record.get("stand"))
        angle = spray_angle(record.get("hc_x"), record.get("hc_y"))
        lsa = _int(record.get("launch_speed_angle"))

        rows.append({
            "game_id": game_id,
            "at_bat_index": _int(record.get("at_bat_number")),
            "pitch_number": _int(record.get("pitch_number")),
            "batter_id": batter_id,
            "pitcher_id": pitcher_id,
            "launch_speed": launch_speed,
            "launch_angle": _num(record.get("launch_angle")),
            "hit_distance": _num(record.get("hit_distance_sc")),
            "launch_speed_angle": lsa,
            "is_barrel": None if lsa is None else lsa == BARREL_CLASS,
            "is_hard_hit": None if launch_speed is None else launch_speed >= HARD_HIT_MPH,
            "bb_type": bb_type,
            "estimated_woba": _num(record.get("estimated_woba_using_speedangle")),
            "estimated_ba": _num(record.get("estimated_ba_using_speedangle")),
            "estimated_slg": _num(record.get("estimated_slg_using_speedangle")),
            "woba_value": _num(record.get("woba_value")),
            "woba_denom": _int(record.get("woba_denom")),
            "spray_angle": angle,
            "field_direction": field_direction(angle, stands),
            "outcome": _str(record.get("events")),
            "source_name": source_name,
            "retrieved_at": retrieved_at,
            "knowledge_time": knowledge_times[game_id],
        })
        counts["rows"] += 1
    return rows, counts


__all__ = [
    "BARREL_CLASS",
    "CALLED_STRIKE_DESCRIPTIONS",
    "HARD_HIT_MPH",
    "IN_PLAY_DESCRIPTIONS",
    "KNOWN_DESCRIPTIONS",
    "NON_PITCH_DESCRIPTIONS",
    "STATCAST_KNOWLEDGE_LAG",
    "SWING_DESCRIPTIONS",
    "WHIFF_DESCRIPTIONS",
    "batted_ball_rows",
    "classify_description",
    "field_direction",
    "is_real_pitch",
    "knowledge_time_for",
    "pitch_rows",
    "spray_angle",
]
