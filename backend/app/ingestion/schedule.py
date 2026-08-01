"""Schedule ingestion, including probable pitchers and starter projections."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import settings
from app.core.logging import get_logger
from app.db.upsert import upsert
from app.db.models import (
    Ballpark,
    Game,
    Player,
    StartingPitcherProjection,
    Team,
    TeamGameStat,
)
from app.ingestion.reference import ingest_players
from app.ingestion.status import apply_provider_result, job_run, record_source_status
from app.providers.base import DataCategory, RawGame
from app.providers.mlb_statsapi import mappers
from app.providers.registry import get_schedule_provider

log = get_logger(__name__)

GAME_COLUMNS = {c.name for c in Game.__table__.columns}


def _game_row(game: RawGame, result: Any, known_venues: set[int]) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": game.id,
        "game_guid": game.game_guid,
        "season": game.season,
        "game_type": game.game_type,
        "game_date_utc": game.game_date_utc,
        "official_date": game.official_date,
        "status_abstract": game.status_abstract,
        "status_detailed": game.status_detailed,
        "status_code": game.status_code,
        "home_team_id": game.home_team_id,
        "away_team_id": game.away_team_id,
        "venue_id": game.venue_id if game.venue_id in known_venues else None,
        "day_night": game.day_night,
        "doubleheader": game.doubleheader,
        "game_number": game.game_number,
        "series_game_number": game.series_game_number,
        "games_in_series": game.games_in_series,
        "scheduled_innings": game.scheduled_innings,
        "home_score": game.home_score,
        "away_score": game.away_score,
        "home_win": game.home_win,
        "is_final": game.is_final,
        "innings_played": game.innings_played,
        "home_probable_pitcher_id": game.home_probable_pitcher_id,
        "away_probable_pitcher_id": game.away_probable_pitcher_id,
        "home_record_wins": game.home_record_wins,
        "home_record_losses": game.home_record_losses,
        "away_record_wins": game.away_record_wins,
        "away_record_losses": game.away_record_losses,
        "weather_condition": game.weather_condition,
        "weather_temp_f": game.weather_temp_f,
        "weather_wind": game.weather_wind,
        "source_name": result.source_name,
        "retrieved_at": result.retrieved_at,
        # A game's own result is knowable only after the game ends. Scheduling
        # facts are knowable at fetch time; the result columns are guarded by
        # the as-of window, which always precedes first pitch.
        "knowledge_time": (
            mappers.result_knowledge_time(game) if game.is_final else result.retrieved_at
        ),
    }
    return {k: v for k, v in row.items() if k in GAME_COLUMNS}


def ingest_schedule_range(session: Session, start: date, end: date) -> dict[str, int]:
    """Upsert every game in a date range. Idempotent and re-runnable."""
    provider = get_schedule_provider()
    result = provider.fetch_schedule(start, end)
    apply_provider_result(session, result, records=len(result.data or []))
    if not result.ok or result.data is None:
        log.warning("ingest.schedule.unavailable", message=result.message)
        return {"games": 0, "players": 0, "projections": 0}

    games = result.data
    known_teams = {t for (t,) in session.query(Team.id).all()}
    known_venues = {v for (v,) in session.query(Ballpark.id).all()}

    usable = [g for g in games if g.home_team_id in known_teams and g.away_team_id in known_teams]
    dropped = len(games) - len(usable)
    if dropped:
        log.warning("ingest.schedule.unknown_teams", dropped=dropped)

    pitcher_ids = sorted(
        {
            pid
            for g in usable
            for pid in (g.home_probable_pitcher_id, g.away_probable_pitcher_id)
            if pid
        }
    )
    n_players = ingest_players(session, pitcher_ids)
    session.flush()

    rows = [_game_row(g, result, known_venues) for g in usable]

    # Null out probable pitchers we could not resolve to a player row, rather
    # than creating a dangling reference.
    if pitcher_ids:
        known_players = {
            p for (p,) in session.query(Player.id).filter(Player.id.in_(pitcher_ids)).all()
        }
        for row in rows:
            for key in ("home_probable_pitcher_id", "away_probable_pitcher_id"):
                if row.get(key) is not None and row[key] not in known_players:
                    row[key] = None

    n_games = upsert(session, Game, rows, ["id"])

    n_projections = _record_starter_projections(session, usable, result)

    record_source_status(
        session,
        result.source_name,
        DataCategory.PROBABLE_PITCHERS,
        success=True,
        records=sum(
            1
            for g in usable
            if g.home_probable_pitcher_id or g.away_probable_pitcher_id
        ),
    )

    log.info(
        "ingest.schedule",
        start=start.isoformat(),
        end=end.isoformat(),
        games=n_games,
        players=n_players,
        projections=n_projections,
    )
    return {"games": n_games, "players": n_players, "projections": n_projections}


def _record_starter_projections(
    session: Session, games: list[RawGame], result: Any
) -> int:
    """Append an as-of snapshot of the expected starter for upcoming games."""
    now = utcnow()
    rows: list[dict[str, Any]] = []
    for game in games:
        if game.is_final:
            continue
        for team_id, pitcher_id in (
            (game.home_team_id, game.home_probable_pitcher_id),
            (game.away_team_id, game.away_probable_pitcher_id),
        ):
            rows.append(
                {
                    "game_id": game.id,
                    "team_id": team_id,
                    "pitcher_id": pitcher_id,
                    "status": "PROBABLE" if pitcher_id else "UNKNOWN",
                    "is_estimated": True,
                    "as_of": now,
                    "source_name": result.source_name,
                    "retrieved_at": result.retrieved_at,
                    "knowledge_time": result.retrieved_at,
                }
            )
    if not rows:
        return 0

    pids = {r["pitcher_id"] for r in rows if r["pitcher_id"]}
    known = (
        {p for (p,) in session.query(Player.id).filter(Player.id.in_(pids)).all()}
        if pids
        else set()
    )
    for row in rows:
        if row["pitcher_id"] and row["pitcher_id"] not in known:
            row["pitcher_id"] = None
            row["status"] = "UNKNOWN"

    return upsert(
        session,
        StartingPitcherProjection,
        rows,
        ["game_id", "team_id", "as_of"],
        update=False,
    )


def run_schedule_ingest(
    session: Session,
    days_back: int | None = None,
    days_forward: int | None = None,
) -> dict[str, int]:
    days_back = settings.schedule_window_days_back if days_back is None else days_back
    days_forward = (
        settings.schedule_window_days_forward if days_forward is None else days_forward
    )
    today = utcnow().date()
    start, end = today - timedelta(days=days_back), today + timedelta(days=days_forward)
    with job_run(session, "ingest_schedule", start=start.isoformat(), end=end.isoformat()) as run:
        counts = ingest_schedule_range(session, start, end)
        run.rows_written = counts["games"]
    return counts


def pending_boxscore_game_ids(session: Session, limit: int | None = None) -> list[int]:
    """Final games whose boxscore has not been ingested yet."""
    stmt = (
        select(Game.id)
        .outerjoin(TeamGameStat, TeamGameStat.game_id == Game.id)
        .where(Game.is_final.is_(True), TeamGameStat.id.is_(None))
        .order_by(Game.game_date_utc)
    )
    if limit:
        stmt = stmt.limit(limit)
    return [row[0] for row in session.execute(stmt).all()]
