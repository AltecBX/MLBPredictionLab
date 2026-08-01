"""Shared idempotent upsert helper.

Rows are normalized to a common key set before the multi-values INSERT, because
PostgreSQL requires a uniform column list and callers legitimately produce
heterogeneous dicts (a batter line and a pitcher line carry different fields).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

BATCH_SIZE = 500


def upsert(
    session: Session,
    model: type,
    rows: Sequence[dict[str, Any]],
    conflict_cols: list[str],
    *,
    update: bool = True,
    batch_size: int = BATCH_SIZE,
) -> int:
    if not rows:
        return 0

    columns = {c.name for c in model.__table__.columns}
    keys: set[str] = set()
    for row in rows:
        keys.update(k for k in row if k in columns)
    ordered = sorted(keys)

    payload = [{k: row.get(k) for k in ordered} for row in rows]

    # PostgreSQL rejects an ON CONFLICT DO UPDATE that touches the same row
    # twice in one statement, so collapse duplicates on the conflict key first,
    # keeping the last occurrence (the most recently fetched view of the fact).
    if all(c in ordered for c in conflict_cols):
        deduped: dict[tuple, dict[str, Any]] = {}
        for row in payload:
            deduped[tuple(row[c] for c in conflict_cols)] = row
        payload = list(deduped.values())

    update_cols = [c for c in ordered if c not in conflict_cols]

    written = 0
    for start in range(0, len(payload), batch_size):
        chunk = payload[start : start + batch_size]
        stmt = insert(model).values(chunk)
        if update and update_cols:
            stmt = stmt.on_conflict_do_update(
                index_elements=conflict_cols,
                set_={c: getattr(stmt.excluded, c) for c in update_cols},
            )
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=conflict_cols)
        session.execute(stmt)
        written += len(chunk)
    return written
