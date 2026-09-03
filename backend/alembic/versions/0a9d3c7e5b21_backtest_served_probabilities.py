"""Store what was served, per backtest game, beside the logistic component.

The backtest scored the logistic model alone, and the product serves a
log-odds blend of that model with the run simulation. The two are not the same
number and do not calibrate the same way — the logistic's tails run
overconfident, the simulation's run the other way, and the blend sits between —
so a reliability report on the component was not a reliability report on the
served figure. Every backtest row now carries the served probability and the
simulation's, so the served slices can be recomputed from the rows like every
other slice.

Nullable: rows from runs before this column existed have no served figure and
say so, rather than reporting the logistic as if it were one.

Revision ID: 0a9d3c7e5b21
Revises: a71b3ce905f2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0a9d3c7e5b21"
down_revision = "a71b3ce905f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "backtest_predictions",
        sa.Column("served_home_win_prob", sa.Numeric(6, 5), nullable=True),
    )
    op.add_column(
        "backtest_predictions",
        sa.Column("simulated_home_win_prob", sa.Numeric(6, 5), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("backtest_predictions", "simulated_home_win_prob")
    op.drop_column("backtest_predictions", "served_home_win_prob")
