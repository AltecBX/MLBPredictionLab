"""Raw payload retention.

Runs against a throwaway PostgreSQL database — `raw_source_payloads` uses JSONB
and a partial-index-free unique constraint that SQLite cannot stand in for. The
module skips rather than silently passing if no database is reachable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.base import Base
from app.db.models import RawSourcePayload
from app.ingestion.maintenance import prune_raw_payloads

TEST_DB = "jerry_mlb_maint_test"


@pytest.fixture(scope="module")
def engine():
    admin_url = str(settings.database_url).rsplit("/", 1)[0] + "/postgres"
    try:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
        admin.dispose()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"PostgreSQL is not reachable: {exc}")

    eng = create_engine(str(settings.database_url).rsplit("/", 1)[0] + f"/{TEST_DB}")
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


def _payload(session, *, days_ago: int, digest: str) -> None:
    fetched = datetime.now(UTC) - timedelta(days=days_ago)
    session.add(
        RawSourcePayload(
            source_name="mlb_statsapi",
            endpoint="/schedule",
            request_params={"date": "2024-04-01"},
            payload={"synthetic": True, "note": "test fixture, never served"},
            content_hash=digest,
            retrieved_at=fetched,
            # Deliberately *newer* than the retrieval cutoff, to prove the prune
            # keys off retrieved_at and never off knowledge_time.
            knowledge_time=datetime.now(UTC),
        )
    )


def test_prune_deletes_only_payloads_past_the_retention_window(engine):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        _payload(session, days_ago=200, digest="a" * 64)
        _payload(session, days_ago=120, digest="b" * 64)
        _payload(session, days_ago=10, digest="c" * 64)
        _payload(session, days_ago=0, digest="d" * 64)
        session.commit()

        result = prune_raw_payloads(session, older_than_days=90)

        assert result == {"deleted": 2, "remaining": 2}
        survivors = set(
            session.scalars(select(RawSourcePayload.content_hash)).all()
        )
        assert survivors == {"c" * 64, "d" * 64}


def test_prune_is_idempotent_and_safe_on_an_empty_archive(engine):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        session.execute(text("TRUNCATE raw_source_payloads"))
        session.commit()

        first = prune_raw_payloads(session, older_than_days=90)
        second = prune_raw_payloads(session, older_than_days=90)

        assert first == {"deleted": 0, "remaining": 0}
        assert second == first


def test_prune_records_a_job_run(engine):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        session.execute(text("TRUNCATE raw_source_payloads"))
        _payload(session, days_ago=365, digest="e" * 64)
        session.commit()

        prune_raw_payloads(session, older_than_days=30)

        row = session.execute(
            text(
                "SELECT status, rows_written, details FROM job_runs"
                " WHERE job_name = 'prune_raw_payloads' ORDER BY id DESC LIMIT 1"
            )
        ).one()
        assert row.status == "SUCCESS"
        assert row.rows_written == 1
        assert row.details["deleted"] == 1


def test_default_retention_comes_from_settings(engine):
    """The configured bound is the one actually enforced, not a hard-coded 90."""
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        session.execute(text("TRUNCATE raw_source_payloads"))
        _payload(session, days_ago=settings.raw_payload_retention_days + 5, digest="f" * 64)
        _payload(session, days_ago=max(settings.raw_payload_retention_days - 5, 0), digest="g" * 64)
        session.commit()

        prune_raw_payloads(session)

        assert session.scalar(select(func.count()).select_from(RawSourcePayload)) == 1
