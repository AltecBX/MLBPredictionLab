"""Feature store, model registry, immutable predictions, backtest output."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ModelFeature(Base):
    """Immutable feature-store row: one per (game, side, as_of, feature set)."""

    __tablename__ = "model_features"
    __table_args__ = (
        UniqueConstraint(
            "game_id", "team_side", "as_of", "feature_set_version", name="uq_model_feature"
        ),
        Index("ix_mf_game", "game_id"),
        Index("ix_mf_features", "features", postgresql_using="gin"),
        CheckConstraint("team_side IN ('H','A')", name="ck_mf_side"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    team_side: Mapped[str] = mapped_column(String(1), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    feature_set_version: Mapped[str] = mapped_column(String(32), nullable=False)
    features: Mapped[dict] = mapped_column(JSONB, nullable=False)
    sample_sizes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    estimated_flags: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    missing_features: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list
    )
    completeness: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_model_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_set_version: Mapped[str] = mapped_column(String(32), nullable=False)
    trained_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    train_start_date: Mapped[date | None] = mapped_column(Date)
    train_end_date: Mapped[date | None] = mapped_column(Date)
    train_rows: Mapped[int | None] = mapped_column(Integer)
    hyperparameters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    calibration_method: Mapped[str | None] = mapped_column(String(32))
    calibration_params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    feature_names: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    artifact_path: Mapped[str | None] = mapped_column(Text)
    # The artifact itself, not merely where it was written.
    #
    # `artifact_path` is a path on the filesystem of whichever machine ran the
    # training, and nothing else can read it. That is not a theoretical
    # limitation: the hourly pregame job reissues predictions WITHOUT
    # retraining, runs on a fresh GitHub runner, and failed every hour with
    # `FileNotFoundError: artifacts/models/jerry_logistic/v1/model.pkl` — a
    # registry row pointing at a file that only ever existed somewhere else.
    #
    # The database is the one durable store every process here can reach: the
    # API on Render, the daily refresh, the pregame job. A logistic model over
    # forty-odd features pickles to a few kilobytes, so this costs nothing
    # worth measuring.
    artifact_blob: Mapped[bytes | None] = mapped_column(LargeBinary)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64))
    git_sha: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(Text)


# Exactly one active version per model name.
ModelVersion.__table__.append_constraint(
    Index(
        "uq_model_version_active",
        ModelVersion.__table__.c.name,
        unique=True,
        postgresql_where=ModelVersion.__table__.c.is_active.is_(True),
    )
)


class Prediction(Base):
    """Immutable prediction snapshot. Never updated except to mark superseded."""

    __tablename__ = "predictions"
    __table_args__ = (
        UniqueConstraint("game_id", "model_version_id", "as_of", name="uq_prediction_asof"),
        Index("ix_pred_game", "game_id"),
        Index("ix_pred_created", "created_at"),
        CheckConstraint(
            "abs(home_win_prob + away_win_prob - 1) < 0.000001",
            name="ck_pred_probs_sum_to_one",
        ),
        CheckConstraint(
            "home_win_prob > 0 AND home_win_prob < 1", name="ck_pred_prob_range"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    model_version_id: Mapped[int] = mapped_column(
        ForeignKey("model_versions.id"), nullable=False
    )
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    home_win_prob: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)
    away_win_prob: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)
    home_win_prob_uncalibrated: Mapped[float | None] = mapped_column(Numeric(6, 5))

    projected_home_runs: Mapped[float | None] = mapped_column(Numeric(5, 2))
    projected_away_runs: Mapped[float | None] = mapped_column(Numeric(5, 2))
    projected_home_runs_low: Mapped[float | None] = mapped_column(Numeric(5, 2))
    projected_home_runs_high: Mapped[float | None] = mapped_column(Numeric(5, 2))
    projected_away_runs_low: Mapped[float | None] = mapped_column(Numeric(5, 2))
    projected_away_runs_high: Mapped[float | None] = mapped_column(Numeric(5, 2))

    fair_home_moneyline: Mapped[int | None] = mapped_column(Integer)
    fair_away_moneyline: Mapped[int | None] = mapped_column(Integer)
    market_home_prob: Mapped[float | None] = mapped_column(Numeric(6, 5))
    market_edge: Mapped[float | None] = mapped_column(Numeric(6, 5))

    confidence_score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    confidence_label: Mapped[str] = mapped_column(String(24), nullable=False)
    recommendation: Mapped[str] = mapped_column(String(32), nullable=False)
    model_agreement: Mapped[float | None] = mapped_column(Numeric(5, 4))
    data_completeness: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    missing_data: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    warnings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    feature_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    component_probs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    confidence_components: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    superseded_by: Mapped[int | None] = mapped_column(ForeignKey("predictions.id"))
    is_latest: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


Prediction.__table__.append_constraint(
    Index(
        "uq_prediction_latest",
        Prediction.__table__.c.game_id,
        Prediction.__table__.c.model_version_id,
        unique=True,
        postgresql_where=Prediction.__table__.c.is_latest.is_(True),
    )
)


class PredictionExplanation(Base):
    """Per-feature contribution in probability points."""

    __tablename__ = "prediction_explanations"
    __table_args__ = (
        Index("ix_pexp_prediction", "prediction_id", "rank"),
        CheckConstraint("favors IN ('H','A')", name="ck_pexp_favors"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    prediction_id: Mapped[int] = mapped_column(
        ForeignKey("predictions.id", ondelete="CASCADE"), nullable=False
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    feature_key: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    favors: Mapped[str] = mapped_column(String(1), nullable=False)
    contribution_pp: Mapped[float] = mapped_column(Numeric(6, 3), nullable=False)
    feature_value: Mapped[float | None] = mapped_column(Numeric(12, 5))
    feature_display: Mapped[str | None] = mapped_column(Text)
    sample_size: Mapped[int | None] = mapped_column(Integer)
    is_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    narrative: Mapped[str | None] = mapped_column(Text)


class SimulationResult(Base):
    """Phase 3. Empty until the Monte Carlo engine is enabled."""

    __tablename__ = "simulation_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.id"), nullable=False)
    n_simulations: Mapped[int] = mapped_column(Integer, nullable=False)
    home_win_pct: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)
    away_win_pct: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)
    mean_home_runs: Mapped[float | None] = mapped_column(Numeric(5, 2))
    mean_away_runs: Mapped[float | None] = mapped_column(Numeric(5, 2))
    run_distribution: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    score_distribution: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    extra_innings_prob: Mapped[float | None] = mapped_column(Numeric(6, 5))
    one_run_prob: Mapped[float | None] = mapped_column(Numeric(6, 5))
    upset_prob: Mapped[float | None] = mapped_column(Numeric(6, 5))
    seed: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_set_version: Mapped[str] = mapped_column(String(32), nullable=False)
    as_of_policy: Mapped[str] = mapped_column(String(24), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    step_days: Mapped[int] = mapped_column(Integer, nullable=False)
    validation_days: Mapped[int] = mapped_column(Integer, nullable=False)
    min_train_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    git_sha: Mapped[str | None] = mapped_column(String(64))
    n_games: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_steps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_steps_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sanity_flags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BacktestResult(Base):
    """Sliced metrics. slice_type/slice_key follow BACKTEST_PLAN.md §4."""

    __tablename__ = "backtest_results"
    __table_args__ = (
        Index("ix_btr_run_slice", "run_id", "slice_type"),
        UniqueConstraint("run_id", "slice_type", "slice_key", name="uq_backtest_slice"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False
    )
    model_version_id: Mapped[int | None] = mapped_column(ForeignKey("model_versions.id"))
    slice_type: Mapped[str] = mapped_column(String(32), nullable=False)
    slice_key: Mapped[str] = mapped_column(String(64), nullable=False)
    season: Mapped[int | None] = mapped_column(Integer)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    n_games: Mapped[int] = mapped_column(Integer, nullable=False)
    accuracy: Mapped[float | None] = mapped_column(Numeric(7, 5))
    log_loss: Mapped[float | None] = mapped_column(Numeric(8, 6))
    brier_score: Mapped[float | None] = mapped_column(Numeric(8, 6))
    calibration_error: Mapped[float | None] = mapped_column(Numeric(8, 6))
    max_calibration_error: Mapped[float | None] = mapped_column(Numeric(8, 6))
    roc_auc: Mapped[float | None] = mapped_column(Numeric(7, 5))
    roi: Mapped[float | None] = mapped_column(Numeric(8, 5))
    clv: Mapped[float | None] = mapped_column(Numeric(8, 5))
    extra: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BacktestPrediction(Base):
    """Row-level walk-forward output, so any slice can be recomputed."""

    __tablename__ = "backtest_predictions"
    __table_args__ = (
        Index("ix_btp_run", "run_id"),
        UniqueConstraint("run_id", "game_id", name="uq_backtest_pred"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False
    )
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    predicted_home_win_prob: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)
    actual_home_win: Mapped[bool] = mapped_column(Boolean, nullable=False)
    train_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    n_train_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    lineup_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    starter_quality_index: Mapped[float | None] = mapped_column(Numeric(8, 4))
    features: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


__all__ = [
    "ModelFeature",
    "ModelVersion",
    "Prediction",
    "PredictionExplanation",
    "SimulationResult",
    "BacktestRun",
    "BacktestResult",
    "BacktestPrediction",
]
