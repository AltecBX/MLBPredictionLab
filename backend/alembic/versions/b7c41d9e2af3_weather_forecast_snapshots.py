"""Weather forecasts become append-only snapshots.

A forecast is not a fact about a game, it is a fact about a moment: the forecast
for tonight at noon and the forecast for tonight at 4pm are two different rows,
both true, and which one a prediction may read depends on when the prediction
was made. `ix_weather_game` was a plain index on (game_id, observation_type),
which supports neither an upsert nor that history.

The unique key gains `knowledge_time`, so re-forecasting the same game appends
rather than overwrites and the as-of filter picks whichever snapshots existed at
the moment being reconstructed. That is the same shape `lineups` uses and the
same rule LEAKAGE_PREVENTION.md applies everywhere else.

Revision ID: b7c41d9e2af3
Revises: e37fd67a9dfd
"""

from __future__ import annotations

from alembic import op

revision = "b7c41d9e2af3"
down_revision = "e37fd67a9dfd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_weather_game_type_knowledge",
        "weather",
        ["game_id", "observation_type", "knowledge_time"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_weather_game_type_knowledge", "weather", type_="unique")
