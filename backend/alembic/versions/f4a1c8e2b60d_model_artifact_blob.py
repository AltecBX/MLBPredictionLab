"""Store the model artifact, not just where it was written.

`model_versions.artifact_path` is a path on the filesystem of whichever machine
ran the training. Nothing else can read it, and that is not a theoretical
limitation: the hourly pregame job reissues predictions without retraining, runs
on a fresh GitHub runner, and failed every hour with

    FileNotFoundError: artifacts/models/jerry_logistic/v1/model.pkl

— a registry row pointing at a file that only ever existed somewhere else. The
timeline the job exists to build was never accumulating.

The database is the one durable store every process here can reach: the API on
Render, the daily refresh, the pregame poller. A logistic model over forty-odd
features pickles to a few kilobytes.

Nullable, because rows registered before this column existed keep their path and
still load on the machine that trained them.

Revision ID: f4a1c8e2b60d
Revises: d51a8c30f7e6
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f4a1c8e2b60d"
down_revision = "d51a8c30f7e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "model_versions",
        sa.Column("artifact_blob", sa.LargeBinary(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("model_versions", "artifact_blob")
