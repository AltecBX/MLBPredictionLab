"""Storage maintenance for the raw payload archive.

Every provider response is stored verbatim so a normalization bug can be
replayed without refetching, and so any number on the site can be traced back
to the bytes it came from. That archive is by far the largest thing in the
database — on a four-season backfill it is roughly three quarters of the total —
and it grows without bound unless something removes the old rows.

`RAW_PAYLOAD_RETENTION_DAYS` is that bound. Pruning is safe: nothing references
`raw_source_payloads`, and every fact derived from a payload was normalized into
its own table at ingest time with its own `knowledge_time`. Deleting a payload
costs replayability for that fetch, never correctness of a prediction.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import RawSourcePayload
from app.ingestion.status import job_run

log = get_logger(__name__)


def prune_raw_payloads(
    session: Session, older_than_days: int | None = None
) -> dict[str, int]:
    """Delete stored payloads retrieved before the retention cutoff.

    Cut on `retrieved_at`, not `knowledge_time`: this is an operational storage
    decision about when we fetched something, and it must never be confused
    with the as-of logic that decides what a prediction was allowed to know.
    """
    days = older_than_days if older_than_days is not None else settings.raw_payload_retention_days
    cutoff = utcnow() - timedelta(days=days)

    with job_run(session, "prune_raw_payloads", retention_days=days) as run:
        doomed = (
            session.scalar(
                select(func.count())
                .select_from(RawSourcePayload)
                .where(RawSourcePayload.retrieved_at < cutoff)
            )
            or 0
        )
        if doomed:
            session.execute(
                delete(RawSourcePayload).where(RawSourcePayload.retrieved_at < cutoff)
            )
        remaining = (
            session.scalar(select(func.count()).select_from(RawSourcePayload)) or 0
        )
        run.rows_written = doomed
        run.details = {
            "deleted": doomed,
            "remaining": remaining,
            "cutoff": cutoff.isoformat(),
        }

    session.commit()
    log.info(
        "maintenance.prune_raw_payloads",
        deleted=doomed,
        remaining=remaining,
        retention_days=days,
    )
    return {"deleted": doomed, "remaining": remaining}


__all__ = ["prune_raw_payloads"]
