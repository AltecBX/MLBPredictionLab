"""Prediction generation.

Predictions are immutable, timestamped snapshots. A new prediction supersedes
the previous one; both remain queryable so a historical prediction can be
evaluated exactly as it was issued (ARCHITECTURE.md §2 rule 3).
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.errors import ModelNotFoundError
from app.core.logging import get_logger
from app.db.models import (
    Game,
    ModelFeature,
    ModelVersion,
    Prediction,
    PredictionExplanation,
    Team,
)
from app.features.asof import AsOfStore
from app.features.builder import FeatureBuilder, FeatureVector
from app.features.context import GameContext
from app.features.elo import AsOfElo
from app.ingestion.status import job_run
from app.modeling.logistic import LogisticWinModel
from app.modeling.registry import load_active_model
from app.services.confidence import recommendation_label, score_confidence
from app.services.explanation import build_contributions, build_warnings
from app.services.freshness import freshness_map
from app.services.run_projection import fair_moneyline, project_runs

log = get_logger(__name__)

# The live as-of never reaches first pitch.
LIVE_AS_OF_MARGIN = pd.Timedelta(minutes=1).to_pytimedelta()

# Reference spread used to normalize model agreement into [0, 1].
AGREEMENT_SPREAD = 0.10


def _live_as_of(first_pitch: datetime, now: datetime | None = None) -> datetime:
    now = now or utcnow()
    return min(now, first_pitch - LIVE_AS_OF_MARGIN)


def _model_agreement(components: dict[str, float]) -> float | None:
    values = [v for v in components.values() if v is not None]
    if len(values) < 2:
        return None
    spread = float(np.std(values))
    return round(float(max(0.0, 1.0 - spread / AGREEMENT_SPREAD)), 4)


def _feature_frame(vector: FeatureVector, feature_names: list[str]) -> pd.DataFrame:
    return pd.DataFrame([{name: vector.features.get(name) for name in feature_names}])


def _snapshot_signature(vector: FeatureVector, probability: float) -> str:
    payload = {
        "features": {
            k: (None if v is None else round(float(v), 6))
            for k, v in sorted(vector.features.items())
        },
        "p": round(float(probability), 6),
    }
    return json.dumps(payload, sort_keys=True)


def generate_prediction(
    session: Session,
    ctx: GameContext,
    vector: FeatureVector,
    model: LogisticWinModel,
    version: ModelVersion,
    builder: FeatureBuilder,
    elo: AsOfElo,
    team_names: dict[int, str],
    freshness: dict[str, str],
    force: bool = False,
) -> Prediction | None:
    """Score one game and persist an immutable prediction snapshot."""
    frame = _feature_frame(vector, list(model.feature_names))
    raw = float(model.predict_raw(frame)[0])
    probability = float(model.predict(frame)[0])
    probability = min(max(probability, 0.001), 0.999)

    # Elo is shipped in Phase 1 as a reference model: it does not enter the
    # ensemble, but the spread between it and the calibrated model is a real
    # disagreement signal (MODELING_PLAN.md §3).
    as_of_ts = pd.Timestamp(vector.as_of)
    elo_prob = elo.engine.expected_home(
        elo.rating_at(ctx.home_team_id, as_of_ts),
        elo.rating_at(ctx.away_team_id, as_of_ts),
        elo.engine.home_advantage,
    )
    components = {"logistic_calibrated": probability, "elo_reference": float(elo_prob)}
    agreement = _model_agreement(components)

    existing = session.scalar(
        select(Prediction).where(
            Prediction.game_id == ctx.game_id,
            Prediction.model_version_id == version.id,
            Prediction.is_latest.is_(True),
        )
    )
    signature = _snapshot_signature(vector, probability)
    if existing is not None and not force:
        previous = existing.feature_snapshot or {}
        if previous.get("signature") == signature:
            return None  # nothing material changed; do not create a duplicate

    confidence = score_confidence(
        session, probability, vector, model, frame,
        model_agreement=agreement, lineup_confirmed=False,
    )
    starters_known = 0.5 * float(vector.features.get("sp_identified_home") or 0.0) + 0.5 * float(
        vector.features.get("sp_identified_away") or 0.0
    )
    recommendation = recommendation_label(
        probability, confidence.score, vector.completeness, starters_known
    )

    baseline = builder.league_baseline(ctx.season, vector.as_of)
    runs = project_runs(vector, baseline)

    home_name = team_names.get(ctx.home_team_id, str(ctx.home_team_id))
    away_name = team_names.get(ctx.away_team_id, str(ctx.away_team_id))
    contributions = build_contributions(model, frame, vector, home_name, away_name)
    warnings = build_warnings(vector, freshness, lineup_confirmed=False,
                              model_agreement=agreement)

    missing_data = _missing_data_labels(vector, freshness)

    prediction = Prediction(
        game_id=ctx.game_id,
        model_version_id=version.id,
        as_of=vector.as_of,
        home_win_prob=round(probability, 5),
        away_win_prob=round(1.0 - probability, 5),
        home_win_prob_uncalibrated=round(raw, 5),
        projected_home_runs=runs.home_runs,
        projected_away_runs=runs.away_runs,
        projected_home_runs_low=runs.home_low,
        projected_home_runs_high=runs.home_high,
        projected_away_runs_low=runs.away_low,
        projected_away_runs_high=runs.away_high,
        fair_home_moneyline=fair_moneyline(probability),
        fair_away_moneyline=fair_moneyline(1.0 - probability),
        market_home_prob=None,   # requires a licensed odds provider (Phase 3)
        market_edge=None,
        confidence_score=confidence.score,
        confidence_label=confidence.label,
        recommendation=recommendation,
        model_agreement=agreement,
        data_completeness=vector.completeness,
        missing_data=missing_data,
        warnings=warnings,
        feature_snapshot={
            "signature": signature,
            "features": vector.features,
            "sample_sizes": vector.sample_sizes,
            "estimated_flags": vector.estimated_flags,
            "missing_features": vector.missing_features,
            "run_projection": {
                "method": runs.method,
                "is_estimated": runs.is_estimated,
                "detail": runs.detail,
            },
        },
        component_probs=components,
        confidence_components=confidence.components,
        is_latest=True,
    )

    if existing is not None:
        session.execute(
            update(Prediction).where(Prediction.id == existing.id).values(is_latest=False)
        )
        session.flush()

    session.add(prediction)
    session.flush()

    if existing is not None:
        session.execute(
            update(Prediction)
            .where(Prediction.id == existing.id)
            .values(superseded_by=prediction.id)
        )

    for item in contributions[:20]:
        session.add(
            PredictionExplanation(
                prediction_id=prediction.id,
                rank=item.rank,
                feature_key=item.feature_key,
                display_name=item.display_name,
                category=item.category,
                favors=item.favors,
                contribution_pp=round(item.contribution_pp, 3),
                feature_value=item.feature_value,
                feature_display=item.feature_display,
                sample_size=item.sample_size,
                is_estimated=item.is_estimated,
                narrative=item.narrative,
            )
        )

    _persist_feature_row(session, ctx, vector)
    return prediction


def _missing_data_labels(vector: FeatureVector, freshness: dict[str, str]) -> list[str]:
    labels: list[str] = []
    if not vector.features.get("sp_identified_home"):
        labels.append("Home starting pitcher")
    if not vector.features.get("sp_identified_away"):
        labels.append("Away starting pitcher")
    for category, state in freshness.items():
        if state == "UNAVAILABLE" and category in (
            "lineups", "weather", "injuries", "odds"
        ):
            labels.append(category.replace("_", " ").title())
    return labels


def _persist_feature_row(session: Session, ctx: GameContext, vector: FeatureVector) -> None:
    """Store the as-of feature vector for audit and reproducibility."""
    for side, source in (("H", vector.home), ("A", vector.away)):
        payload = {
            key: (None if value.value is None else float(value.value))
            for key, value in source.values.items()
        }
        session.merge(
            ModelFeature(
                game_id=ctx.game_id,
                team_side=side,
                as_of=vector.as_of,
                feature_set_version=vector.feature_set_version,
                features=payload,
                sample_sizes={k: v.sample_size for k, v in source.values.items()},
                estimated_flags={k: v.is_estimated for k, v in source.values.items()},
                missing_features=[k for k, v in source.values.items() if v.value is None],
                completeness=vector.completeness,
                computed_at=utcnow(),
            )
        )


def generate_predictions_for_date(
    session: Session, target: date, force: bool = False
) -> int:
    """Generate predictions for every game on a date. Idempotent."""
    try:
        version, model = load_active_model(session)
    except ModelNotFoundError:
        log.warning("prediction.no_active_model")
        raise

    games = list(
        session.execute(
            select(
                Game.id, Game.season, Game.game_type, Game.game_date_utc,
                Game.official_date, Game.home_team_id, Game.away_team_id, Game.venue_id,
                Game.day_night, Game.doubleheader, Game.game_number,
                Game.home_probable_pitcher_id, Game.away_probable_pitcher_id,
            ).where(Game.official_date == target)
        ).mappings()
    )
    if not games:
        log.info("prediction.no_games", date=target.isoformat())
        return 0

    with job_run(session, "generate_predictions", date=target.isoformat()) as run:
        store = AsOfStore.load(session)
        elo = AsOfElo(store.games)
        builder = FeatureBuilder(store, elo)
        team_names = {
            row.id: row.name for row in session.execute(select(Team.id, Team.name)).all()
        }
        freshness = freshness_map(session)

        created = 0
        skipped = 0
        for row in games:
            ctx = GameContext.from_row(dict(row))
            as_of = _live_as_of(ctx.first_pitch_utc)
            try:
                vector = builder.build(ctx, as_of)
            except Exception as exc:
                log.warning(
                    "prediction.feature_build_failed", game_id=ctx.game_id, error=str(exc)
                )
                skipped += 1
                continue
            if not vector.is_usable:
                log.info(
                    "prediction.insufficient_history",
                    game_id=ctx.game_id,
                    completeness=vector.completeness,
                )
                skipped += 1
                continue
            prediction = generate_prediction(
                session, ctx, vector, model, version, builder, elo,
                team_names, freshness, force=force,
            )
            if prediction is not None:
                created += 1
        run.rows_written = created

    log.info(
        "prediction.generated", date=target.isoformat(), created=created,
        skipped=skipped, games=len(games),
    )
    return created


def latest_predictions_for_games(
    session: Session, game_ids: list[int]
) -> dict[int, Prediction]:
    if not game_ids:
        return {}
    rows = session.scalars(
        select(Prediction).where(
            Prediction.game_id.in_(game_ids), Prediction.is_latest.is_(True)
        )
    ).all()
    return {row.game_id: row for row in rows}


def prediction_history(session: Session, game_id: int, limit: int = 20) -> list[Prediction]:
    return list(
        session.scalars(
            select(Prediction)
            .where(Prediction.game_id == game_id)
            .order_by(Prediction.as_of.desc())
            .limit(limit)
        )
    )


def diff_predictions(current: Prediction, previous: Prediction | None) -> dict[str, Any]:
    """What changed since the previous prediction for this game."""
    if previous is None:
        return {
            "has_previous": False,
            "message": "This is the first prediction issued for this game.",
        }

    current_features = (current.feature_snapshot or {}).get("features", {}) or {}
    previous_features = (previous.feature_snapshot or {}).get("features", {}) or {}

    changes: list[dict[str, Any]] = []
    for key in sorted(set(current_features) | set(previous_features)):
        now_value, before = current_features.get(key), previous_features.get(key)
        if now_value is None and before is None:
            continue
        if now_value is None or before is None or abs(float(now_value) - float(before)) > 1e-9:
            changes.append({
                "feature_key": key,
                "previous": before,
                "current": now_value,
                "delta": (
                    round(float(now_value) - float(before), 6)
                    if now_value is not None and before is not None
                    else None
                ),
            })

    return {
        "has_previous": True,
        "previous_as_of": previous.as_of,
        "current_as_of": current.as_of,
        "home_win_prob_previous": float(previous.home_win_prob),
        "home_win_prob_current": float(current.home_win_prob),
        "home_win_prob_delta_pp": round(
            (float(current.home_win_prob) - float(previous.home_win_prob)) * 100, 2
        ),
        "confidence_previous": float(previous.confidence_score),
        "confidence_current": float(current.confidence_score),
        "completeness_previous": float(previous.data_completeness),
        "completeness_current": float(current.data_completeness),
        "n_changed_features": len(changes),
        "changed_features": changes[:25],
    }
