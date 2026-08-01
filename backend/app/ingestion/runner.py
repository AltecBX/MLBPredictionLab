"""Job orchestration for ingestion."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.logging import get_logger
from app.ingestion.reference import run_reference_ingest
from app.ingestion.results import run_results_ingest
from app.ingestion.schedule import ingest_schedule_range, run_schedule_ingest
from app.ingestion.status import job_run, seed_source_status
from app.providers.registry import configured_categories

log = get_logger(__name__)

# MLB regular season spans roughly late March through early October.
SEASON_START = (3, 1)
SEASON_END = (11, 15)


def season_bounds(season: int) -> tuple[date, date]:
    return date(season, *SEASON_START), date(season, *SEASON_END)


def bootstrap(session: Session) -> None:
    """Seed a status row for every category, including unconfigured ones."""
    seed_source_status(session, configured_categories())
    session.commit()


def ingest_season(
    session: Session, season: int, chunk_days: int = 14, with_boxscores: bool = True
) -> dict[str, int]:
    """Backfill one season's schedule, results and boxscores."""
    start, end = season_bounds(season)
    today = utcnow().date()
    end = min(end, today)
    if start > end:
        log.info("ingest.season.skipped", season=season, reason="season has not started")
        return {"games": 0, "boxscores": 0}

    run_reference_ingest(session, season)
    session.commit()

    totals = {"games": 0, "boxscores": 0, "player_lines": 0}
    with job_run(session, "ingest_season", season=season) as run:
        cursor = start
        while cursor <= end:
            chunk_end = min(cursor + timedelta(days=chunk_days - 1), end)
            counts = ingest_schedule_range(session, cursor, chunk_end)
            totals["games"] += counts["games"]
            session.commit()
            log.info(
                "ingest.season.chunk",
                season=season,
                start=cursor.isoformat(),
                end=chunk_end.isoformat(),
                games=counts["games"],
            )
            cursor = chunk_end + timedelta(days=1)
        run.rows_written = totals["games"]

    if with_boxscores:
        counts = run_results_ingest(session)
        totals["boxscores"] = counts["games"]
        totals["player_lines"] = counts["player_lines"]
        session.commit()

    return totals


def daily_refresh(session: Session) -> dict[str, int]:
    """Schedule window + any missing boxscores. Safe to run repeatedly."""
    schedule_counts = run_schedule_ingest(session)
    session.commit()
    result_counts = run_results_ingest(session)
    session.commit()
    return {
        "games": schedule_counts["games"],
        "boxscores": result_counts["games"],
        "player_lines": result_counts["player_lines"],
    }
