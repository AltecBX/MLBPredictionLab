"""Injured-list moves, from the transactions feed.

The `injuries` table has been schema-complete and empty since Phase 1. The data
exists: MLB's transactions endpoint carries every roster move including
injured-list placements and activations, and it is the same API this repository
already ingests schedules and box scores from.

**What counts as an injury here.** The feed is every transaction — signings,
options, selections, minor-league assignments. Only injured-list placements and
the activations that end them are kept, matched on the description text because
the feed has no dedicated injury type code. A move that cannot be classified is
skipped rather than stored under a guessed status.

**When it becomes knowable, and why that is deliberately late.** A transaction
carries a `date` but no time of day. A placement announced on the morning of
the 14th and one announced after that night's game are the same row in this
feed, and only one of them is knowable before a 7pm first pitch.

So `knowledge_time` is **midnight UTC at the end of the transaction's date** —
the move is treated as unknowable until the day is over. That is conservative
in the safe direction and it costs something real: an injury announced at noon
cannot inform that evening's prediction, though in production it would. The
alternative is a same-day feature that reads a move which may have been
announced after the game started, which is the failure this repository exists
to prevent. LEAKAGE_PREVENTION.md's rule is that a fact is knowable when it was
*published*, and when publication time is unknown the conservative bound is the
only honest one.

**No injury feature is registered.** This lands the data and nothing more. A
feature built on it goes through `run_ablation` like every other candidate, and
five groups have been rejected there.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.logging import get_logger
from app.db.models import Injury, Player, Team
from app.db.upsert import upsert
from app.ingestion.status import job_run, record_source_status
from app.providers.base import DataCategory
from app.providers.mlb_statsapi.client import SOURCE_NAME, MlbStatsApiClient

log = get_logger(__name__)

#: Placement onto an injured list. The number of days varies (7, 10, 15, 60)
#: and the phrasing has changed over the years — "disabled list" before 2019.
PLACED = re.compile(
    r"placed\s+.*?\bon\s+the\s+(?P<days>\d+)[- ]day\s+(?P<list>injured|disabled)\s+list",
    re.IGNORECASE,
)
#: The other end of the same interval.
ACTIVATED = re.compile(
    r"activated\s+.*?\s+from\s+the\s+(?:\d+[- ]day\s+)?(?:injured|disabled)\s+list",
    re.IGNORECASE,
)
#: Body part, when the description volunteers one. It often does not, and a
#: missing body part is left null rather than inferred from the player's
#: position or from anything else.
BODY_PART = re.compile(
    r"\b(?:right|left)?\s*(elbow|shoulder|knee|hamstring|oblique|back|wrist|ankle|"
    r"forearm|calf|groin|hip|thumb|finger|hand|foot|neck|quad|lat|triceps|biceps|"
    r"rib|toe|abdominal|concussion)\b",
    re.IGNORECASE,
)


def _knowledge_time(transaction_date: date) -> datetime:
    """End of the transaction's day, in UTC. See the module docstring."""
    return datetime(
        transaction_date.year, transaction_date.month, transaction_date.day, tzinfo=UTC
    ) + timedelta(days=1)


def classify(description: str) -> tuple[str, int | None] | None:
    """('IL', days) for a placement, ('ACTIVE', None) for an activation, else None."""
    placed = PLACED.search(description or "")
    if placed:
        try:
            return "IL", int(placed.group("days"))
        except (TypeError, ValueError):
            return "IL", None
    if ACTIVATED.search(description or ""):
        return "ACTIVE", None
    return None


def body_part(description: str) -> str | None:
    match = BODY_PART.search(description or "")
    return match.group(1).lower() if match else None


def _rows_from_transactions(
    payload: dict[str, Any],
    known_players: set[int],
    known_teams: set[int],
    retrieved_at: datetime,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in payload.get("transactions") or []:
        person = item.get("person") or {}
        player_id = person.get("id")
        # A foreign key to a player we have never ingested would fail the
        # insert, and most of these are minor leaguers this database has no
        # reason to know about.
        if player_id is None or int(player_id) not in known_players:
            continue

        description = item.get("description") or ""
        classified = classify(description)
        if classified is None:
            continue
        status, days = classified

        raw_date = item.get("effectiveDate") or item.get("date")
        try:
            effective = date.fromisoformat(str(raw_date)[:10])
        except (TypeError, ValueError):
            continue

        # A major-league player rehabbing with an affiliate carries that
        # affiliate's team id, which is not a team this database knows. Null
        # rather than a foreign key to nothing — the move is still a real fact
        # about the player.
        team = (item.get("toTeam") or item.get("fromTeam") or {}).get("id")
        if team is not None and int(team) not in known_teams:
            team = None
        rows.append(
            {
                "player_id": int(player_id),
                "team_id": int(team) if team else None,
                "status": status,
                "description": description[:500],
                "body_part": body_part(description),
                "effective_from": datetime(
                    effective.year, effective.month, effective.day, tzinfo=UTC
                ),
                "effective_to": None,
                "expected_return": (
                    effective + timedelta(days=days) if status == "IL" and days else None
                ),
                "source_name": SOURCE_NAME,
                "retrieved_at": retrieved_at,
                "knowledge_time": _knowledge_time(
                    date.fromisoformat(str(item.get("date") or raw_date)[:10])
                ),
            }
        )
    return rows


def ingest_injuries(
    session: Session,
    start: date,
    end: date,
    client: MlbStatsApiClient | None = None,
    chunk_days: int = 30,
) -> int:
    """Injured-list moves between two dates, written bitemporally.

    Chunked because the endpoint returns every transaction in the window and a
    full season in one request is a large payload for no benefit.
    """
    owned = client is None
    client = client or MlbStatsApiClient()
    written = 0

    try:
        with job_run(
            session, "ingest_injuries", start=start.isoformat(), end=end.isoformat()
        ) as run:
            known_players = {
                row[0] for row in session.execute(select(Player.id)).all()
            }
            known_teams = {row[0] for row in session.execute(select(Team.id)).all()}
            if not known_players:
                log.warning("injuries.no_players")
                return 0

            retrieved_at = utcnow()
            cursor = start
            while cursor <= end:
                chunk_end = min(cursor + timedelta(days=chunk_days - 1), end)
                try:
                    payload = client.get(
                        "/transactions",
                        {
                            "startDate": cursor.isoformat(),
                            "endDate": chunk_end.isoformat(),
                        },
                    )
                except Exception as exc:  # noqa: BLE001 - one window must not stop the run
                    log.warning(
                        "injuries.chunk_failed", start=cursor.isoformat(), error=str(exc)
                    )
                    cursor = chunk_end + timedelta(days=1)
                    continue

                rows = _rows_from_transactions(
                    payload, known_players, known_teams, retrieved_at
                )
                if rows:
                    upsert(
                        session, Injury, rows,
                        ["player_id", "effective_from", "status"],
                    )
                    written += len(rows)
                cursor = chunk_end + timedelta(days=1)

            run.rows_written = written
            # See the note in `lineup_poller`.
            record_source_status(
                session, SOURCE_NAME, DataCategory.INJURIES,
                success=True, records=written,
                detail="Injured-list moves from the transactions feed.",
            )
            log.info("injuries.ingested", written=written)
    finally:
        if owned:
            client.close()
    return written


__all__ = ["body_part", "classify", "ingest_injuries"]
