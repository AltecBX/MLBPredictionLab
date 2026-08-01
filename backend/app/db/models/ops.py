"""Operational tables: source status, raw payload audit trail, job runs."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DataSourceStatus(Base):
    """Per (source, category) health and freshness. Drives the diagnostics screen."""

    __tablename__ = "data_source_status"
    __table_args__ = (
        UniqueConstraint("source_name", "category", name="uq_source_category"),
        CheckConstraint("status IN ('OK','DEGRADED','UNAVAILABLE')", name="ck_dss_status"),
        CheckConstraint(
            "freshness IN ('FRESH','AGING','STALE','UNAVAILABLE')", name="ck_dss_freshness"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_name: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="UNAVAILABLE")
    freshness: Mapped[str] = mapped_column(String(16), nullable=False, default="UNAVAILABLE")
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_last_run: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class RawSourcePayload(Base):
    """Verbatim provider responses, deduplicated by content hash."""

    __tablename__ = "raw_source_payloads"
    __table_args__ = (
        UniqueConstraint("source_name", "endpoint", "content_hash", name="uq_raw_payload"),
        Index("ix_raw_retrieved", "retrieved_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_name: Mapped[str] = mapped_column(String(64), nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    request_params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    knowledge_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class JobRun(Base):
    __tablename__ = "job_runs"
    __table_args__ = (
        Index("ix_job_runs_name_started", "job_name", "started_at"),
        CheckConstraint("status IN ('RUNNING','SUCCESS','FAILED')", name="ck_job_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_name: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="RUNNING")
    rows_written: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


__all__ = ["DataSourceStatus", "RawSourcePayload", "JobRun"]
