"""Statcast ingestion: fetch, normalize, store, reconcile.

Resumable in the same way the box-score backfill is: `pending_statcast_dates`
returns dates that have final games but no stored pitches, so an interrupted
run picks up where it stopped and a finished date is never refetched.

The reconciliation at the end is the part that matters. Savant and the MLB
Stats API are two independent sources describing the same games, so their
counts must agree — pitches thrown, strikeouts, home runs. When they do not,
the game is recorded as a discrepancy rather than quietly stored, because a
feature built on a game that is missing a third of its pitches is worse than no
feature at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import BattedBallEvent, Game, Pitch, Player, TeamGameStat
from app.db.upsert import upsert
from app.ingestion.reference import ingest_players
from app.ingestion.status import apply_provider_result, job_run
from app.providers.baseball_savant.mappers import (
    batted_ball_rows,
    knowledge_time_for,
    pitch_rows,
)
from app.providers.registry import get_statcast_provider

log = get_logger(__name__)

# Once automatic balls and strikes are excluded (they are awarded without a
# pitch, so `is_pitch` is false), the two sources agree exactly: 0 of the first
# 30 games reconciled differ at all, against 14 of 30 when every row is counted.
# The tolerance therefore exists only to absorb a later scoring correction, and
# is small enough that a structural problem still trips it.
PITCH_COUNT_TOLERANCE = 0.005
# Strikeouts and home runs are discrete and unambiguous. Any gap is a real
# problem, so the tolerance is a single event to absorb a scoring correction.
EVENT_TOLERANCE = 1
# How many examples of a repeated problem the job-run record keeps.
DETAIL_SAMPLE = 50


@dataclass(slots=True)
class Discrepancy:
    game_id: int
    check: str
    statcast: float
    reference: float

    @property
    def gap(self) -> float:
        return abs(self.statcast - self.reference)

    def describe(self) -> str:
        return (
            f"game {self.game_id}: {self.check} statcast={self.statcast:g} "
            f"boxscore={self.reference:g} gap={self.gap:g}"
        )


@dataclass(slots=True)
class IngestResult:
    dates: int = 0
    pitches: int = 0
    batted_balls: int = 0
    games: int = 0
    players_resolved: int = 0
    rejected_unknown_game: int = 0
    rejected_missing_ids: int = 0
    rejected_unknown_player: int = 0
    empty_dates: list[str] = field(default_factory=list)
    discrepancies: list[Discrepancy] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dates": self.dates,
            "pitches": self.pitches,
            "batted_balls": self.batted_balls,
            "games": self.games,
            "players_resolved": self.players_resolved,
            "rejected_unknown_game": self.rejected_unknown_game,
            "rejected_missing_ids": self.rejected_missing_ids,
            "rejected_unknown_player": self.rejected_unknown_player,
            "empty_dates": self.empty_dates[:DETAIL_SAMPLE],
            "discrepancy_count": len(self.discrepancies),
            # Capped: this lands in a JSONB job_runs.details column, and a run
            # that went badly wrong should not write a megabyte describing it.
            "discrepancies": [d.describe() for d in self.discrepancies[:DETAIL_SAMPLE]],
        }


def pending_statcast_dates(
    session: Session, start: date, end: date, limit: int | None = None
) -> list[date]:
    """Dates in range where some final regular-season game still has no pitches.

    Date-level rather than game-level because one Savant request covers a whole
    date. A date counts as done only when *every* final game on it has pitches,
    so an interrupted run resumes correctly; refetching a partially ingested
    date is idempotent thanks to `uq_pitch`.

    A date whose games predate Statcast tracking has no rows to find and so
    stays pending forever. That is why the CLI takes an explicit range rather
    than walking all of history: the caller says which seasons are tracked.
    """
    has_pitches = select(Pitch.id).where(Pitch.game_id == Game.id).exists()
    stmt = (
        select(Game.official_date)
        .where(
            Game.official_date.between(start, end),
            Game.is_final.is_(True),
            Game.game_type == "R",
        )
        .group_by(Game.official_date)
        .having(func.count().filter(has_pitches) < func.count())
        .order_by(Game.official_date)
    )
    if limit:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt))


def _game_context(session: Session, day: date) -> tuple[set[int], dict[int, datetime]]:
    """Known game ids for a date, and when each game's data became knowable."""
    rows = session.execute(
        select(Game.id, Game.game_date_utc, Game.game_end_utc).where(
            Game.official_date == day, Game.game_type == "R"
        )
    ).all()
    ids = {r.id for r in rows}
    knowledge = {r.id: knowledge_time_for(r.game_end_utc, r.game_date_utc) for r in rows}
    return ids, knowledge


def _resolve_players(session: Session, wanted: set[int]) -> tuple[set[int], int]:
    """Make sure every player in the export exists, and say which now do.

    A Statcast export routinely names a player the box-score ingest has not
    stored yet — a September callup whose first appearance we have not walked
    to. Fetching the master record is one request to the same MLB Stats API the
    rest of the reference data comes from, and it is skipped entirely when
    nothing is missing. Dropping those pitches instead would silently thin the
    sample for exactly the players we know least about.
    """
    known = set(
        session.scalars(select(Player.id).where(Player.id.in_(wanted)))
    )
    missing = sorted(wanted - known)
    if not missing:
        return known, 0

    added = ingest_players(session, missing)
    session.flush()
    known = set(session.scalars(select(Player.id).where(Player.id.in_(wanted))))
    return known, added


# Savant's `events` values that the box score counts as a strikeout. A batter
# who reaches on an uncaught third strike is still charged one, which is why
# `strikeout_double_play` and the dropped-third-strike variants are here.
STRIKEOUT_EVENTS = ("strikeout", "strikeout_double_play", "strikeout_triple_play")
HOME_RUN_EVENTS = ("home_run",)


def reconcile_game(session: Session, game_id: int) -> list[Discrepancy]:
    """Cross-check stored Statcast against the box score already ingested.

    Two independent sources describing the same game: Savant's pitch export and
    the MLB Stats API box score that was ingested months earlier. Pitch counts,
    strikeouts and home runs must agree; a gap means one of them is wrong, and a
    feature built on a game missing a third of its pitches is worse than no
    feature at all.

    Counts come from `pa_event`, Savant's own plate-appearance outcome, rather
    than from a rules engine over `description`. The pitch count is over
    `is_pitch` rows only, because the box score counts pitches thrown and Savant
    also emits a row for each ball or strike awarded without one. Returns an
    empty list when the box score has no reference value — an unmeasured check
    is not a passed one, but it is also not a discrepancy.
    """
    reference = session.execute(
        select(
            func.sum(TeamGameStat.pitches_thrown),
            func.sum(TeamGameStat.strikeouts_pitched),
            func.sum(TeamGameStat.home_runs_allowed),
            # Balls in play, from the batting lines: every at-bat that did not
            # end in a strikeout put a ball in play, and the two sacrifice
            # categories are batted balls that are not at-bats.
            func.sum(TeamGameStat.at_bats)
            - func.sum(TeamGameStat.strikeouts)
            + func.sum(TeamGameStat.sac_flies)
            + func.sum(TeamGameStat.sac_bunts),
        ).where(TeamGameStat.game_id == game_id)
    ).one()
    ref_pitches, ref_k, ref_hr, ref_bip = (
        float(v) if v is not None else None for v in reference
    )

    def _count(*where: Any) -> float:
        return float(
            session.scalar(
                select(func.count()).select_from(Pitch).where(Pitch.game_id == game_id, *where)
            )
            or 0
        )

    sc_pitches = _count(Pitch.is_pitch.is_(True))
    sc_k = _count(Pitch.pa_event.in_(STRIKEOUT_EVENTS))
    sc_hr = _count(Pitch.pa_event.in_(HOME_RUN_EVENTS))

    out: list[Discrepancy] = []

    if ref_pitches:
        gap = abs(sc_pitches - ref_pitches) / ref_pitches
        if gap >= PITCH_COUNT_TOLERANCE:
            out.append(Discrepancy(game_id, "pitch_count", sc_pitches, ref_pitches))

    for check, statcast, expected in (
        ("strikeouts", sc_k, ref_k),
        ("home_runs", sc_hr, ref_hr),
    ):
        if expected is not None and abs(statcast - expected) > EVENT_TOLERANCE:
            out.append(Discrepancy(game_id, check, statcast, expected))

    # The batted-ball table is the one every contact-quality feature reads, so
    # it gets its own check against the batting lines rather than inheriting the
    # pitch table's. Balls in play are countable exactly from both sources.
    if ref_bip is not None:
        sc_bip = float(
            session.scalar(
                select(func.count())
                .select_from(BattedBallEvent)
                .where(BattedBallEvent.game_id == game_id)
            )
            or 0
        )
        if abs(sc_bip - ref_bip) > EVENT_TOLERANCE:
            out.append(Discrepancy(game_id, "balls_in_play", sc_bip, ref_bip))

    return out


def ingest_statcast_range(
    session: Session,
    start: date,
    end: date,
    limit_dates: int | None = None,
    reconcile: bool = True,
) -> IngestResult:
    """Backfill Statcast for every pending date in range."""
    provider = get_statcast_provider()
    if not hasattr(provider, "fetch_statcast_range"):
        raise RuntimeError(
            "No Statcast provider is configured. Set STATCAST_PROVIDER=baseball_savant. "
            "Nothing is estimated in its absence."
        )

    days = pending_statcast_dates(session, start, end, limit_dates)
    result = IngestResult()
    if not days:
        log.info("statcast.nothing_pending", start=str(start), end=str(end))
        return result

    with job_run(
        session, "ingest_statcast", start=str(start), end=str(end), dates=len(days)
    ) as run:
        for day in days:
            game_ids, knowledge = _game_context(session, day)
            if not game_ids:
                continue

            fetched = provider.fetch_statcast_range(day, day, season=day.year)
            apply_provider_result(
                session, fetched, records=0 if fetched.data is None else len(fetched.data)
            )
            if not fetched.ok or fetched.data is None or fetched.data.empty:
                log.warning("statcast.empty_date", date=str(day), message=fetched.message)
                result.empty_dates.append(day.isoformat())
                session.commit()
                continue

            pitches, pitch_counts = pitch_rows(
                fetched.data, game_ids, knowledge, provider.name, fetched.retrieved_at
            )
            balls, ball_counts = batted_ball_rows(
                fetched.data, game_ids, knowledge, provider.name, fetched.retrieved_at
            )

            wanted = {p["pitcher_id"] for p in pitches} | {p["batter_id"] for p in pitches}
            wanted |= {b["pitcher_id"] for b in balls} | {b["batter_id"] for b in balls}
            known_players, added = _resolve_players(session, wanted)
            result.players_resolved += added

            # A row naming a player the reference API could not resolve either
            # would violate the foreign key. Drop and count it rather than
            # failing the date, so one unresolvable id cannot block a day.
            before = len(pitches)
            pitches = [
                p
                for p in pitches
                if p["pitcher_id"] in known_players and p["batter_id"] in known_players
            ]
            balls = [
                b
                for b in balls
                if b["pitcher_id"] in known_players and b["batter_id"] in known_players
            ]
            result.rejected_unknown_player += before - len(pitches)

            if pitches:
                upsert(
                    session,
                    Pitch,
                    pitches,
                    ["game_id", "at_bat_index", "pitch_number"],
                )
            if balls:
                upsert(
                    session,
                    BattedBallEvent,
                    balls,
                    ["game_id", "at_bat_index", "pitch_number"],
                )
            session.commit()

            result.dates += 1
            result.pitches += len(pitches)
            result.batted_balls += len(balls)
            result.rejected_unknown_game += pitch_counts["unknown_game"]
            result.rejected_missing_ids += pitch_counts["missing_ids"]

            touched = {p["game_id"] for p in pitches}
            result.games += len(touched)
            if reconcile:
                for game_id in sorted(touched):
                    result.discrepancies.extend(reconcile_game(session, game_id))

            log.info(
                "statcast.date_done",
                date=str(day),
                pitches=len(pitches),
                batted_balls=len(balls),
                skipped_balls=ball_counts["skipped"],
                games=len(touched),
                discrepancies=len(result.discrepancies),
            )

        run.rows_written = result.pitches
        run.details = result.as_dict()

    if result.discrepancies:
        log.warning(
            "statcast.discrepancies",
            count=len(result.discrepancies),
            sample=[d.describe() for d in result.discrepancies[:5]],
        )
    return result


def season_bounds_for_statcast(season: int) -> tuple[date, date]:
    """Regular-season window, clipped to today."""
    from app.core.clock import utcnow

    start, end = date(season, 3, 1), date(season, 11, 15)
    return start, min(end, utcnow().date() - timedelta(days=1))


__all__ = [
    "EVENT_TOLERANCE",
    "HOME_RUN_EVENTS",
    "PITCH_COUNT_TOLERANCE",
    "STRIKEOUT_EVENTS",
    "Discrepancy",
    "IngestResult",
    "ingest_statcast_range",
    "pending_statcast_dates",
    "reconcile_game",
    "season_bounds_for_statcast",
]
