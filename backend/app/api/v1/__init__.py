from fastapi import APIRouter

from app.api.v1.backtest import router as backtest_router
from app.api.v1.diagnostics import router as diagnostics_router
from app.api.v1.games import router as games_router
from app.api.v1.meta import router as meta_router
from app.api.v1.streaks import router as streaks_router

api_router = APIRouter()
api_router.include_router(games_router)
api_router.include_router(backtest_router)
api_router.include_router(diagnostics_router)
api_router.include_router(meta_router)
api_router.include_router(streaks_router)

__all__ = ["api_router"]
