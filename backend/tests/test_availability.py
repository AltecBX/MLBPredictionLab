"""Roster availability: the state machine, the weighting, and the leak.

Three things can go wrong here and only one of them is arithmetic.

The state machine is the subtle one. `injuries` is an event log — an `IL` row
followed eventually by an `ACTIVE` row — with roughly 1,700 stints across three
seasons whose closing row never arrived. Read naively that marks a thousand
players unavailable on a midsummer day. The window is what makes it a state
machine rather than an accumulator, and these tests pin the transitions.

The leak is the ordinary one, and it has a specific shape here: `effective_from`
on an IL row is routinely backdated to the last game the player appeared in,
days before the transaction was announced. Filtering on it would let a Tuesday
prediction read a Thursday announcement.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app.features.availability import (
    IL_RECENCY_DAYS,
    availability_loss,
    unavailable_as_of,
)

AS_OF = datetime(2025, 6, 15, tzinfo=UTC)


def _injuries(rows: list[tuple[int, str, datetime]]) -> tuple[pd.DataFrame, np.ndarray]:
    """Build the frame in the shape the store guarantees: sorted, with an index."""
    frame = pd.DataFrame(
        [
            {"id": i, "player_id": pid, "status": status, "knowledge_time": kt}
            for i, (pid, status, kt) in enumerate(rows, start=1)
        ]
    )
    frame = frame.sort_values(["knowledge_time", "id"]).reset_index(drop=True)
    stamps = frame["knowledge_time"].to_numpy(dtype="datetime64[ns]").astype("int64")
    return frame, stamps


def test_a_recent_placement_with_nothing_after_it_counts():
    frame, ns = _injuries([(1, "IL", AS_OF - timedelta(days=3))])
    assert unavailable_as_of(frame, ns, AS_OF) == {1}


def test_a_return_supersedes_the_placement_it_follows():
    """The whole point of reading the last row rather than any row."""
    frame, ns = _injuries(
        [
            (1, "IL", AS_OF - timedelta(days=20)),
            (1, "ACTIVE", AS_OF - timedelta(days=2)),
        ]
    )
    assert unavailable_as_of(frame, ns, AS_OF) == set()


def test_a_placement_after_a_return_counts_again():
    frame, ns = _injuries(
        [
            (1, "IL", AS_OF - timedelta(days=25)),
            (1, "ACTIVE", AS_OF - timedelta(days=10)),
            (1, "IL", AS_OF - timedelta(days=1)),
        ]
    )
    assert unavailable_as_of(frame, ns, AS_OF) == {1}


def test_a_stale_placement_is_dropped():
    """Measured, not assumed: 91% of these players are playing.

    A placement older than the window is overwhelmingly a stint whose closing
    `ACTIVE` row was never ingested, so treating it as live would report a
    healthy regular as missing every day for the rest of the season.
    """
    frame, ns = _injuries([(1, "IL", AS_OF - timedelta(days=IL_RECENCY_DAYS + 1))])
    assert unavailable_as_of(frame, ns, AS_OF) == set()


def test_a_placement_reported_after_the_prediction_is_invisible():
    """The as-of guard, on the table where backdating makes it easiest to lose."""
    frame, ns = _injuries([(1, "IL", AS_OF + timedelta(hours=1))])
    assert unavailable_as_of(frame, ns, AS_OF) == set()


def test_nothing_reads_effective_from():
    """An IL row's `effective_from` is backdated to the player's last game.

    A prediction may only know what was *reported* by then, which is
    `knowledge_time`. This is asserted against the source rather than against
    behaviour because the failure mode is a column name appearing in a query,
    where no unit test would see it.

    Executable code only — attribute accesses and string literals that are not
    docstrings. Prose explaining why the column is off limits is the opposite of
    a violation, and a plain grep flags this module's own docstring for saying
    so.
    """
    import ast
    from pathlib import Path

    app = Path(__file__).resolve().parents[1] / "app"
    offenders: list[str] = []
    for path in (app / "features").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(
                node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
            )
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute) and node.attr == "effective_from"
            ) or (
                isinstance(node, ast.Constant)
                and node.value == "effective_from"
                and id(node) not in docstrings
            ):
                offenders.append(f"{path.relative_to(app)}:{node.lineno}")

    assert not offenders, (
        "The feature layer must filter injuries on knowledge_time, never on "
        f"effective_from (backdated): {offenders}"
    )


# --------------------------------------------------------------------------
# Weighting
# --------------------------------------------------------------------------


class _Store:
    """Just enough store to exercise the share arithmetic."""

    def __init__(self, batting: pd.DataFrame, pitching: pd.DataFrame, out: set[int]):
        self.injuries = pd.DataFrame({"id": [1]})
        self._batting = batting
        self._pitching = pitching
        self._out = frozenset(out)

    def unavailable_asof(self, as_of):  # noqa: ARG002
        return self._out

    def team_batter_games_asof(self, team_id, as_of, start=None):  # noqa: ARG002
        return self._batting

    def team_pitcher_games_asof(self, team_id, as_of, start=None):  # noqa: ARG002
        return self._pitching


def _batting(rows: list[tuple[int, int, int, int]]) -> pd.DataFrame:
    """(player_id, pa, singles, home_runs) repeated enough to clear MIN_TEAM_PA."""
    return pd.DataFrame(
        [
            {
                "player_id": pid, "pa": pa, "hits": singles + hr, "doubles": 0,
                "triples": 0, "home_runs": hr, "bb": 0, "ibb": 0, "hbp": 0,
            }
            for pid, pa, singles, hr in rows
        ]
    )


def test_the_loss_is_a_share_of_production_not_a_headcount():
    """A cleanup hitter and a bench bat are not the same event.

    Two players, identical plate appearances, wildly different production. The
    share must follow the production.
    """
    batting = _batting([(1, 200, 0, 40), (2, 200, 20, 0)])
    pitching = pd.DataFrame({"player_id": [9], "batters_faced": [800]})

    star_out = availability_loss(_Store(batting, pitching, {1}), 1, AS_OF)
    scrub_out = availability_loss(_Store(batting, pitching, {2}), 1, AS_OF)

    assert star_out.offense is not None and scrub_out.offense is not None
    assert star_out.offense > 0.7 > scrub_out.offense
    assert star_out.batters_out == scrub_out.batters_out == 1


def test_nobody_out_is_zero_and_no_window_is_unavailable():
    """`EVEN` and `UNAVAILABLE` are different states (CLAUDE.md)."""
    batting = _batting([(1, 300, 60, 10)])
    pitching = pd.DataFrame({"player_id": [9], "batters_faced": [800]})

    nobody = availability_loss(_Store(batting, pitching, set()), 1, AS_OF)
    assert nobody.offense == pytest.approx(0.0)

    blank = availability_loss(
        _Store(pd.DataFrame(), pd.DataFrame(), {1}), 1, AS_OF
    )
    assert blank.offense is None
    assert blank.pitching is None


def test_a_window_too_small_to_divide_reports_missing():
    """Twenty plate appearances is not a team, and 0/20 is not a finding."""
    batting = _batting([(1, 20, 4, 1)])
    pitching = pd.DataFrame({"player_id": [9], "batters_faced": [20]})
    loss = availability_loss(_Store(batting, pitching, set()), 1, AS_OF)
    assert loss.offense is None
    assert loss.pitching is None


def test_pitching_is_weighted_by_batters_faced():
    pitching = pd.DataFrame(
        {"player_id": [1, 2], "batters_faced": [600, 200]}
    )
    batting = _batting([(5, 300, 60, 10)])
    loss = availability_loss(_Store(batting, pitching, {1}), 1, AS_OF)
    assert loss.pitching == pytest.approx(0.75)
    assert loss.pitchers_out == 1
