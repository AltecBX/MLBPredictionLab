"""API integration tests.

These run against a throwaway PostgreSQL database created for the test session
and seeded with a small, explicitly synthetic fixture. If no database is
reachable the module skips rather than silently passing.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.models import (  # noqa: E402
    BacktestResult,
    BacktestRun,
    Ballpark,
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
            config={"served": {"available": True, "blend_weight": 0.5,
                               "run_model": "projected", "simulations": 20000,
                               "n_games": 6000, "n_blended": 5900,
                               "n_logistic_only": 100}},
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
                # The served figure's rows sit beside the component's under a
                # prefixed slice type; the product readouts prefer them.
                BacktestResult(
                    run_id=run.id, slice_type="served_overall", slice_key="all",
                    n_games=6000, accuracy=0.561, log_loss=0.6790, brier_score=0.2436,
                    calibration_error=0.009, max_calibration_error=0.05, roc_auc=0.588,
                    extra={"bins": [{"lower": 0.5, "upper": 0.6, "n": 3000,
                                     "mean_predicted": 0.54, "observed_frequency": 0.545,
                                     "wilson_low": 0.52, "wilson_high": 0.56}]},
                ),
                BacktestResult(
                    run_id=run.id, slice_type="served_probability_band", slice_key="60-65",
                    n_games=350, accuracy=0.63, log_loss=0.655, brier_score=0.228,
                    extra={"mean_predicted": 0.622, "observed": 0.640},
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
    # This fixture's prediction has no persisted simulation, so the slot must be
    # an explicit unavailable carrying a reason — never zeros standing in for a
    # simulation that did not run.
    assert body["simulation"]["available"] is False
    assert body["simulation"]["reason"]
    assert "home_win_pct" not in body["simulation"]
    assert body["market"]["available"] is False
    assert "ODDS_PROVIDER" in body["market"]["reason"]


def test_a_persisted_simulation_surfaces_on_the_detail(client, seeded):
    """The Simulation tab reads back what was served, not a fresh re-run.

    The row is removed again at the end: this module shares one seeded database
    across its tests, and a simulation left behind would silently change what
    the unavailable-state test above is asserting.
    """
    from sqlalchemy import select as sa_select

    from app.db.models import Prediction, SimulationResult

    Session = sessionmaker(bind=seeded, expire_on_commit=False)
    session = Session()
    prediction = session.scalar(
        sa_select(Prediction).where(
            Prediction.game_id == 555001, Prediction.is_latest.is_(True)
        )
    )
    original_snapshot = dict(prediction.feature_snapshot or {})
    row = SimulationResult(
        prediction_id=prediction.id,
        n_simulations=20000,
        home_win_pct=0.5432,
        away_win_pct=0.4568,
        mean_home_runs=4.61,
        mean_away_runs=4.33,
        run_distribution={"max_reported": 10, "home": [0.02] * 11, "away": [0.02] * 11},
        score_distribution={
            "scores": [{"away": 3, "home": 4, "probability": 0.041}],
            "covered": 0.041,
        },
        extra_innings_prob=0.0871,
        one_run_prob=0.2604,
        upset_prob=0.4568,
        seed=555001,
    )
    session.add(row)
    prediction.feature_snapshot = original_snapshot | {
        "blend": {
            "method": "log_odds", "weight_on_simulation": 0.5,
            "is_blended": True, "simulation_unavailable": None,
        }
    }
    session.commit()

    try:
        simulation = client.get("/api/v1/games/555001").json()["simulation"]
        assert simulation["available"] is True
        assert simulation["home_win_pct"] == pytest.approx(0.5432)
        assert simulation["n_simulations"] == 20000
        assert simulation["max_reported_runs"] == 10
        assert simulation["likely_scores"][0] == {"away": 3, "home": 4, "probability": 0.041}
        assert simulation["one_run_prob"] == pytest.approx(0.2604)
        assert simulation["blended_with_logistic"] is True
        assert simulation["blend_weight"] == 0.5
    finally:
        session.delete(row)
        prediction.feature_snapshot = original_snapshot
        session.commit()
        session.close()


def test_backtest_evidence_reports_the_matching_band(client):
    body = client.get("/api/v1/games/555001").json()
    evidence = body["backtest_evidence"]
    assert evidence["available"] is True
    assert evidence["band"] == "60-65"
    assert evidence["n"] == 400
    # The overall figures describe what is served, not the logistic component.
    assert evidence["overall_log_loss"] == pytest.approx(0.6790)
    assert evidence["overall_calibration_error"] == pytest.approx(0.009)


def test_historical_calibration_judges_against_the_served_bands(seeded):
    """The probability being judged is the served one, so its band is too."""
    from sqlalchemy.orm import sessionmaker

    from app.backtest.served import reported_slices
    from app.services.confidence import historical_calibration

    Session = sessionmaker(bind=seeded)
    with Session() as session:
        score, detail = historical_calibration(session, 0.62)
        assert detail["band"] == "60-65"
        assert detail["n"] == 350  # the served row, not the component's 400
        assert detail["observed"] == pytest.approx(0.640)
        assert score is not None

        run_id = session.scalar(select(BacktestRun.id))
        assert [r.slice_type for r in reported_slices(session, run_id, "overall")] == [
            "served_overall"
        ]


def test_a_run_without_served_rows_falls_back_to_the_component(seeded):
    from sqlalchemy.orm import sessionmaker

    from app.backtest.served import reported_slices

    Session = sessionmaker(bind=seeded)
    with Session() as session:
        older = BacktestRun(
            model_name=settings.active_model_name, algorithm="logistic_regression_l2",
            feature_set_version="fs_v1", as_of_policy="T_MINUS_3H",
            start_date=date(2023, 4, 1), end_date=date(2024, 10, 1), step_days=30,
            validation_days=45, min_train_rows=500, seed=1, n_games=3000, n_steps=15,
            sanity_flags=[], created_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
        session.add(older)
        session.flush()
        session.add(
            BacktestResult(
                run_id=older.id, slice_type="overall", slice_key="all", n_games=3000,
                log_loss=0.69, extra={},
            )
        )
        session.flush()
        try:
            rows = reported_slices(session, older.id, "overall")
            assert [r.slice_type for r in rows] == ["overall"]
            assert reported_slices(session, older.id, "probability_band") == []
        finally:
            session.rollback()


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


def test_backtest_payload_separates_the_served_figure_from_the_component(client):
    body = client.get("/api/v1/backtest/latest").json()
    assert body["component"] == "logistic"
    # The component's figures keep their keys, and no served row leaks into them.
    assert body["overall"]["log_loss"] == pytest.approx(0.6805)
    assert not any(key.startswith("served_") for key in body["slices"])

    served = body["served"]
    assert served["available"] is True
    assert served["overall"]["slice_type"] == "overall"
    assert served["overall"]["log_loss"] == pytest.approx(0.6790)
    assert served["calibration_bins"][0]["n"] == 3000
    assert served["slices"]["probability_band"][0]["n_games"] == 350
    assert served["config"]["blend_weight"] == 0.5
    assert served["config"]["n_logistic_only"] == 100


def test_backtest_run_list(client):
    body = client.get("/api/v1/backtest/runs").json()
    assert body["count"] == 1


def test_invalid_backtest_run_id(client):
    assert client.get("/api/v1/backtest/runs/not-a-uuid").status_code == 400


# --- metadata ---------------------------------------------------------------

def test_feature_dictionary_endpoint(client):
    from app.core.config import settings

    body = client.get("/api/v1/meta/features").json()
    assert body["feature_set_version"] == settings.feature_set_version
    # The dictionary lists the set that is actually served, whatever it is.
    from app.features.registry import feature_keys

    assert [s["key"] for s in body["active"]] == feature_keys(settings.feature_set_version)
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

    # Drift is real now rather than a Phase 4 placeholder. The fixture has an
    # active model, so the report is available; what it must never do is claim
    # a PSI or a calibration reading it did not have the sample to compute.
    drift = body["drift"]
    assert drift["available"] is True
    assert drift["model_version"]
    assert "note" in drift["bands"]
    assert drift["calibration"]["available"] is False
    assert "are needed" in drift["calibration"]["reason"]
    assert drift["importance_stability"]["available"] is False


def test_openapi_schema_is_generated(client):
    body = client.get("/openapi.json").json()
    assert "/api/v1/games" in body["paths"]
    assert "/api/v1/backtest/latest" in body["paths"]


# --- configuration ----------------------------------------------------------

def test_blank_optional_env_vars_disable_their_category_rather_than_crashing(monkeypatch):
    """Orchestrators pass VAR= for an unset variable; that must not fail startup."""
    from app.core.config import Settings

    for name in ("REDIS_URL", "ADMIN_API_KEY", "SENTRY_DSN", "ODDS_PROVIDER",
                 "WEATHER_PROVIDER", "LINEUP_PROVIDER", "STATCAST_PROVIDER"):
        monkeypatch.setenv(name, "")

    parsed = Settings(_env_file=None)
    assert parsed.redis_url is None
    assert parsed.caching_active is False
    assert parsed.admin_api_key is None
    assert parsed.odds_provider is None
    assert parsed.weather_provider is None


@pytest.mark.parametrize(
    "given",
    [
        # Exactly what Render, Heroku, Railway, Supabase and Neon hand you.
        "postgresql://jerry:pw@dpg-abc.oregon-postgres.render.com/jerry_mlb",
        "postgres://u:p@ec2-1-2-3-4.compute-1.amazonaws.com:5432/d",
        "postgresql://u:p@host:5432/db?sslmode=require",
        # Already explicit; must survive untouched.
        "postgresql+psycopg://postgres@127.0.0.1:5432/jerry_mlb",
    ],
)
def test_provider_database_urls_resolve_to_the_installed_driver(given):
    """A copy-pasted managed-Postgres DSN must not be a startup failure.

    SQLAlchemy reads a bare `postgresql://` as psycopg2, which is not installed,
    so the process would die on first connect with a bare ModuleNotFoundError —
    a long way from the actual cause.
    """
    from app.core.config import Settings

    parsed = Settings(_env_file=None, database_url=given)
    assert parsed.sqlalchemy_url.startswith("postgresql+psycopg://")
    # The rest of the DSN is carried through unchanged.
    assert parsed.sqlalchemy_url.split("://", 1)[1] == given.split("://", 1)[1]


def test_required_provider_cannot_be_blank(monkeypatch):
    """A required category refuses to resolve rather than degrading silently."""
    from app.core.errors import ConfigurationError
    from app.providers import registry

    monkeypatch.setattr(registry.settings, "schedule_provider", None, raising=False)
    with pytest.raises(ConfigurationError, match="required"):
        registry.get_schedule_provider()


def test_admin_routes_are_disabled_when_no_key_is_configured(monkeypatch):
    from fastapi import HTTPException

    from app.api.deps import require_admin
    from app.core.config import settings as live_settings

    monkeypatch.setattr(live_settings, "admin_api_key", None, raising=False)
    with pytest.raises(HTTPException) as exc:
        require_admin(x_admin_key=None)
    assert exc.value.status_code == 404
    assert "disabled" in exc.value.detail

    monkeypatch.setattr(live_settings, "admin_api_key", "secret", raising=False)
    with pytest.raises(HTTPException) as exc:
        require_admin(x_admin_key="wrong")
    assert exc.value.status_code == 401
    require_admin(x_admin_key="secret")  # correct key passes


# --- staleness --------------------------------------------------------------

def test_prediction_predating_a_source_refresh_is_flagged(seeded):
    """The spec requires telling the user when a prediction is out of date."""
    from datetime import UTC, datetime, timedelta

    from app.services.game_view import _staleness_warning

    created = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)

    class _Stub:
        created_at = created

    # A refresh well after the prediction is flagged.
    warning = _staleness_warning(_Stub(), created + timedelta(minutes=45))
    assert warning is not None
    assert warning.code == "PREDICTION_PREDATES_SOURCE_REFRESH"
    assert "45 minutes" in warning.message

    # A near-concurrent refresh is not.
    assert _staleness_warning(_Stub(), created + timedelta(minutes=2)) is None
    # A refresh before the prediction is not.
    assert _staleness_warning(_Stub(), created - timedelta(hours=1)) is None
    # No prediction or no source data means nothing to compare.
    assert _staleness_warning(None, created) is None
    assert _staleness_warning(_Stub(), None) is None
