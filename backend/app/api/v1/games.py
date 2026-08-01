"""Daily game center and game detail endpoints."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import db_session
from app.core.clock import utcnow
from app.core.config import settings
from app.db.models import Game, ModelVersion
from app.schemas.common import FreshnessEntry
from app.schemas.games import GameDetail, GameListResponse
from app.services.freshness import freshness_report
from app.services.game_view import (
    SORT_KEYS,
    build_game_cards,
    build_game_detail,
    load_games_for_date,
    sort_cards,
)
from app.services.prediction import latest_predictions_for_games

router = APIRouter(prefix="/games", tags=["games"])


def _active_model_label(session: Session) -> str | None:
    row = session.scalar(
        select(ModelVersion).where(
            ModelVersion.name == settings.active_model_name,
            ModelVersion.is_active.is_(True),
        )
    )
    return f"{row.name}:{row.version}" if row else None


@router.get("", response_model=GameListResponse, summary="Games and predictions for a date")
def list_games(
    game_date: date = Query(default=None, alias="date"),
    sort: str = Query(default="game_time"),
    session: Session = Depends(db_session),
) -> GameListResponse:
    if sort not in SORT_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown sort {sort!r}. Valid options: {sorted(SORT_KEYS)}",
        )
    target = game_date or utcnow().date()

    games = load_games_for_date(session, target)
    predictions = latest_predictions_for_games(session, [g.id for g in games])
    cards = sort_cards(build_game_cards(session, games, predictions), sort)

    return GameListResponse(
        date=target,
        count=len(cards),
        generated_at=utcnow(),
        model_version=_active_model_label(session),
        freshness=[FreshnessEntry(**row) for row in freshness_report(session)],
        games=cards,
    )


@router.get("/{game_id}", response_model=GameDetail, summary="Full game detail")
def get_game(game_id: int, session: Session = Depends(db_session)) -> GameDetail:
    game = session.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail=f"Game {game_id} not found.")
    predictions = latest_predictions_for_games(session, [game_id])
    return build_game_detail(session, game, predictions.get(game_id))


@router.get("/{game_id}/predictions", summary="Immutable prediction history for a game")
def get_prediction_history(game_id: int, session: Session = Depends(db_session)) -> dict:
    from app.services.prediction import diff_predictions, prediction_history

    game = session.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail=f"Game {game_id} not found.")

    history = prediction_history(session, game_id, limit=50)
    if not history:
        return {
            "game_id": game_id,
            "count": 0,
            "predictions": [],
            "change_since_previous": {
                "has_previous": False,
                "message": "No prediction has been issued for this game yet.",
            },
        }

    return {
        "game_id": game_id,
        "count": len(history),
        "predictions": [
            {
                "id": p.id,
                "as_of": p.as_of,
                "created_at": p.created_at,
                "model_version_id": p.model_version_id,
                "home_win_prob": float(p.home_win_prob),
                "away_win_prob": float(p.away_win_prob),
                "confidence_score": float(p.confidence_score),
                "confidence_label": p.confidence_label,
                "recommendation": p.recommendation,
                "data_completeness": float(p.data_completeness),
                "is_latest": p.is_latest,
                "superseded_by": p.superseded_by,
            }
            for p in history
        ],
        "change_since_previous": diff_predictions(
            history[0], history[1] if len(history) > 1 else None
        ),
    }
