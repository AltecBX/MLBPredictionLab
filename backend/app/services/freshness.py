"""Per-category freshness reporting.

Freshness is tracked per data category, not globally, because a stale weather
feed and a stale schedule feed have different consequences
(ARCHITECTURE.md §7).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.db.models import DataSourceStatus
from app.ingestion.status import classify_freshness
from app.providers.base import DataCategory
from app.providers.registry import configured_categories

# TTL per category, clamped near first pitch (ARCHITECTURE.md §7).
CACHE_TTL_S: dict[str, int] = {
    DataCategory.SCHEDULE: 300,
    DataCategory.PROBABLE_PITCHERS: 300,
    DataCategory.LINEUPS: 60,
    DataCategory.INJURIES: 600,
    DataCategory.WEATHER: 600,
    DataCategory.PLAYER_STATS: 900,
    DataCategory.BULLPEN_USAGE: 600,
    DataCategory.ODDS: 60,
}
DEFAULT_TTL_S = 300
NEAR_GAME_TTL_S = 60
NEAR_GAME_WINDOW = timedelta(hours=3)

CATEGORY_LABELS = {
    DataCategory.SCHEDULE: "Schedule",
    DataCategory.RESULTS: "Game results",
    DataCategory.PROBABLE_PITCHERS: "Starting pitchers",
    DataCategory.LINEUPS: "Lineups",
    DataCategory.INJURIES: "Injuries",
    DataCategory.WEATHER: "Weather",
    DataCategory.PLAYER_STATS: "Player statistics",
    DataCategory.BULLPEN_USAGE: "Bullpen usage",
    DataCategory.BULLPEN_AVAILABILITY: "Bullpen availability",
    DataCategory.STATCAST: "Statcast metrics",
    DataCategory.PARK_FACTORS: "Park factors",
    DataCategory.ODDS: "Odds",
    DataCategory.REFERENCE: "Teams and ballparks",
}

# Categories surfaced beside a prediction.
PREDICTION_CATEGORIES = [
    DataCategory.SCHEDULE,
    DataCategory.PROBABLE_PITCHERS,
    DataCategory.LINEUPS,
    DataCategory.INJURIES,
    DataCategory.WEATHER,
    DataCategory.PLAYER_STATS,
    DataCategory.BULLPEN_USAGE,
    DataCategory.ODDS,
]


def source_rows(session: Session) -> list[DataSourceStatus]:
    return list(session.scalars(select(DataSourceStatus).order_by(DataSourceStatus.category)))


def freshness_report(session: Session, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or utcnow()
    configured = configured_categories()
    rows = {row.category: row for row in source_rows(session)}

    report: list[dict[str, Any]] = []
    for category in PREDICTION_CATEGORIES:
        row = rows.get(category)
        provider = configured.get(category)
        if row is None:
            report.append(
                {
                    "category": category,
                    "label": CATEGORY_LABELS.get(category, category),
                    "status": "UNAVAILABLE",
                    "freshness": "UNAVAILABLE",
                    "last_success_at": None,
                    "age_seconds": None,
                    "provider": provider,
                    "detail": "No provider configured for this category."
                    if not provider
                    else "This category has not been ingested yet.",
                }
            )
            continue

        freshness = classify_freshness(category, row.last_success_at, now)
        age = (
            int((now - row.last_success_at).total_seconds())
            if row.last_success_at is not None
            else None
        )
        report.append(
            {
                "category": category,
                "label": CATEGORY_LABELS.get(category, category),
                "status": row.status,
                "freshness": freshness,
                "last_success_at": row.last_success_at,
                "age_seconds": age,
                "provider": provider or row.source_name,
                "detail": row.detail
                or (None if provider else "No provider configured for this category."),
                "records_last_run": row.records_last_run,
            }
        )
    return report


def freshness_map(session: Session) -> dict[str, str]:
    return {row["category"]: row["freshness"] for row in freshness_report(session)}


def cache_ttl(category: str, first_pitch: datetime | None = None,
              now: datetime | None = None) -> int:
    ttl = CACHE_TTL_S.get(category, DEFAULT_TTL_S)
    if first_pitch is None:
        return ttl
    now = now or utcnow()
    if first_pitch - now <= NEAR_GAME_WINDOW:
        return min(ttl, NEAR_GAME_TTL_S)
    return ttl


def refresh_freshness(session: Session) -> list[dict[str, Any]]:
    """Recompute stored freshness classes from last-success timestamps."""
    now = utcnow()
    out = []
    for row in source_rows(session):
        row.freshness = classify_freshness(row.category, row.last_success_at, now)
        row.updated_at = now
        out.append(
            {
                "source_name": row.source_name,
                "category": row.category,
                "status": row.status,
                "freshness": row.freshness,
                "last_success_at": row.last_success_at,
                "consecutive_failures": row.consecutive_failures,
            }
        )
    return out
