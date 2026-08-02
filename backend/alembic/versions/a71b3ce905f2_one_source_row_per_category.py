"""One `data_source_status` row per category.

`seed_source_status` writes a placeholder named `unavailable::<category>` at
bootstrap, and `record_source_status` matched on (source_name, category) — so
the first real ingest for a category inserted a SECOND row beside the
placeholder instead of replacing it.

That is worse than the stale row it was meant to replace. `freshness_report`
builds a dict keyed by category, so with two rows the one a reader sees is
whichever the database returned last. Observed on the deployment: lineups
reported OK and weather reported UNAVAILABLE on the same page, written by the
same code in the same run.

This deletes any placeholder whose category already has a real row, then adds
the unique constraint that stops it recurring. `record_source_status` now takes
the placeholder over rather than inserting beside it, so the constraint is a
guard rather than a thing to work around.

Revision ID: a71b3ce905f2
Revises: f4a1c8e2b60d
"""

from __future__ import annotations

from alembic import op

revision = "a71b3ce905f2"
down_revision = "f4a1c8e2b60d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A placeholder is only ever a stand-in. Where a category has been ingested
    # for real, the placeholder is the row that must go — never the other way
    # round, or a working feed loses its last_success_at.
    op.execute(
        """
        DELETE FROM data_source_status AS stale
        USING data_source_status AS live
        WHERE stale.category = live.category
          AND stale.source_name LIKE 'unavailable::%'
          AND live.source_name NOT LIKE 'unavailable::%'
        """
    )
    # Any remaining duplicates within a category keep the most recently
    # updated row, which is the one carrying real ingest history.
    op.execute(
        """
        DELETE FROM data_source_status AS d
        USING data_source_status AS keep
        WHERE d.category = keep.category
          AND d.id <> keep.id
          AND (
                keep.updated_at > d.updated_at
                OR (keep.updated_at = d.updated_at AND keep.id > d.id)
              )
        """
    )
    op.create_unique_constraint(
        "uq_data_source_status_category", "data_source_status", ["category"]
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_data_source_status_category", "data_source_status", type_="unique"
    )
