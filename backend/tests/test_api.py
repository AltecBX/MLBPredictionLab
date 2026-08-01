"""API integration tests.

These run against a throwaway PostgreSQL database created for the test session
and seeded with a small, explicitly synthetic fixture. If no database is
reachable the module skips rather than silently passing.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.models import (  # noqa: E402
    Ballpark,
    BacktestResult,
    BacktestRun,
    DataSourceStatus,
    Game,
    ModelVersion,
    Player,
    Prediction,
    PredictionExplanation,
    Team,
)

TEST_DB = "jerry_mlb_apitest"


def _admin_url() -> str:
    return str(settings.database_url).rsplit("/", 1)[0] + "/postgres"


def _test_url() -> str:
    return str(settings.database_url).rsplit("/", 1)[0] + f"/{TEST_DB}"


@pytest.fixture(scope="module")
def test_engine():
    try:
        admin = create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
        admin.dispose()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"PostgreSQL is not reachable for integration tests: {exc}")

    engine = create_engine(_test_url())
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def seeded(test_engine):
    Session = sessionmaker(bind=test_engine, expire_on_commit=False)
    now = datetime.now(UTC)
    first_pitch = now + timedelta(hours=6)

    with Session() as session:
        session.add(
            Ballpark(id=1, name="Test Park", city="Testville", latitude=40.0,
                     longitude=-74.0, elevation_ft=30, roof_type="Open",
                     lf_line=330, center=400, rf_line=330, turf_type="Grass",
                     capacity=42000, timezone="America/New_York",
                     source_name="fixture", knowledge_time=now)
        )
        session.add_all(
            [
                Team(id=10, name="Home Club", abbreviation="HME", team_name="Club",
                     division_name="Test East", home_venue_id=1,
                     source_name="fixture", knowledge_time=now),
                Team(id=20, name="Away Club", abbreviation="AWY", team_name="Visitors",
                     division_name="Test West", source_name="fixture",
                     knowledge_time=now),
            ]
        )
        session.add_all(
            [
                Player(id=101, full_name="Home Starter", pitch_hand="R",
                       primary_position="P", source_name="fixture", knowledge_time=now),
                Player(id=201, full_name="Away Starter", pitch_hand="L",
                       primary_position="P", source_name="fixture", knowledge_time=now),
            ]
        )
        session.flush()

        session.add(
            Game(
                id=555001, season=2026, game_type="R", game_date_utc=first_pitch,
                official_date=first_pitch.date(), status_abstract="Preview",
                status_detailed="Scheduled", home_team_id=10, away_team_id=20,
                venue_id=1, day_night="night", doubleheader="N", game_number=1,
                is_final=False, home_probable_pitcher_id=101,
                away_probable_pitcher_id=201, home_record_wins=55,
                home_record_losses=45, away_record_wins=48, away_record_losses=52,
                source_name="fixture", knowledge_time=now,
            )
        )
        version = ModelVersion(
            name=settings.active_model_name, version="test", algorithm="logistic_regression_l2",
            feature_set_version="fs_v1", train_rows=1000, is_active=True,
            feature_names=["elo_diff"], metrics={"out_of_sample": {"log_loss": 0.68}},
            hyperparameters={"C": 0.01},
        )
        session.add(version)
        session.flush()

        prediction = Prediction(
            game_id=555001, model_version_id=version.id, as_of=now,
            home_win_prob=0.618, away_win_prob=0.382,
            home_win_prob_uncalibrated=0.63,
            projected_home_runs=4.6, projected_away_runs=4.0,
            projected_home_runs_low=3, projected_home_runs_high=6,
            projected_away_runs_low=2, projected_away_runs_high=5,
            fair_home_moneyline=-162, fair_away_moneyline=162,
            confidence_score=0.71, confidence_label="MODERATE",
            recommendation="STRONG_LEAN", model_agreement=0.82,
            data_completeness=0.94, missing_data=[],
            warnings=[{"code": "LINEUP_UNCONFIRMED", "severity": "medium",
                       "message": "Batting orders are not confirmed."}],
            feature_snapshot={"signature": "x", "features": {"elo_diff": 42.0},
                              "sample_sizes": {"elo_diff": 90},
                              "estimated_flags": {"elo_diff": False},
                              "missing_features": [],
                              "run_projection": {"method": "odds_ratio_runs_v1",
                                                 "is_estimated": True, "detail": "test"}},
            component_probs={"logistic_calibrated": 0.618, "elo_reference": 0.60},
            confidence_components={"historical_calibration_detail":
                                   {"band": "60-65", "n": 400, "observed": 0.607,
                                    "predicted": 0.621}},
            is_latest=True,
        )
        session.add(prediction)
        session.flush()
        session.add(
            PredictionExplanation(
                prediction_id=prediction.id, rank=1,
                feature_key="sp_fip_season_diff", display_name="Starter FIP edge",
                category="starting_pitching", favors="H", contribution_pp=6.2,
                feature_value=0.41, feature_display="+0.41 FIP", sample_size=18,
                is_estimated=False,
                narrative="Home Club has the better fielding-independent starter.",
            )
        )
        session.add(
            DataSourceStatus(
                source_name="mlb_statsapi", category="schedule", status="OK",
                freshness="FRESH", last_success_at=now, consecutive_failures=0,
                records_last_run=15,
            )
        )

        run = BacktestRun(
            model_name=settings.active_model_name, algorithm="logistic_regression_l2",
            feature_set_version="fs_v1", as_of_policy="T_MINUS_3H",
            start_date=date(2023, 4, 1), end_date=date(2025, 10, 1), step_days=30,
            validation_days=45, min_train_rows=500, seed=1, n_games=6000, n_steps=30,
            sanity_flags=[],
        )
        session.add(run)
        session.flush()
        session.add_all(
            [
                BacktestResult(
                    run_id=run.id, slice_type="overall", slice_key="all", n_games=6000,
                    accuracy=0.558, log_loss=0.6805, brier_score=0.2441,
                    calibration_error=0.011, max_calibration_error=0.03, roc_auc=0.585,
                    extra={"bins": [{"lower": 0.4, "upper": 0.5, "n": 900,
                                     "mean_predicted": 0.46, "observed_frequency": 0.45,
                                     "wilson_low": 0.42, "wilson_high": 0.48}]},
                ),
                BacktestResult(
                    run_id=run.id, slice_type="probability_band", slice_key="60-65",
                    n_games=400, accuracy=0.61, log_loss=0.66, brier_score=0.23,
                    extra={"mean_predicted": 0.621, "observed": 0.607},
                ),
                BacktestResult(
                    run_id=run.id, slice_type="ablation", slice_key="starting_pitcher",
                    n_games=6000, log_loss=0.6841,
                    extra={"group": "starting_pitcher", "n_features_removed": 13,
                           "delta_log_loss": 0.0036, "verdict": "IMPROVES"},
                ),
            ]
        )
        session.commit()
    return test_engine


@pytest.fixture(scope="module")
def client(seeded):
    from app.api import deps
    from app.main import app

    Session = sessionmaker(bind=seeded, expire_on_commit=False)

    def override():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[deps.db_session] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# --- health -----------------------------------------------------------------

def test_process_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_diagnostics_health(client):
    response = client.get("/api/v1/diagnostics/health")
    assert response.status_code == 200
    body = response.json()
    assert body["database"]["reachable"] is True
    assert "degraded_categories" in body


# --- games ------------------------------------------------------------------

def test_game_list_returns_the_seeded_game(client, seeded):
    with sessionmaker(bind=seeded)() as session:
        target = session.query(Game).one().official_date

    response = client.get(f"/api/v1/games?date={target.isoformat()}")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    game = body["games"][0]
    assert game["home"]["abbreviation"] == "HME"
    assert game["away"]["abbreviation"] == "AWY"
    assert game["home_pitcher"]["full_name"] == "Home Starter"
    assert game["ballpark"]["name"] == "Test Park"
    assert game["prediction"]["recommendation"] == "STRONG_LEAN"


def test_probabilities_sum_to_one_in_the_response(client, seeded):
    with sessionmaker(bind=seeded)() as session:
        target = session.query(Game).one().official_date
    body = client.get(f"/api/v1/games?date={target.isoformat()}").json()
    prediction = body["games"][0]["prediction"]
    assert prediction["home_win_prob"] + prediction["away_win_prob"] == pytest.approx(1.0)


def test_game_list_reports_freshness_per_category(client, seeded):
    with sessionmaker(bind=seeded)() as session:
        target = session.query(Game).one().official_date
    body = client.get(f"/api/v1/games?date={target.isoformat()}").json()
    categories = {row["category"] for row in body["freshness"]}
    assert {"schedule", "lineups", "weather", "odds"} <= categories
    lineups = next(r for r in body["freshness"] if r["category"] == "lineups")
    assert lineups["freshness"] == "UNAVAILABLE"


def test_unknown_sort_is_rejected(client):
    response = client.get("/api/v1/games?date=2026-08-01&sort=nonsense")
    assert response.status_code == 400
    assert "Unknown sort" in response.json()["detail"]


def test_empty_date_returns_zero_games_not_an_error(client):
    response = client.get("/api/v1/games?date=1990-01-01")
    assert response.status_code == 200
    assert response.json()["count"] == 0


def test_game_detail_exposes_every_tab_payload(client):
    response = client.get("/api/v1/games/555001")
    assert response.status_code == 200
    body = response.json()
    for key in (
        "card", "drivers_for", "drivers_against", "matchup_bars", "home_detail",
        "away_detail", "matchup_history", "environment", "simulation", "market",
        "backtest_evidence", "change_since_previous", "freshness",
    ):
        assert key in body

    assert body["drivers_for"][0]["display_name"] == "Starter FIP edge"
    assert body["simulation"]["available"] is False
    assert "Phase 3" in body["simulation"]["reason"] or body["simulation"]["phase"] == 3
    assert body["market"]["available"] is False
    assert "ODDS_PROVIDER" in body["market"]["reason"]


def test_backtest_evidence_reports_the_matching_band(client):
    body = client.get("/api/v1/games/555001").json()
    evidence = body["backtest_evidence"]
    assert evidence["available"] is True
    assert evidence["band"] == "60-65"
    assert evidence["n"] == 400
    assert evidence["overall_log_loss"] == pytest.approx(0.6805)


def test_missing_game_is_404(client):
    assert client.get("/api/v1/games/999999").status_code == 404


def test_prediction_history_endpoint(client):
    body = client.get("/api/v1/games/555001/predictions").json()
    assert body["count"] == 1
    assert body["predictions"][0]["is_latest"] is True
    assert body["change_since_previous"]["has_previous"] is False


# --- backtest ---------------------------------------------------------------

def test_backtest_latest_returns_slices_and_omits_odds_metrics(client):
    response = client.get("/api/v1/backtest/latest")
    assert response.status_code == 200
    body = response.json()
    assert body["overall"]["n_games"] == 6000
    assert body["baseline_log_loss"] == pytest.approx(0.6931, abs=1e-4)
    assert "probability_band" in body["slices"]
    assert "ablation" in body["slices"]
    assert body["odds_dependent_metrics"]["available"] is False
    # ROI/CLV are null, never zero.
    assert body["overall"]["roi"] is None
    assert body["overall"]["clv"] is None


def test_backtest_run_list(client):
    body = client.get("/api/v1/backtest/runs").json()
    assert body["count"] == 1


def test_invalid_backtest_run_id(client):
    assert client.get("/api/v1/backtest/runs/not-a-uuid").status_code == 400


# --- metadata ---------------------------------------------------------------

def test_feature_dictionary_endpoint(client):
    body = client.get("/api/v1/meta/features").json()
    assert body["feature_set_version"] == "fs_v1"
    assert len(body["active"]) > 30
    assert all(spec["available"] for spec in body["active"])
    assert all(not spec["available"] for spec in body["deferred"])
    assert any(spec["source_category"] == "odds" for spec in body["deferred"])


def test_model_registry_endpoint(client):
    body = client.get("/api/v1/meta/models").json()
    assert body["count"] == 1
    assert body["models"][0]["is_active"] is True


def test_diagnostics_snapshot(client):
    body = client.get("/api/v1/diagnostics").json()
    for key in ("sources", "jobs", "missing_data", "model", "predictions",
                "backtest", "api_usage", "feature_set", "drift"):
        assert key in body
    assert body["drift"]["available"] is False


def test_openapi_schema_is_generated(client):
    body = client.get("/openapi.json").json()
    assert "/api/v1/games" in body["paths"]
    assert "/api/v1/backtest/latest" in body["paths"]
