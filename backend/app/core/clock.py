"""Single source of 'now'.

Injecting the clock keeps as-of logic testable and prevents accidental use of
wall-clock time inside backtests, where as_of is policy-derived
(LEAKAGE_PREVENTION.md §2).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

AsOfPolicy = Literal["T_MINUS_3H", "T_MINUS_60M", "T_MINUS_15M"]

_POLICY_OFFSETS: dict[str, timedelta] = {
    "T_MINUS_3H": timedelta(hours=3),
    "T_MINUS_60M": timedelta(minutes=60),
    "T_MINUS_15M": timedelta(minutes=15),
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_of_for_game(first_pitch_utc: datetime, policy: AsOfPolicy = "T_MINUS_3H") -> datetime:
    """Policy-derived prediction timestamp for a game.

    Never returns a time at or after first pitch.
    """
    if policy not in _POLICY_OFFSETS:
        raise ValueError(f"Unknown as-of policy: {policy}")
    if first_pitch_utc.tzinfo is None:
        first_pitch_utc = first_pitch_utc.replace(tzinfo=UTC)
    return first_pitch_utc - _POLICY_OFFSETS[policy]


def ensure_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
