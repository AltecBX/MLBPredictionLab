"""Reference ingestion: ballparks, teams, players. Idempotent upserts."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.logging import get_logger
from app.db.upsert import upsert
from app.db.models import Ballpark, Player, Team
from app.ingestion.status import apply_provider_result, job_run
from app.providers.base import ProviderStatus
from app.providers.registry import get_reference_provider

log = get_logger(__name__)


def ingest_ballparks(session: Session, season: int) -> int:
    provider = get_reference_provider()
    result = provider.fetch_venues(season)
    apply_provider_result(session, result, records=len(result.data or []))
    if not result.ok or not result.data:
        log.warning("ingest.ballparks.unavailable", message=result.message)
        return 0
    rows = []
    for venue in result.data:
        row = asdict(venue)
        row.update(
            source_name=result.source_name,
            retrieved_at=result.retrieved_at,
            knowledge_time=result.knowledge_time,
        )
        rows.append(row)
    n = upsert(session, Ballpark, rows, ["id"])
    log.info("ingest.ballparks", count=n, season=season)
    return n


def ingest_teams(session: Session, season: int) -> int:
    provider = get_reference_provider()
    result = provider.fetch_teams(season)
    apply_provider_result(session, result, records=len(result.data or []))
    if not result.ok or not result.data:
        log.warning("ingest.teams.unavailable", message=result.message)
        return 0

    known_venues = {v for (v,) in session.query(Ballpark.id).all()}
    rows = []
    for team in result.data:
        row = asdict(team)
        # Do not create a dangling FK; a venue we have not ingested becomes NULL.
        if row.get("home_venue_id") not in known_venues:
            row["home_venue_id"] = None
        row.update(
            source_name=result.source_name,
            retrieved_at=result.retrieved_at,
            knowledge_time=result.knowledge_time,
        )
        rows.append(row)
    n = upsert(session, Team, rows, ["id"])
    log.info("ingest.teams", count=n, season=season)
    return n


def ingest_players(session: Session, player_ids: list[int]) -> int:
    """Resolve player master records. Only fetches ids not already stored."""
    if not player_ids:
        return 0
    existing = {
        pid for (pid,) in session.query(Player.id).filter(Player.id.in_(player_ids)).all()
    }
    missing = sorted(set(player_ids) - existing)
    if not missing:
        return 0

    provider = get_reference_provider()
    result = provider.fetch_people(missing)
    apply_provider_result(session, result, records=len(result.data or []))
    if result.status is ProviderStatus.UNAVAILABLE or not result.data:
        log.warning("ingest.players.unavailable", message=result.message, requested=len(missing))
        return 0

    rows = []
    for player in result.data:
        row = asdict(player)
        row.update(
            source_name=result.source_name,
            retrieved_at=result.retrieved_at,
            knowledge_time=result.knowledge_time,
        )
        rows.append(row)
    n = upsert(session, Player, rows, ["id"])
    log.info("ingest.players", count=n, requested=len(missing))
    return n


def run_reference_ingest(session: Session, season: int | None = None) -> dict[str, int]:
    season = season or utcnow().year
    with job_run(session, "ingest_reference", season=season) as run:
        parks = ingest_ballparks(session, season)
        teams = ingest_teams(session, season)
        run.rows_written = parks + teams
    return {"ballparks": parks, "teams": teams}
