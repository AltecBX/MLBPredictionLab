"""BRIN instead of B-tree on knowledge_time for the append-only tables.

Phase 4 § Performance, and the number is the point rather than the idea.

The as-of store's dominant query is "every row knowable at this moment", which
on a backfilled database is very nearly the whole table. A B-tree on
`knowledge_time` tempted the planner into an index scan for exactly that query,
and an index scan that returns 333,501 of 333,520 rows is the slowest possible
way to read a table — one random heap fetch per row instead of a sequential
read:

    B-tree, index scan          5,108 ms
    BRIN, parallel seq + sort     514 ms

Ten times faster, and the index shrinks from 2,696 kB to 24 kB.

BRIN works here because these tables are append-only in time order, so
`knowledge_time` is almost perfectly correlated with physical position —
measured at 0.9995 for `player_game_stats`, 0.9995 for `team_game_stats` and
0.9908 for `pitches`. A BRIN stores one summary per 128-page range instead of
one entry per row, which is why it is 112 times smaller and why it does not
distort the planner's estimate for a full scan.

Selective range queries do not regress: a one-week window over
`player_game_stats` still answers in about 2 ms through a bitmap heap scan.

`games` keeps its B-tree. Its correlation is 0.77 — it is written and rewritten
as schedules change rather than purely appended — and at 288 kB the index is not
worth the risk of a worse plan on a table small enough that the scan is cheap
either way.

Revision ID: c93b5e17d204
Revises: b7c41d9e2af3
"""

from __future__ import annotations

from alembic import op

revision = "c93b5e17d204"
down_revision = "b7c41d9e2af3"
branch_labels = None
depends_on = None

#: (table, old B-tree name). Only the append-only tables large enough for the
#: plan to matter; the reference tables are a few kilobytes either way.
TABLES = [
    ("player_game_stats", "ix_player_game_stats_knowledge_time"),
    ("team_game_stats", "ix_team_game_stats_knowledge_time"),
    ("pitches", "ix_pitches_knowledge_time"),
    ("batted_ball_events", "ix_batted_ball_events_knowledge_time"),
    ("lineups", "ix_lineups_knowledge_time"),
    ("game_officials", "ix_game_officials_knowledge_time"),
]


def _brin_name(table: str) -> str:
    return f"ix_{table}_knowledge_time_brin"


def upgrade() -> None:
    for table, btree in TABLES:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {_brin_name(table)} "
            f"ON {table} USING brin (knowledge_time)"
        )
        op.execute(f"DROP INDEX IF EXISTS {btree}")

    # Applied by hand while measuring; make the migration idempotent against a
    # database that already has it rather than failing on the duplicate.
    op.execute("DROP INDEX IF EXISTS brin_pgs_kt")


def downgrade() -> None:
    for table, btree in TABLES:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {btree} ON {table} (knowledge_time)"
        )
        op.execute(f"DROP INDEX IF EXISTS {_brin_name(table)}")
