"""The API must stay useful when its database is not.

A reader opened the site and saw nothing. The cause was not a bug in any
feature — it was that a dependency being unavailable took the whole product
down instead of degrading it, in two places at once:

* the container ran `alembic upgrade head && uvicorn`, so a database that was
  merely slow to wake stopped the API from *existing*, and Render answered 502
  to everything until somebody redeployed by hand;
* a request that reached a dead database escaped as a bare 500 with a
  plain-text body, which the web app could only render as
  "500 Internal Server Error".

These tests pin the contract that replaced it: liveness never depends on the
database, and a database failure is an explicit, retryable, *explained*
unavailable state.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402

from app.api import deps  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture
def dead_database():
    """Every database session raises the way psycopg does when nothing answers."""

    def broken():
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))
        yield  # pragma: no cover - unreachable, keeps this a generator

    app.dependency_overrides[deps.db_session] = broken
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client
    app.dependency_overrides.clear()


def test_liveness_answers_without_a_database(dead_database):
    """Render's health check hits this. If it fails, the service is torn down.

    A liveness probe that depends on the database turns a database outage into
    a service outage, which is the difference between a site that explains
    itself and a site that is a 502.
    """
    response = dead_database.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_a_dead_database_is_a_503_not_a_500(dead_database):
    response = dead_database.get("/api/v1/games?date=2026-08-02")
    assert response.status_code == 503


def test_the_failure_explains_itself_in_json(dead_database):
    """The web app renders `detail`. A plain-text 500 gave it nothing to say."""
    response = dead_database.get("/api/v1/games?date=2026-08-02")
    body = response.json()
    assert body["code"] == "DATABASE_UNAVAILABLE"
    assert "database" in body["detail"].lower()
    # It must not read as a bug in the product.
    assert "internal server error" not in body["detail"].lower()


def test_the_status_is_one_the_client_retries(dead_database):
    """503 is in the web client's retryable set; 500 is not.

    This is what lets a database that is waking be ridden out instead of
    reported as broken, so the two halves of the fix stay in agreement.
    """
    assert dead_database.get("/api/v1/games?date=2026-08-02").status_code == 503


def test_readiness_reports_unavailable_rather_than_claiming_health(dead_database):
    """Liveness says the process is up; readiness must not say the same.

    If both answered 200 the uptime check would pass through a total outage.
    """
    assert dead_database.get("/api/v1/diagnostics/health").status_code == 503


def test_the_detail_does_not_leak_a_connection_string(dead_database):
    """Errors are read by whoever opens the site. Credentials are not for them."""
    detail = dead_database.get("/api/v1/games?date=2026-08-02").json()["detail"]
    for leak in ("postgresql://", "postgresql+psycopg", "password", "@"):
        assert leak not in detail
