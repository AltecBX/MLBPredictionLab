"""A player can be placed on the injured list more than once.

`injuries` had only a non-unique index, which supports neither an upsert nor the
fact that being hurt is an interval rather than a state. The key is
(player_id, effective_from, status): the same player, the same day, the same
kind of move is one row, and a second stint later in the season is another.

Revision ID: d51a8c30f7e6
Revises: c93b5e17d204
"""

from __future__ import annotations

from alembic import op

revision = "d51a8c30f7e6"
down_revision = "c93b5e17d204"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_injury_player_effective_status",
        "injuries",
        ["player_id", "effective_from", "status"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_injury_player_effective_status", "injuries", type_="unique")
