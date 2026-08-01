"""Game context DTO.

Structurally omits every outcome field, so a feature builder cannot reference
the target game's result even by accident (LEAKAGE_PREVENTION.md §4).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from app.core.clock import AsOfPolicy, as_of_for_game, ensure_utc

# Fields that must never appear on a GameContext.
FORBIDDEN_CONTEXT_FIELDS = frozenset(
    {"home_win", "home_score", "away_score", "is_final", "innings_played",
     "winner", "final_score", "game_end_utc"}
)


@dataclass(frozen=True, slots=True)
class GameContext:
    game_id: int
    season: int
    game_type: str
    first_pitch_utc: datetime
    official_date: date
    home_team_id: int
    away_team_id: int
    venue_id: int | None
    day_night: str | None
    doubleheader: str | None
    game_number: int | None
    home_starter_id: int | None
    away_starter_id: int | None
    home_starter_status: str
    away_starter_status: str

    def as_of(self, policy: AsOfPolicy = "T_MINUS_3H") -> datetime:
        return as_of_for_game(self.first_pitch_utc, policy)

    @classmethod
    def from_row(cls, row: pd.Series | dict) -> GameContext:
        get = row.get if isinstance(row, dict) else row.get
        home_sp = get("home_probable_pitcher_id")
        away_sp = get("away_probable_pitcher_id")
        home_sp = int(home_sp) if home_sp is not None and not pd.isna(home_sp) else None
        away_sp = int(away_sp) if away_sp is not None and not pd.isna(away_sp) else None
        game_number = get("game_number")
        return cls(
            game_id=int(get("id")),
            season=int(get("season")),
            game_type=str(get("game_type") or "R"),
            first_pitch_utc=ensure_utc(pd.Timestamp(get("game_date_utc")).to_pydatetime()),
            official_date=pd.Timestamp(get("official_date")).date(),
            home_team_id=int(get("home_team_id")),
            away_team_id=int(get("away_team_id")),
            venue_id=(
                int(get("venue_id"))
                if get("venue_id") is not None and not pd.isna(get("venue_id"))
                else None
            ),
            day_night=get("day_night"),
            doubleheader=get("doubleheader"),
            game_number=(
                int(game_number)
                if game_number is not None and not pd.isna(game_number)
                else None
            ),
            home_starter_id=home_sp,
            away_starter_id=away_sp,
            home_starter_status="PROBABLE" if home_sp else "UNKNOWN",
            away_starter_status="PROBABLE" if away_sp else "UNKNOWN",
        )
