"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Generator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db


def db_session() -> Generator[Session, None, None]:
    yield from get_db()


def require_admin(x_admin_key: str | None = Header(default=None)) -> None:
    """Admin routes are disabled when no key is configured, never left open."""
    if not settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Admin API is disabled. Set ADMIN_API_KEY to enable it.",
        )
    if x_admin_key != settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin key."
        )


SessionDep = Depends(db_session)
AdminDep = Depends(require_admin)
