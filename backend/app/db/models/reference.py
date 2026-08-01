"""Reference tables: teams, ballparks, park factors, players, rosters."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
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


class Ballpark(Base, SourcedMixin):
    __tablename__ = "ballparks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    city: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    elevation_ft: Mapped[int | None] = mapped_column(Integer)
    azimuth_angle: Mapped[float | None] = mapped_column(Float)
    roof_type: Mapped[str | None] = mapped_column(Text)
    turf_type: Mapped[str | None] = mapped_column(Text)
    capacity: Mapped[int | None] = mapped_column(Integer)
    lf_line: Mapped[int | None] = mapped_column(Integer)
    lf_center: Mapped[int | None] = mapped_column(Integer)
    center: Mapped[int | None] = mapped_column(Integer)
    rf_center: Mapped[int | None] = mapped_column(Integer)
    rf_line: Mapped[int | None] = mapped_column(Integer)
    timezone: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Team(Base, SourcedMixin):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    abbreviation: Mapped[str] = mapped_column(String(8), nullable=False)
    team_name: Mapped[str | None] = mapped_column(Text)
    location_name: Mapped[str | None] = mapped_column(Text)
    league_id: Mapped[int | None] = mapped_column(Integer)
    league_name: Mapped[str | None] = mapped_column(Text)
    division_id: Mapped[int | None] = mapped_column(Integer)
    division_name: Mapped[str | None] = mapped_column(Text)
    home_venue_id: Mapped[int | None] = mapped_column(ForeignKey("ballparks.id"))
    first_year_of_play: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ParkFactor(Base, SourcedMixin):
    """Populated in Phase 2. Empty until a park-factor provider is enabled."""

    __tablename__ = "park_factors"
    __table_args__ = (
        UniqueConstraint("venue_id", "season", "factor_type", "handedness",
                         name="uq_park_factor"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("ballparks.id"), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    factor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    handedness: Mapped[str | None] = mapped_column(String(1))
    value: Mapped[float] = mapped_column(Numeric(6, 3), nullable=False)
    sample_games: Mapped[int | None] = mapped_column(Integer)
    method: Mapped[str | None] = mapped_column(Text)
    is_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Player(Base, SourcedMixin):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    primary_position: Mapped[str | None] = mapped_column(String(8))
    position_type: Mapped[str | None] = mapped_column(Text)
    bat_side: Mapped[str | None] = mapped_column(String(1))
    pitch_hand: Mapped[str | None] = mapped_column(String(1))
    birth_date: Mapped[date | None] = mapped_column(Date)
    mlb_debut_date: Mapped[date | None] = mapped_column(Date)
    height_in: Mapped[int | None] = mapped_column(Integer)
    weight_lb: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Roster(Base, SourcedMixin):
    __tablename__ = "rosters"
    __table_args__ = (
        Index("ix_rosters_team_season", "team_id", "season"),
        Index("ix_rosters_player", "player_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str | None] = mapped_column(Text)
    roster_type: Mapped[str | None] = mapped_column(Text)
    jersey_number: Mapped[str | None] = mapped_column(String(8))
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


__all__ = ["Ballpark", "Team", "ParkFactor", "Player", "Roster"]

# Guard against a factor_type typo silently creating a new category.
ParkFactor.__table__.append_constraint(
    CheckConstraint(
        "factor_type IN ('runs','hr','hits','doubles','triples','k','bb')",
        name="ck_park_factor_type",
    )
)
