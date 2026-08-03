"""Winning and losing streaks — research and display, never a model input.

The payload is computed from completed games at request time; every rate in it
is documented in `app.services.streaks`, including the shrinkage constant and
the expectation model the adjusted effect is measured against. The response is
a plain dict rather than a rigid schema on purpose: the shape is versioned by
the service, the page consumes it wholesale, and no field in it is ever a
stand-in for a missing value — absent things are absent or carry a reason.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_session
from app.services.streaks import build_streaks_payload

router = APIRouter(prefix="/streaks", tags=["streaks"])


@router.get("")
def get_streaks(session: Session = Depends(db_session)) -> dict[str, Any]:
    return build_streaks_payload(session)
