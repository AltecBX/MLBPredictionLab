"""Posted lineups, polled before first pitch.

IMPLEMENTATION_PLAN.md § "Why step 3's confirmed half is still not next"
measured the problem precisely: every lineup in this database comes from a
completed game's box score and carries `knowledge_time = first pitch + 3h30m`.
All 188,604 of them. Zero are knowable before the game they describe, so a
lineup feature cannot enter a pregame snapshot at all, and building one against
backfilled box-score lineups would produce an improvement that could not be
reproduced live.

This is the missing half. MLB posts lineups an hour or two before first pitch
and the schedule endpoint hydrates them, so a poller can capture the batting
order **as it was posted**, with `knowledge_time` set to the moment it was
observed. That is a genuinely pregame fact, and it is the substrate step 3's
confirmed half was waiting on.

**What makes this leak-free where the backfill is not.** `knowledge_time` is
`utcnow()` at the moment of the poll — not the game date, not first pitch. A
lineup captured at 17:40 for a 19:05 game is knowable from 17:40, and a
prediction made at 17:00 cannot see it. Because the table is append-only on
`(game_id, team_id, batting_order, knowledge_time)`, a lineup that changes
between polls produces a second snapshot rather than overwriting the first, and
the history of what was known when survives.

**It cannot rewrite history.** A poll only ever writes rows stamped with the
present, so running it can never make a *past* game's lineup retroactively
knowable. That is the property that keeps the walk-forward honest while this
data accumulates.

The consequence worth stating plainly: this data starts from the day the poller
first runs. A confirmed-lineup feature is not measurable on 2024 or 2025 and
will not be for a season. What this lands is the collection, not the feature.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.logging import get_logger
from app.db.models import Game, Lineup, Player, Team
from app.db.upsert import upsert
from app.ingestion.status import job_run, record_source_status
from app.providers.base import DataCategory
from app.providers.mlb_statsapi.client import SOURCE_NAME, MlbStatsApiClient

log = get_logger(__name__)

#: Only games whose first pitch is still ahead of us are polled. A lineup read
#: after first pitch is the box score arriving by another route, and it would
#: land with a knowledge_time that makes it look pregame when it is not.
POLL_ONLY_UPCOMING = True


def _lineup_rows(
    game_id: int,
    home_team_id: int,
    away_team_id: int,
    payload: dict[str, Any],
    known_players: set[int],
    observed_at: datetime,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for side, team_id in (("homePlayers", home_team_id), ("awayPlayers", away_team_id)):
        players = payload.get(side) or []
        for order, player in enumerate(players, start=1):
            player_id = player.get("id")
            if player_id is None or int(player_id) not in known_players:
                continue
            rows.append(
                {
                    "game_id": game_id,
                    "team_id": team_id,
                    "player_id": int(player_id),
                    "batting_order": order,
                    "position": (player.get("primaryPosition") or {}).get("abbreviation"),
                    # Posted by the club, so confirmed rather than projected.
                    "is_confirmed": True,
                    "lineup_status": "CONFIRMED",
                    "source_name": SOURCE_NAME,
                    "retrieved_at": observed_at,
                    # The whole point of this module.
                    "knowledge_time": observed_at,
                }
            )
    return rows


def poll_lineups(
    session: Session,
    target: date | None = None,
    client: MlbStatsApiClient | None = None,
) -> int:
    """Capture posted lineups for games that have not started yet.

    Idempotent in the way that matters: a second poll with an unchanged lineup
    writes new rows only if the minute has changed, and an unchanged lineup at a
    later minute is still a true statement about what was known then.
    """
    owned = client is None
    client = client or MlbStatsApiClient()
    target = target or utcnow().date()
    written = 0

    try:
        with job_run(session, "poll_lineups", date=target.isoformat()) as run:
            now = utcnow()
            games = session.execute(
                select(Game.id, Game.home_team_id, Game.away_team_id, Game.game_date_utc)
                .where(Game.official_date == target)
            ).all()
            upcoming = {
                g.id: g
                for g in games
                if not POLL_ONLY_UPCOMING or g.game_date_utc > now
            }
            if not upcoming:
                log.info("lineups.no_upcoming_games", date=target.isoformat())
                return 0

            known_players = {r[0] for r in session.execute(select(Player.id)).all()}
            known_teams = {r[0] for r in session.execute(select(Team.id)).all()}

            payload = client.get(
                "/schedule",
                {"sportId": 1, "date": target.isoformat(), "hydrate": "lineups"},
            )
            for day in payload.get("dates") or []:
                for game in day.get("games") or []:
                    game_id = game.get("gamePk")
                    if game_id is None or int(game_id) not in upcoming:
                        continue
                    lineups = game.get("lineups") or {}
                    if not lineups:
                        continue
                    record = upcoming[int(game_id)]
                    if (
                        record.home_team_id not in known_teams
                        or record.away_team_id not in known_teams
                    ):
                        continue
                    rows = _lineup_rows(
                        int(game_id),
                        record.home_team_id,
                        record.away_team_id,
                        lineups,
                        known_players,
                        now,
                    )
                    if rows:
                        upsert(
                            session, Lineup, rows,
                            ["game_id", "team_id", "batting_order", "knowledge_time"],
                        )
                        written += len(rows)

            run.rows_written = written
            # Without this the Diagnostics row for this category keeps whatever
            # the bootstrap wrote and never learns the poller ran. The
            # categories that report correctly -- schedule, results, reference,
            # player stats -- all record a source result; the three that read a
            # client directly rather than through a ProviderResult did not, and
            # so the deployed site described a working hourly feed as
            # UNAVAILABLE.
            record_source_status(
                session, SOURCE_NAME, DataCategory.LINEUPS,
                success=True, records=written,
                detail="Posted lineups captured pregame, stamped when observed.",
            )
            log.info(
                "lineups.polled",
                date=target.isoformat(),
                games_upcoming=len(upcoming),
                rows=written,
            )
    finally:
        if owned:
            client.close()
    return written


__all__ = ["poll_lineups"]
