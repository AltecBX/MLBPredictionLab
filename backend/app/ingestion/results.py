"""Boxscore ingestion.

Produces the per-game team and player lines that every rolling feature is
rebuilt from. Season-aggregate endpoints are never consumed
(LEAKAGE_PREVENTION.md §3).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.upsert import upsert
from app.db.models import (
    Game,
    GameOfficial,
    Lineup,
    Player,
    PlayerGameStat,
    TeamGameStat,
)
from app.ingestion.reference import ingest_players
from app.ingestion.status import apply_provider_result, job_run, record_source_status
from app.providers.base import DataCategory, RawBoxscore
from app.providers.mlb_statsapi import mappers
from app.providers.registry import get_results_provider

log = get_logger(__name__)


def _team_rows(box: RawBoxscore, game: Game, result: Any) -> list[dict[str, Any]]:
    knowledge = _knowledge_time(game)
    rows = []
    for line in box.team_lines:
        row: dict[str, Any] = {
            "game_id": game.id,
            "team_id": line.team_id,
            "opponent_team_id": line.opponent_team_id,
            "is_home": line.is_home,
            "game_date_utc": game.game_date_utc,
            "source_name": result.source_name,
            "retrieved_at": result.retrieved_at,
            "knowledge_time": knowledge,
        }
        row.update(mappers.extract(line.batting, mappers.TEAM_BATTING_FIELDS))
        row.update(mappers.extract(line.pitching, mappers.TEAM_PITCHING_FIELDS))
        row["outs_pitched"] = mappers.outs_from_stats(line.pitching)
        row["errors"] = mappers._int((line.fielding or {}).get("errors"))
        rows.append(row)
    return rows


def _player_rows(box: RawBoxscore, game: Game, result: Any,
                 known_players: set[int]) -> list[dict[str, Any]]:
    knowledge = _knowledge_time(game)
    rows = []
    for line in box.player_lines:
        if line.player_id not in known_players:
            continue
        row: dict[str, Any] = {
            "game_id": game.id,
            "player_id": line.player_id,
            "team_id": line.team_id,
            "opponent_team_id": line.opponent_team_id,
            "game_date_utc": game.game_date_utc,
            "is_home": line.is_home,
            "role": line.role,
            "batting_order": line.batting_order,
            "batting_order_slot": line.batting_order,
            "is_starter": line.is_starter,
            "position": line.position,
            "source_name": result.source_name,
            "retrieved_at": result.retrieved_at,
            "knowledge_time": knowledge,
        }
        if line.role == "batter":
            row.update(mappers.extract(line.stats, mappers.BATTING_FIELDS))
        else:
            row.update(mappers.extract(line.stats, mappers.PITCHING_FIELDS))
            row["outs_pitched"] = mappers.outs_from_stats(line.stats)
        rows.append(row)
    return rows


def _knowledge_time(game: Game):
    """A game's boxscore is knowable only after the game ends."""
    if game.game_end_utc is not None:
        return game.game_end_utc
    return game.game_date_utc + mappers.RESULT_KNOWLEDGE_LAG


def ingest_boxscore(session: Session, game_id: int) -> dict[str, int]:
    """Fetch and persist one game's boxscore. Idempotent."""
    result = get_results_provider().fetch_boxscore(game_id)
    return persist_boxscore(session, game_id, result)


def persist_boxscore(session: Session, game_id: int, result: Any) -> dict[str, int]:
    """Persist an already-fetched boxscore.

    Split from fetching so a backfill can issue requests concurrently while
    writes stay on a single session.
    """
    game = session.get(Game, game_id)
    if game is None:
        log.warning("ingest.boxscore.unknown_game", game_id=game_id)
        return {"team_lines": 0, "player_lines": 0, "lineups": 0}

    apply_provider_result(session, result)
    if not result.ok or result.data is None:
        log.warning("ingest.boxscore.unavailable", game_id=game_id, message=result.message)
        return {"team_lines": 0, "player_lines": 0, "lineups": 0}

    box: RawBoxscore = result.data
    player_ids = sorted({line.player_id for line in box.player_lines})
    ingest_players(session, player_ids)
    session.flush()
    known_players = {
        p for (p,) in session.query(Player.id).filter(Player.id.in_(player_ids)).all()
    }

    n_team = upsert(session, TeamGameStat, _team_rows(box, game, result),
                     ["game_id", "team_id"])
    n_player = upsert(
        session,
        PlayerGameStat,
        _player_rows(box, game, result, known_players),
        ["game_id", "player_id", "role"],
    )

    lineup_rows = [
        {
            **entry,
            "is_confirmed": True,
            "lineup_status": "CONFIRMED",
            "observed_at": game.game_date_utc,
            "source_name": result.source_name,
            "retrieved_at": result.retrieved_at,
            "knowledge_time": _knowledge_time(game),
        }
        for entry in box.lineups
        if entry["player_id"] in known_players
    ]
    n_lineup = upsert(
        session, Lineup, lineup_rows,
        ["game_id", "team_id", "batting_order", "knowledge_time"],
    )

    official_rows = [
        {
            **official,
            "game_id": game.id,
            "source_name": result.source_name,
            "retrieved_at": result.retrieved_at,
            "knowledge_time": _knowledge_time(game),
        }
        for official in box.officials
    ]
    upsert(session, GameOfficial, official_rows, ["game_id", "official_type"])

    return {"team_lines": n_team, "player_lines": n_player, "lineups": n_lineup}


def pending_boxscore_ids(session: Session, limit: int | None = None) -> list[int]:
    stmt = (
        select(Game.id)
        .outerjoin(TeamGameStat, TeamGameStat.game_id == Game.id)
        .where(Game.is_final.is_(True), TeamGameStat.id.is_(None))
        .order_by(Game.game_date_utc)
    )
    if limit:
        stmt = stmt.limit(limit)
    return [row[0] for row in session.execute(stmt).all()]


def run_results_ingest(
    session: Session,
    limit: int | None = None,
    batch_size: int = 60,
    concurrency: int = 6,
) -> dict[str, int]:
    """Backfill boxscores for final games that do not have one yet.

    Requests are issued concurrently but remain globally rate limited by the
    provider client; writes stay serial on one session. Chunked and resumable —
    an interrupted run simply picks up the games it has not yet stored.
    """
    game_ids = pending_boxscore_ids(session, limit)
    totals = {"games": 0, "team_lines": 0, "player_lines": 0, "lineups": 0, "failed": 0}
    provider = get_results_provider()

    with job_run(session, "ingest_results", pending=len(game_ids)) as run:
        for start in range(0, len(game_ids), batch_size):
            batch = game_ids[start : start + batch_size]
            with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
                fetched = list(pool.map(provider.fetch_boxscore, batch))

            for game_id, result in zip(batch, fetched, strict=True):
                try:
                    counts = persist_boxscore(session, game_id, result)
                except Exception as exc:  # a single bad game must not abort the run
                    totals["failed"] += 1
                    log.error("ingest.boxscore.failed", game_id=game_id, error=str(exc))
                    session.rollback()
                    continue
                if counts["team_lines"]:
                    totals["games"] += 1
                    totals["team_lines"] += counts["team_lines"]
                    totals["player_lines"] += counts["player_lines"]
                    totals["lineups"] += counts["lineups"]
                else:
                    totals["failed"] += 1
            session.commit()
            log.info(
                "ingest.results.progress",
                done=min(start + batch_size, len(game_ids)),
                of=len(game_ids),
                failed=totals["failed"],
            )
        run.rows_written = totals["player_lines"]

    record_source_status(
        session,
        get_results_provider().name,
        DataCategory.PLAYER_STATS,
        success=totals["failed"] < max(1, len(game_ids)),
        records=totals["player_lines"],
        detail=f"{totals['failed']} of {len(game_ids)} boxscores failed."
        if totals["failed"]
        else None,
    )
    record_source_status(
        session,
        get_results_provider().name,
        DataCategory.BULLPEN_USAGE,
        success=totals["games"] > 0 or not game_ids,
        records=totals["player_lines"],
        detail="Derived from ingested relief-appearance game logs.",
    )
    log.info("ingest.results", **totals)
    return totals
