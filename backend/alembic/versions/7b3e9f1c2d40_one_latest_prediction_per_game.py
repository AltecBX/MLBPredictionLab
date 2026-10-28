"""One `is_latest` prediction per game, not per (game, model version).

`generate_prediction` retired the previous snapshot by looking for an
`is_latest` row with the SAME model version, and the partial unique index
allowed one latest row per (game_id, model_version_id). That was harmless
while the model version rarely changed. Since the daily refresh retrains every
morning, every day's prediction has a new version, never finds yesterday's,
and leaves it marked latest beside it — 381 games carried two or more "latest"
predictions by October 2026.

Every reader treats `is_latest` as one row per game.
`latest_predictions_for_games` builds a dict keyed by game, so the one a
reader saw was whichever the database returned last. Observed on the
deployment: a World Series game served at 46.7% by the newest snapshot was
shown at 56.6% from the previous day's.

This keeps the newest snapshot of each game as its latest, points each older
one at the snapshot that followed it, and replaces the index with one that
allows a single latest row per game. `generate_prediction` now supersedes the
game's latest whatever model produced it, so the index is a guard rather than
a thing to work around.

Revision ID: 7b3e9f1c2d40
Revises: 0a9d3c7e5b21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "7b3e9f1c2d40"
down_revision = "0a9d3c7e5b21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Of the rows marked latest, only the newest of each game stays latest.
    # Each one retired here is pointed at the snapshot of the same game that
    # came after it, which is what `superseded_by` means everywhere else.
    op.execute(
        """
        WITH ordered AS (
            SELECT id,
                   is_latest,
                   LEAD(id) OVER (
                       PARTITION BY game_id ORDER BY created_at, id
                   ) AS next_id
            FROM predictions
        )
        UPDATE predictions AS p
        SET is_latest = FALSE,
            superseded_by = COALESCE(p.superseded_by, ordered.next_id)
        FROM ordered
        WHERE p.id = ordered.id
          AND ordered.is_latest
          AND ordered.next_id IS NOT NULL
        """
    )
    op.drop_index("uq_prediction_latest", table_name="predictions")
    op.create_index(
        "uq_prediction_latest",
        "predictions",
        ["game_id"],
        unique=True,
        postgresql_where=sa.text("is_latest IS true"),
    )


def downgrade() -> None:
    op.drop_index("uq_prediction_latest", table_name="predictions")
    op.create_index(
        "uq_prediction_latest",
        "predictions",
        ["game_id", "model_version_id"],
        unique=True,
        postgresql_where=sa.text("is_latest IS true"),
    )
