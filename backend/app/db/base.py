"""Declarative base and shared column mixins."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SourcedMixin:
    """Attribution columns required on every externally-sourced table.

    ``knowledge_time`` is the earliest wall-clock time the fact could have been
    known; it is the ONLY timestamp feature queries may filter on.
    ``retrieved_at`` is operational metadata (DATA_SOURCES.md §4).
    """

    source_name: Mapped[str] = mapped_column(String(64), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    knowledge_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
