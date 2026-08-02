"""Pregame state: starter projections, bullpen availability, injuries, weather, odds."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, SourcedMixin


class StartingPitcherProjection(Base, SourcedMixin):
    """As-of view of who is expected to start and what is known about them."""

    __tablename__ = "starting_pitcher_projections"
    __table_args__ = (
        UniqueConstraint("game_id", "team_id", "as_of", name="uq_sp_projection"),
        Index("ix_spp_game", "game_id"),
        CheckConstraint(
            "status IN ('CONFIRMED','PROBABLE','PROJECTED','UNKNOWN')",
            name="ck_spp_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    pitcher_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    expected_innings: Mapped[float | None] = mapped_column(Numeric(4, 2))
    expected_pitch_count: Mapped[int | None] = mapped_column(Integer)
    days_rest: Mapped[int | None] = mapped_column(Integer)
    is_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BullpenAvailability(Base, SourcedMixin):
    """Phase 2. Per-pitcher availability is a distinct feed from usage."""

    __tablename__ = "bullpen_availability"
    __table_args__ = (
        UniqueConstraint("game_id", "team_id", "pitcher_id", "as_of", name="uq_bullpen_avail"),
        CheckConstraint(
            "availability IN ('AVAILABLE','LIMITED','UNAVAILABLE','UNKNOWN')",
            name="ck_bullpen_availability",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    pitcher_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    availability: Mapped[str] = mapped_column(String(16), nullable=False)
    pitches_last_1d: Mapped[int | None] = mapped_column(Integer)
    pitches_last_2d: Mapped[int | None] = mapped_column(Integer)
    pitches_last_3d: Mapped[int | None] = mapped_column(Integer)
    appearances_last_3d: Mapped[int | None] = mapped_column(Integer)
    appearances_last_7d: Mapped[int | None] = mapped_column(Integer)
    consecutive_days_pitched: Mapped[int | None] = mapped_column(Integer)
    is_closer: Mapped[bool | None] = mapped_column(Boolean)
    is_setup: Mapped[bool | None] = mapped_column(Boolean)
    throws: Mapped[str | None] = mapped_column(String(1))
    expected_role: Mapped[str | None] = mapped_column(Text)


class Injury(Base, SourcedMixin):
    """Phase 2. Bitemporal: effective_* is real-world validity."""

    __tablename__ = "injuries"
    __table_args__ = (
        Index("ix_injuries_player_effective", "player_id", "effective_from"),
        UniqueConstraint(
            "player_id", "effective_from", "status",
            name="uq_injury_player_effective_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    body_part: Mapped[str | None] = mapped_column(Text)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expected_return: Mapped[date | None] = mapped_column(Date)


class Weather(Base, SourcedMixin):
    """Observed weather lands in Phase 1 where the source supplies it; forecast is Phase 2."""

    __tablename__ = "weather"
    __table_args__ = (
        Index("ix_weather_game", "game_id", "observation_type"),
        UniqueConstraint(
            "game_id", "observation_type", "knowledge_time",
            name="uq_weather_game_type_knowledge",
        ),
        CheckConstraint(
            "observation_type IN ('FORECAST','OBSERVED')", name="ck_weather_obs_type"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    venue_id: Mapped[int | None] = mapped_column(ForeignKey("ballparks.id"))
    observation_type: Mapped[str] = mapped_column(String(16), nullable=False)
    valid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    temperature_f: Mapped[float | None] = mapped_column(Numeric(5, 2))
    feels_like_f: Mapped[float | None] = mapped_column(Numeric(5, 2))
    wind_speed_mph: Mapped[float | None] = mapped_column(Numeric(5, 2))
    wind_direction_deg: Mapped[int | None] = mapped_column(Integer)
    wind_direction_text: Mapped[str | None] = mapped_column(Text)
    wind_field_relative: Mapped[str | None] = mapped_column(Text)
    humidity_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))
    pressure_mb: Mapped[float | None] = mapped_column(Numeric(6, 2))
    precipitation_prob: Mapped[float | None] = mapped_column(Numeric(5, 2))
    precipitation_mm: Mapped[float | None] = mapped_column(Numeric(6, 2))
    condition: Mapped[str | None] = mapped_column(Text)
    air_density_kg_m3: Mapped[float | None] = mapped_column(Numeric(6, 4))
    roof_status: Mapped[str | None] = mapped_column(Text)
    is_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class OddsSnapshot(Base, SourcedMixin):
    """Phase 3. Only snapshots with snapshot_at <= as_of are ever readable by a feature."""

    __tablename__ = "odds_snapshots"
    __table_args__ = (
        Index("ix_odds_game_time", "game_id", "snapshot_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    book: Mapped[str] = mapped_column(Text, nullable=False)
    market: Mapped[str] = mapped_column(String(32), nullable=False, default="moneyline")
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    home_price: Mapped[int | None] = mapped_column(Integer)
    away_price: Mapped[int | None] = mapped_column(Integer)
    home_implied_prob: Mapped[float | None] = mapped_column(Numeric(6, 5))
    away_implied_prob: Mapped[float | None] = mapped_column(Numeric(6, 5))
    home_novig_prob: Mapped[float | None] = mapped_column(Numeric(6, 5))
    away_novig_prob: Mapped[float | None] = mapped_column(Numeric(6, 5))
    total_line: Mapped[float | None] = mapped_column(Numeric(4, 1))
    is_closing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


__all__ = [
    "StartingPitcherProjection",
    "BullpenAvailability",
    "Injury",
    "Weather",
    "OddsSnapshot",
]
