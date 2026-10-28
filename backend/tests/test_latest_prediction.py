"""One latest prediction per game, whichever model version produced it.

The model is retrained every morning, so every day's prediction has a new model
version. Retiring only the latest snapshot of the SAME version left yesterday's
marked latest beside today's — 381 games by October 2026 — and every reader
keys by game, so which snapshot a page showed was whichever the database
returned last. These run the real migration on a database seeded the way
production was, and pin the guard that stops it recurring.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Game, ModelVersion, Prediction, Team
from app.services.prediction import current_latest, latest_predictions_for_games

BACKEND = Path(__file__).resolve().parents[1]
TEST_DB = "jerry_mlb_latest_test"
BEFORE = "0a9d3c7e5b21"  # the revision before the repair
REPAIR = "7b3e9f1c2d40"


def _url(database: str) -> str:
    return str(settings.database_url).rsplit("/", 1)[0] + f"/{database}"


def _alembic(target: str) -> None:
    env = {**os.environ, "DATABASE_URL": _url(TEST_DB)}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", target],
        cwd=BACKEND, env=env, check=True, capture_output=True,
    )


@pytest.fixture
def engine():
    try:
        admin = create_engine(_url("postgres"), isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"PostgreSQL is not reachable for integration tests: {exc}")
    _alembic(BEFORE)
    engine = create_engine(_url(TEST_DB))
    yield engine
    engine.dispose()
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
    admin.dispose()


NOW = datetime(2026, 10, 28, 20, 0, tzinfo=UTC)
GAME = 849799


def _prediction(version_id: int, created: datetime, p: float, latest: bool, **kw) -> Prediction:
    return Prediction(
        game_id=GAME, model_version_id=version_id, as_of=created, created_at=created,
        home_win_prob=p, away_win_prob=round(1 - p, 5), confidence_score=0.5,
        confidence_label="LOW", recommendation="LEAN", data_completeness=0.9,
        missing_data=[], warnings=[], feature_snapshot={}, component_probs={},
        confidence_components={}, is_latest=latest, **kw,
    )


def _seed(engine) -> dict[str, int]:
    """Three daily model versions, each leaving its snapshot marked latest, and
    one same-version reissue that was retired correctly — production's shape."""
    with Session(engine) as session:
        session.add_all([
            Team(id=10, name="Home Club", abbreviation="HME", source_name="fixture",
                 knowledge_time=NOW),
            Team(id=20, name="Away Club", abbreviation="AWY", source_name="fixture",
                 knowledge_time=NOW),
        ])
        session.flush()
        session.add(Game(
            id=GAME, season=2026, game_type="W", game_date_utc=NOW + timedelta(hours=4),
            official_date=NOW.date(), home_team_id=10, away_team_id=20, is_final=False,
            source_name="fixture", knowledge_time=NOW,
        ))
        versions = []
        for n in (61, 62, 63):
            v = ModelVersion(
                name="jerry_logistic", version=f"v{n}", algorithm="logistic_regression_l2",
                feature_set_version="fs_v9", is_active=n == 63, feature_names=["elo_diff"],
                metrics={}, hyperparameters={}, calibration_params={},
            )
            session.add(v)
            session.flush()
            versions.append(v.id)
        first = _prediction(versions[0], NOW - timedelta(hours=30), 0.70, latest=False)
        session.add(first)
        session.flush()
        older = _prediction(versions[0], NOW - timedelta(hours=19), 0.75647, latest=True)
        session.add(older)
        session.flush()
        first.superseded_by = older.id
        middle = _prediction(versions[1], NOW - timedelta(hours=4), 0.56640, latest=True)
        newest = _prediction(versions[2], NOW, 0.46739, latest=True)
        session.add_all([middle, newest])
        session.commit()
        return {"first": first.id, "older": older.id, "middle": middle.id,
                "newest": newest.id, "v61": versions[0]}


def test_the_repair_keeps_the_newest_snapshot_and_chains_the_rest(engine):
    ids = _seed(engine)
    with Session(engine) as session:
        assert len(session.scalars(select(Prediction).where(Prediction.is_latest)).all()) == 3

    _alembic(REPAIR)

    with Session(engine) as session:
        rows = {p.id: p for p in session.scalars(select(Prediction)).all()}
        assert [i for i, p in rows.items() if p.is_latest] == [ids["newest"]]
        # Each retired snapshot points at the one that followed it.
        assert rows[ids["older"]].superseded_by == ids["middle"]
        assert rows[ids["middle"]].superseded_by == ids["newest"]
        # A link that was already right is left alone.
        assert rows[ids["first"]].superseded_by == ids["older"]
        assert rows[ids["newest"]].superseded_by is None
        # And the readers now see the newest, at 46.7%, not yesterday's 56.6%.
        assert current_latest(session, GAME).id == ids["newest"]
        assert float(latest_predictions_for_games(session, [GAME])[GAME].home_win_prob) == 0.46739


def test_a_second_latest_for_a_game_is_refused_whatever_model_made_it(engine):
    ids = _seed(engine)
    _alembic(REPAIR)
    with Session(engine) as session, pytest.raises(IntegrityError):
        # An older model version, which the per-version index used to allow.
        session.add(_prediction(ids["v61"], NOW + timedelta(hours=1), 0.5, latest=True))
        session.commit()


def test_the_lookup_finds_another_versions_latest():
    """What `generate_prediction` retires is the game's latest, not this
    version's: a per-version lookup is exactly what left the duplicates."""
    source = (BACKEND / "app" / "services" / "prediction.py").read_text(encoding="utf-8")
    start = source.index("def current_latest(")
    body = source[start:source.index("\ndef ", start + 1)]
    assert "model_version_id" not in body.split('"""')[-1]
    assert "existing = current_latest(session, ctx.game_id)" in source
