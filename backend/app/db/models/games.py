"""Game, boxscore-derived statistics, lineups, and pitch-level tables."""

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
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, SourcedMixin


class Game(Base, SourcedMixin):
    __tablename__ = "games"
    __table_args__ = (
        Index("ix_games_official_date", "official_date"),
        Index("ix_games_season_type", "season", "game_type"),
        Index("ix_games_home_date", "home_team_id", "game_date_utc"),
        Index("ix_games_away_date", "away_team_id", "game_date_utc"),
        Index("ix_games_final_date", "is_final", "game_date_utc"),
        CheckConstraint(
            "(home_win IS NULL) OR (is_final AND home_score IS NOT NULL "
            "AND away_score IS NOT NULL)",
            name="ck_games_label_requires_final",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    game_guid: Mapped[str | None] = mapped_column(Text)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    game_type: Mapped[str] = mapped_column(String(4), nullable=False)
    game_date_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    official_date: Mapped[date] = mapped_column(Date, nullable=False)

    status_abstract: Mapped[str | None] = mapped_column(String(16))
    status_detailed: Mapped[str | None] = mapped_column(Text)
    status_code: Mapped[str | None] = mapped_column(String(8))

    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    venue_id: Mapped[int | None] = mapped_column(ForeignKey("ballparks.id"))

    day_night: Mapped[str | None] = mapped_column(String(8))
    doubleheader: Mapped[str | None] = mapped_column(String(1))
    game_number: Mapped[int | None] = mapped_column(Integer)
    series_game_number: Mapped[int | None] = mapped_column(Integer)
    games_in_series: Mapped[int | None] = mapped_column(Integer)
    scheduled_innings: Mapped[int | None] = mapped_column(Integer)

    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    home_win: Mapped[bool | None] = mapped_column(Boolean)
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    innings_played: Mapped[int | None] = mapped_column(Integer)
    game_end_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    home_probable_pitcher_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    away_probable_pitcher_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    probable_pitchers_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    home_record_wins: Mapped[int | None] = mapped_column(Integer)
    home_record_losses: Mapped[int | None] = mapped_column(Integer)
    away_record_wins: Mapped[int | None] = mapped_column(Integer)
    away_record_losses: Mapped[int | None] = mapped_column(Integer)

    # Observed weather, present only for games this source has played out.
    weather_condition: Mapped[str | None] = mapped_column(Text)
    weather_temp_f: Mapped[int | None] = mapped_column(Integer)
    weather_wind: Mapped[str | None] = mapped_column(Text)


class TeamGameStat(Base, SourcedMixin):
    """One row per team per game, from the boxscore."""

    __tablename__ = "team_game_stats"
    __table_args__ = (
        UniqueConstraint("game_id", "team_id", name="uq_team_game"),
        Index("ix_tgs_team_date", "team_id", "game_date_utc"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    opponent_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    is_home: Mapped[bool] = mapped_column(Boolean, nullable=False)
    game_date_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Batting
    runs: Mapped[int | None] = mapped_column(Integer)
    hits: Mapped[int | None] = mapped_column(Integer)
    doubles: Mapped[int | None] = mapped_column(Integer)
    triples: Mapped[int | None] = mapped_column(Integer)
    home_runs: Mapped[int | None] = mapped_column(Integer)
    walks: Mapped[int | None] = mapped_column(Integer)
    intentional_walks: Mapped[int | None] = mapped_column(Integer)
    strikeouts: Mapped[int | None] = mapped_column(Integer)
    hit_by_pitch: Mapped[int | None] = mapped_column(Integer)
    stolen_bases: Mapped[int | None] = mapped_column(Integer)
    caught_stealing: Mapped[int | None] = mapped_column(Integer)
    left_on_base: Mapped[int | None] = mapped_column(Integer)
    at_bats: Mapped[int | None] = mapped_column(Integer)
    plate_appearances: Mapped[int | None] = mapped_column(Integer)
    total_bases: Mapped[int | None] = mapped_column(Integer)
    sac_flies: Mapped[int | None] = mapped_column(Integer)
    sac_bunts: Mapped[int | None] = mapped_column(Integer)
    gidp: Mapped[int | None] = mapped_column(Integer)

    # Pitching / defense
    runs_allowed: Mapped[int | None] = mapped_column(Integer)
    earned_runs_allowed: Mapped[int | None] = mapped_column(Integer)
    hits_allowed: Mapped[int | None] = mapped_column(Integer)
    walks_allowed: Mapped[int | None] = mapped_column(Integer)
    strikeouts_pitched: Mapped[int | None] = mapped_column(Integer)
    home_runs_allowed: Mapped[int | None] = mapped_column(Integer)
    outs_pitched: Mapped[int | None] = mapped_column(Integer)
    batters_faced: Mapped[int | None] = mapped_column(Integer)
    pitches_thrown: Mapped[int | None] = mapped_column(Integer)
    strikes_thrown: Mapped[int | None] = mapped_column(Integer)
    ground_outs_pitched: Mapped[int | None] = mapped_column(Integer)
    air_outs_pitched: Mapped[int | None] = mapped_column(Integer)
    errors: Mapped[int | None] = mapped_column(Integer)


class PlayerGameStat(Base, SourcedMixin):
    """One row per player per game per role. Substrate for every rolling feature."""

    __tablename__ = "player_game_stats"
    __table_args__ = (
        UniqueConstraint("game_id", "player_id", "role", name="uq_player_game_role"),
        Index("ix_pgs_player_role_date", "player_id", "role", "game_date_utc"),
        Index("ix_pgs_team_date", "team_id", "game_date_utc"),
        Index("ix_pgs_starter", "player_id", "is_starter", "game_date_utc"),
        Index("ix_pgs_team_role_starter_date", "team_id", "role", "is_starter", "game_date_utc"),
        CheckConstraint("role IN ('batter','pitcher')", name="ck_pgs_role"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    opponent_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    game_date_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_home: Mapped[bool] = mapped_column(Boolean, nullable=False)
    role: Mapped[str] = mapped_column(String(8), nullable=False)
    batting_order: Mapped[int | None] = mapped_column(Integer)
    batting_order_slot: Mapped[int | None] = mapped_column(Integer)
    is_starter: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    position: Mapped[str | None] = mapped_column(String(8))

    # Batting
    pa: Mapped[int | None] = mapped_column(Integer)
    ab: Mapped[int | None] = mapped_column(Integer)
    hits: Mapped[int | None] = mapped_column(Integer)
    doubles: Mapped[int | None] = mapped_column(Integer)
    triples: Mapped[int | None] = mapped_column(Integer)
    home_runs: Mapped[int | None] = mapped_column(Integer)
    runs: Mapped[int | None] = mapped_column(Integer)
    rbi: Mapped[int | None] = mapped_column(Integer)
    bb: Mapped[int | None] = mapped_column(Integer)
    ibb: Mapped[int | None] = mapped_column(Integer)
    so: Mapped[int | None] = mapped_column(Integer)
    hbp: Mapped[int | None] = mapped_column(Integer)
    sb: Mapped[int | None] = mapped_column(Integer)
    cs: Mapped[int | None] = mapped_column(Integer)
    sac_flies: Mapped[int | None] = mapped_column(Integer)
    sac_bunts: Mapped[int | None] = mapped_column(Integer)
    gidp: Mapped[int | None] = mapped_column(Integer)
    total_bases: Mapped[int | None] = mapped_column(Integer)
    left_on_base: Mapped[int | None] = mapped_column(Integer)

    # Pitching
    games_started: Mapped[int | None] = mapped_column(Integer)
    outs_pitched: Mapped[int | None] = mapped_column(Integer)
    batters_faced: Mapped[int | None] = mapped_column(Integer)
    hits_allowed: Mapped[int | None] = mapped_column(Integer)
    runs_allowed: Mapped[int | None] = mapped_column(Integer)
    earned_runs: Mapped[int | None] = mapped_column(Integer)
    bb_allowed: Mapped[int | None] = mapped_column(Integer)
    ibb_allowed: Mapped[int | None] = mapped_column(Integer)
    so_pitched: Mapped[int | None] = mapped_column(Integer)
    hr_allowed: Mapped[int | None] = mapped_column(Integer)
    hbp_allowed: Mapped[int | None] = mapped_column(Integer)
    pitches_thrown: Mapped[int | None] = mapped_column(Integer)
    strikes_thrown: Mapped[int | None] = mapped_column(Integer)
    ground_outs_pitched: Mapped[int | None] = mapped_column(Integer)
    air_outs_pitched: Mapped[int | None] = mapped_column(Integer)
    inherited_runners: Mapped[int | None] = mapped_column(Integer)
    inherited_runners_scored: Mapped[int | None] = mapped_column(Integer)
    wild_pitches: Mapped[int | None] = mapped_column(Integer)
    balks: Mapped[int | None] = mapped_column(Integer)


class Lineup(Base, SourcedMixin):
    """Append-only lineup snapshots. Multiple snapshots per game are retained."""

    __tablename__ = "lineups"
    __table_args__ = (
        UniqueConstraint("game_id", "team_id", "batting_order", "knowledge_time",
                         name="uq_lineup_snapshot"),
        Index("ix_lineups_game_team", "game_id", "team_id"),
        CheckConstraint(
            "lineup_status IN ('CONFIRMED','PROJECTED','UNAVAILABLE')",
            name="ck_lineup_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    batting_order: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[str | None] = mapped_column(String(8))
    is_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    lineup_status: Mapped[str] = mapped_column(String(16), nullable=False)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Pitch(Base, SourcedMixin):
    """One row per pitch, from Baseball Savant's Statcast export.

    Every column is nullable because Savant itself leaves them null on older
    seasons and on pitches its tracking missed. A missing value is recorded as
    missing; it is never filled with a default.
    """

    __tablename__ = "pitches"
    __table_args__ = (
        # Re-ingesting a date is idempotent, and a duplicated pitch is a
        # constraint violation rather than a silently doubled sample.
        UniqueConstraint("game_id", "at_bat_index", "pitch_number", name="uq_pitch"),
        Index("ix_pitches_game", "game_id"),
        # (player, knowledge_time) is the shape every as-of rolling window
        # queries with, so it is the shape the index takes.
        Index("ix_pitches_pitcher_known", "pitcher_id", "knowledge_time"),
        Index("ix_pitches_batter_known", "batter_id", "knowledge_time"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    at_bat_index: Mapped[int | None] = mapped_column(Integer)
    pitch_number: Mapped[int | None] = mapped_column(Integer)
    pitcher_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    batter_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    inning: Mapped[int | None] = mapped_column(Integer)
    is_top: Mapped[bool | None] = mapped_column(Boolean)
    balls: Mapped[int | None] = mapped_column(Integer)
    strikes: Mapped[int | None] = mapped_column(Integer)
    outs: Mapped[int | None] = mapped_column(Integer)
    pitch_type: Mapped[str | None] = mapped_column(String(8))
    release_speed: Mapped[float | None] = mapped_column(Numeric(5, 2))
    spin_rate: Mapped[int | None] = mapped_column(Integer)
    pfx_x: Mapped[float | None] = mapped_column(Numeric(6, 3))
    pfx_z: Mapped[float | None] = mapped_column(Numeric(6, 3))
    plate_x: Mapped[float | None] = mapped_column(Numeric(6, 3))
    plate_z: Mapped[float | None] = mapped_column(Numeric(6, 3))
    release_extension: Mapped[float | None] = mapped_column(Numeric(5, 2))
    zone: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    call: Mapped[str | None] = mapped_column(Text)

    # --- Phase 2A -----------------------------------------------------------
    pitch_name: Mapped[str | None] = mapped_column(Text)
    effective_speed: Mapped[float | None] = mapped_column(Numeric(5, 2))
    spin_axis: Mapped[int | None] = mapped_column(Integer)
    batter_stands: Mapped[str | None] = mapped_column(String(1))
    pitcher_throws: Mapped[str | None] = mapped_column(String(1))
    # Derived once at ingest from `description`, so a change to Savant's
    # vocabulary fails loudly instead of reclassifying history at query time.
    #
    # False on the rows Savant emits for a ball or strike awarded without a
    # pitch — the no-pitch intentional walk and the pitch-timer violation. Those
    # are not pitches, and every count and rate denominator has to say so: they
    # inflated 14 of the first 30 games reconciled, by up to 20 pitches each.
    is_pitch: Mapped[bool | None] = mapped_column(Boolean)
    is_swing: Mapped[bool | None] = mapped_column(Boolean)
    is_whiff: Mapped[bool | None] = mapped_column(Boolean)
    is_called_strike: Mapped[bool | None] = mapped_column(Boolean)
    is_in_zone: Mapped[bool | None] = mapped_column(Boolean)
    times_through_order: Mapped[int | None] = mapped_column(SmallInteger)
    pitcher_days_since_prev: Mapped[int | None] = mapped_column(SmallInteger)
    bat_speed: Mapped[float | None] = mapped_column(Numeric(4, 1))
    swing_length: Mapped[float | None] = mapped_column(Numeric(4, 2))
    # The plate appearance's outcome, set by Savant on the PA's final pitch and
    # null on every other. Carrying it here is what makes strikeouts, walks and
    # hit-by-pitches countable exactly, from the source's own field, instead of
    # reconstructing them from a hand-written rules engine over `description`
    # (two strikes plus a foul tip is a strikeout, plus a plain foul is not,
    # and so on) that would drift the first time a rule changed.
    pa_event: Mapped[str | None] = mapped_column(Text)
    # wOBA numerator and denominator for that same terminal pitch. Batted-ball
    # rows carry these too, but only for balls in play; a pitcher's wOBA against
    # has to include the strikeouts and walks, so it is computed from here.
    woba_value: Mapped[float | None] = mapped_column(Numeric(5, 4))
    woba_denom: Mapped[int | None] = mapped_column(SmallInteger)


class BattedBallEvent(Base, SourcedMixin):
    """One row per ball in play, from the same Statcast export."""

    __tablename__ = "batted_ball_events"
    __table_args__ = (
        UniqueConstraint("game_id", "at_bat_index", "pitch_number", name="uq_bbe"),
        Index("ix_bbe_batter_known", "batter_id", "knowledge_time"),
        Index("ix_bbe_pitcher_known", "pitcher_id", "knowledge_time"),
        Index("ix_bbe_game", "game_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    pitch_id: Mapped[int | None] = mapped_column(ForeignKey("pitches.id"))
    at_bat_index: Mapped[int | None] = mapped_column(Integer)
    pitch_number: Mapped[int | None] = mapped_column(Integer)
    batter_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    pitcher_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    launch_speed: Mapped[float | None] = mapped_column(Numeric(5, 2))
    launch_angle: Mapped[float | None] = mapped_column(Numeric(5, 2))
    hit_distance: Mapped[float | None] = mapped_column(Numeric(6, 2))
    # Savant's own 1-6 contact classification; 6 is a barrel. `is_barrel` is set
    # from it rather than reimplementing the published launch-speed/angle table.
    launch_speed_angle: Mapped[int | None] = mapped_column(SmallInteger)
    is_barrel: Mapped[bool | None] = mapped_column(Boolean)
    is_hard_hit: Mapped[bool | None] = mapped_column(Boolean)
    bb_type: Mapped[str | None] = mapped_column(Text)
    estimated_woba: Mapped[float | None] = mapped_column(Numeric(5, 4))
    estimated_ba: Mapped[float | None] = mapped_column(Numeric(5, 4))
    estimated_slg: Mapped[float | None] = mapped_column(Numeric(5, 4))
    # The *actual* outcome value, as distinct from the expected ones above.
    woba_value: Mapped[float | None] = mapped_column(Numeric(5, 4))
    woba_denom: Mapped[int | None] = mapped_column(SmallInteger)
    spray_angle: Mapped[float | None] = mapped_column(Numeric(5, 2))
    field_direction: Mapped[str | None] = mapped_column(String(4))
    outcome: Mapped[str | None] = mapped_column(Text)


class GameOfficial(Base, SourcedMixin):
    """Umpire assignments; supports the Phase 2 umpire strike-zone feature."""

    __tablename__ = "game_officials"
    __table_args__ = (
        UniqueConstraint("game_id", "official_type", name="uq_game_official"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    official_type: Mapped[str] = mapped_column(String(32), nullable=False)
    official_id: Mapped[int | None] = mapped_column(Integer)
    official_name: Mapped[str | None] = mapped_column(Text)


__all__ = [
    "Game",
    "TeamGameStat",
    "PlayerGameStat",
    "Lineup",
    "Pitch",
    "BattedBallEvent",
    "GameOfficial",
]
