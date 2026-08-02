"""Source status, freshness classification and job-run bookkeeping."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import DataSourceStatus, JobRun, RawSourcePayload
from app.providers.base import DataCategory, ProviderResult, ProviderStatus

log = get_logger(__name__)

# Freshness thresholds per category (ARCHITECTURE.md §7): (fresh_max, aging_max)
FRESHNESS_THRESHOLDS: dict[str, tuple[timedelta, timedelta]] = {
    DataCategory.SCHEDULE: (timedelta(hours=1), timedelta(hours=6)),
    DataCategory.PROBABLE_PITCHERS: (timedelta(hours=2), timedelta(hours=12)),
    DataCategory.LINEUPS: (timedelta(minutes=15), timedelta(minutes=60)),
    DataCategory.INJURIES: (timedelta(hours=6), timedelta(hours=24)),
    DataCategory.WEATHER: (timedelta(hours=1), timedelta(hours=3)),
    DataCategory.PLAYER_STATS: (timedelta(hours=12), timedelta(hours=36)),
    DataCategory.BULLPEN_USAGE: (timedelta(hours=6), timedelta(hours=24)),
    DataCategory.BULLPEN_AVAILABILITY: (timedelta(hours=6), timedelta(hours=24)),
    DataCategory.ODDS: (timedelta(minutes=10), timedelta(hours=1)),
    DataCategory.RESULTS: (timedelta(hours=6), timedelta(hours=24)),
    DataCategory.REFERENCE: (timedelta(hours=36), timedelta(days=7)),
    DataCategory.STATCAST: (timedelta(hours=24), timedelta(days=3)),
    DataCategory.PARK_FACTORS: (timedelta(days=30), timedelta(days=365)),
}

DEFAULT_THRESHOLD = (timedelta(hours=6), timedelta(hours=24))


def classify_freshness(category: str, last_success_at: datetime | None,
                       now: datetime | None = None) -> str:
    if last_success_at is None:
        return "UNAVAILABLE"
    now = now or utcnow()
    if last_success_at.tzinfo is None:
        last_success_at = last_success_at.replace(tzinfo=UTC)
    age = now - last_success_at
    fresh_max, aging_max = FRESHNESS_THRESHOLDS.get(category, DEFAULT_THRESHOLD)
    if age <= fresh_max:
        return "FRESH"
    if age <= aging_max:
        return "AGING"
    return "STALE"


def record_source_status(
    session: Session,
    source_name: str,
    category: str,
    *,
    success: bool,
    records: int | None = None,
    error: str | None = None,
    detail: str | None = None,
    status_override: str | None = None,
) -> None:
    now = utcnow()
    existing = session.scalar(
        select(DataSourceStatus).where(
            DataSourceStatus.source_name == source_name,
            DataSourceStatus.category == category,
        )
    )
    if existing is None:
        # Take over the bootstrap placeholder rather than inserting beside it.
        #
        # `seed_source_status` writes one row per category at bootstrap named
        # `unavailable::<category>`, and this function matches on
        # (source_name, category) — so the first real ingest for a category
        # inserted a SECOND row and left the placeholder in place.
        #
        # That is worse than the stale row it was meant to replace.
        # `freshness_report` builds a dict keyed by category, so with two rows
        # the one a reader sees is whichever the database happened to return
        # last. Observed live: lineups showed OK and weather showed UNAVAILABLE
        # on the same page, from the same code, in the same run.
        placeholder = session.scalar(
            select(DataSourceStatus).where(
                DataSourceStatus.category == category,
                DataSourceStatus.source_name.startswith("unavailable::"),
            )
        )
        if placeholder is not None:
            placeholder.source_name = source_name
            existing = placeholder
        else:
            existing = DataSourceStatus(source_name=source_name, category=category)
            session.add(existing)

    if success:
        existing.last_success_at = now
        existing.consecutive_failures = 0
        existing.last_error = None
        existing.status = status_override or "OK"
        existing.records_last_run = records
    else:
        existing.last_failure_at = now
        existing.consecutive_failures = (existing.consecutive_failures or 0) + 1
        existing.last_error = error
        existing.status = status_override or (
            "UNAVAILABLE" if existing.consecutive_failures >= 3 else "DEGRADED"
        )

    existing.detail = detail
    existing.freshness = classify_freshness(category, existing.last_success_at, now)
    existing.updated_at = now


def seed_source_status(session: Session, configured: dict[str, str | None]) -> None:
    """Ensure a row exists for every category, including unconfigured ones."""
    for category, provider in configured.items():
        row = session.scalar(
            select(DataSourceStatus).where(DataSourceStatus.category == category)
        )
        if row is not None:
            continue
        session.add(
            DataSourceStatus(
                source_name=provider or f"unavailable::{category}",
                category=category,
                status="UNAVAILABLE",
                freshness="UNAVAILABLE",
                detail=None
                if provider
                else "No provider configured for this category.",
            )
        )


def store_raw_payload(session: Session, result: ProviderResult[Any]) -> None:
    """Persist the verbatim payload, deduplicated by content hash.

    Skipped entirely when STORE_RAW_PAYLOADS is off. Nothing else changes: the
    normalized rows and their knowledge_time are written either way, so the
    model and every prediction are identical. What is given up is the ability
    to replay a normalization bug without refetching — which for a historical
    backfill means refetching from a stable public API, not losing anything.
    """
    if not settings.store_raw_payloads:
        return
    if result.raw_payload is None or result.endpoint is None:
        return
    digest = result.content_hash
    if digest is None:
        return
    stmt = (
        insert(RawSourcePayload)
        .values(
            source_name=result.source_name,
            endpoint=result.endpoint,
            request_params=result.request_params,
            payload=result.raw_payload,
            content_hash=digest,
            retrieved_at=result.retrieved_at,
            knowledge_time=result.knowledge_time,
        )
        .on_conflict_do_nothing(index_elements=["source_name", "endpoint", "content_hash"])
    )
    session.execute(stmt)


def apply_provider_result(
    session: Session, result: ProviderResult[Any], *, records: int | None = None
) -> None:
    """Record status and store the payload for a completed provider call."""
    store_raw_payload(session, result)
    record_source_status(
        session,
        result.source_name,
        result.category,
        success=result.status is not ProviderStatus.UNAVAILABLE,
        records=records,
        error=result.message if result.status is ProviderStatus.UNAVAILABLE else None,
        detail=result.message,
        status_override="DEGRADED" if result.status is ProviderStatus.PARTIAL else None,
    )


@contextmanager
def job_run(session: Session, job_name: str, **details: Any) -> Iterator[JobRun]:
    """Track a job execution. Failures are recorded, then re-raised."""
    started = utcnow()
    run = JobRun(
        job_name=job_name, status="RUNNING", started_at=started, details=details or {}
    )
    session.add(run)
    session.flush()
    try:
        yield run
    except Exception as exc:
        # The transaction is poisoned; roll back before recording the failure so
        # the failure row itself is not lost to the same aborted transaction.
        session.rollback()
        failure = JobRun(
            job_name=job_name,
            status="FAILED",
            error=f"{type(exc).__name__}: {exc}"[:4000],
            started_at=started,
            finished_at=utcnow(),
            details=details or {},
        )
        session.add(failure)
        session.commit()
        log.error("job.failed", job=job_name, error=str(exc))
        raise
    else:
        run.status = "SUCCESS"
        run.finished_at = utcnow()
        session.flush()
        log.info(
            "job.succeeded",
            job=job_name,
            rows=run.rows_written,
            duration_ms=int((utcnow() - started).total_seconds() * 1000),
        )
