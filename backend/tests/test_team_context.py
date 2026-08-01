"""Standings, splits and streaks.

Runs against a throwaway PostgreSQL database seeded with an explicitly synthetic
four-team league. Skips rather than silently passing if none is reachable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.base import Base
from app.db.models import Game, Team
from app.services import team_context as tc

TEST_DB = "jerry_mlb_context_test"

# Two two-team divisions inside one league, so division rank, wild card and
# elimination all have something to resolve against.
TEAMS = [
    (1, "Alphas", "ALP", 100, "Test League", 200, "Test League East"),
    (2, "Bravos", "BRV", 100, "Test League", 200, "Test League East"),
    (3, "Charlies", "CHR", 100, "Test League", 201, "Test League West"),
    (4, "Deltas", "DLT", 100, "Test League", 201, "Test League West"),
]

SEASON = 2024
START = datetime(SEASON, 4, 1, 23, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def engine():
    admin_url = str(settings.database_url).rsplit("/", 1)[0] + "/postgres"
    try:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
        admin.dispose()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"PostgreSQL is not reachable: {exc}")

    eng = create_engine(str(settings.database_url).rsplit("/", 1)[0] + f"/{TEST_DB}")
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


def _game(index: int, home: int, away: int, home_score: int, away_score: int) -> Game:
    when = START + timedelta(days=index)
    return Game(
        id=9000 + index,
        season=SEASON,
        game_type="R",
        game_date_utc=when,
        official_date=when.date(),
        home_team_id=home,
        away_team_id=away,
        home_score=home_score,
        away_score=away_score,
        home_win=home_score > away_score,
        is_final=True,
        knowledge_time=when + timedelta(hours=3),
        source_name="test-fixture",
        retrieved_at=when,
    )


@pytest.fixture()
def seeded(engine):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        session.execute(text("TRUNCATE games, teams CASCADE"))
        for tid, name, abbr, lid, lname, did, dname in TEAMS:
            session.add(
                Team(
                    id=tid, name=name, abbreviation=abbr, team_name=name,
                    league_id=lid, league_name=lname,
                    division_id=did, division_name=dname,
                    source_name="test-fixture", retrieved_at=START,
                    knowledge_time=START,
                )
            )
        session.flush()  # games carry an FK to teams
        # Alphas: 3-0 at home over Bravos, then 0-2 on the road -> current L2.
        games = [
            _game(0, 1, 2, 5, 1),
            _game(1, 1, 2, 4, 2),
            _game(2, 1, 2, 6, 3),
            _game(3, 3, 1, 7, 2),
            _game(4, 4, 1, 8, 1),
        ]
        session.add_all(games)
        session.commit()
        yield Session


def test_home_and_away_records_split_correctly(seeded):
    with seeded() as session:
        ctx = tc.team_contexts(session, SEASON, START + timedelta(days=30))

        alphas = ctx[1]
        assert (alphas.home.wins, alphas.home.losses) == (3, 0)
        assert (alphas.away.wins, alphas.away.losses) == (0, 2)
        assert (alphas.overall.wins, alphas.overall.losses) == (3, 2)
        assert alphas.home.win_pct == 1.0
        assert alphas.away.win_pct == 0.0
        assert alphas.overall.win_pct == 0.6


def test_a_team_with_no_games_has_no_win_percentage():
    """.000 would render as a real, terrible record. None renders as nothing."""
    assert tc.Record(0, 0).win_pct is None
    assert tc.Record(0, 1).win_pct == 0.0


def test_streak_reports_length_dates_and_opponents(seeded):
    with seeded() as session:
        ctx = tc.team_contexts(session, SEASON, START + timedelta(days=30))

        alphas = ctx[1].streak
        assert alphas is not None
        assert alphas.kind == "L"
        assert alphas.length == 2
        assert alphas.label == "L2"
        assert [g.opponent for g in alphas.games] == ["Charlies", "Deltas"]
        assert [g.date for g in alphas.games] == [
            (START + timedelta(days=3)).date().isoformat(),
            (START + timedelta(days=4)).date().isoformat(),
        ]
        # Scores are from the streaking team's point of view, both losses.
        assert all(g.runs_for < g.runs_against for g in alphas.games)
        assert all(g.is_home is False for g in alphas.games)

        # The other side of those same games is a win streak.
        assert ctx[3].streak.kind == "W"
        assert ctx[2].streak.label == "L3"


def test_standings_rank_by_win_pct_and_compute_games_behind(seeded):
    with seeded() as session:
        ctx = tc.team_contexts(session, SEASON, START + timedelta(days=30))

        assert ctx[1].standing.division_rank == 1  # 3-2
        assert ctx[2].standing.division_rank == 2  # 0-3
        assert ctx[1].standing.games_behind == 0.0
        # Alphas 3-2 vs Bravos 0-3: (3-0 + 3-2)/2 = 2.0
        assert ctx[2].standing.games_behind == 2.0


def test_exactly_one_leader_per_division(seeded):
    with seeded() as session:
        ctx = tc.team_contexts(session, SEASON, START + timedelta(days=30))
        leaders = [c for c in ctx.values() if c.standing.division_rank == 1]
        assert len(leaders) == 2  # two divisions in the fixture


def test_elimination_number_matches_the_tragic_number_formula(seeded):
    """E = (team's win ceiling) - (leader's wins) + 1."""
    with seeded() as session:
        ctx = tc.team_contexts(session, SEASON, START + timedelta(days=30))

        leader, trailer = ctx[1], ctx[2]
        remaining = tc.SCHEDULED_GAMES - trailer.overall.games
        expected = (trailer.overall.wins + remaining) - leader.overall.wins + 1
        assert trailer.standing.elimination_number == expected
        assert leader.standing.elimination_number is None  # a leader has none
        assert trailer.standing.eliminated is False


def test_context_never_includes_the_game_it_is_shown_beside(seeded):
    """The as-of cut that protects the model protects the standings too.

    Cutting at a game's own first pitch must exclude that game's result, or a
    card would show a record that already knows how the game it is describing
    turned out.
    """
    with seeded() as session:
        third_game = START + timedelta(days=2)

        before = tc.team_contexts(session, SEASON, third_game)
        after = tc.team_contexts(session, SEASON, third_game + timedelta(days=1))

        # Two games are knowable before the third starts; three after it ends.
        assert before[1].overall.games == 2
        assert after[1].overall.games == 3


def test_knowledge_time_is_respected_independently_of_game_date(seeded):
    """A backfilled result becomes visible when it became knowable, not before."""
    with seeded() as session:
        first = START + timedelta(days=0)
        # The game starts at `first` but is only knowable three hours later.
        assert tc.team_contexts(session, SEASON, first + timedelta(hours=1)) == {}
        visible = tc.team_contexts(session, SEASON, first + timedelta(hours=4))
        assert visible[1].overall.games == 1


def test_streaks_and_standings_are_declared_display_only():
    """The module must never feed the model.

    Standings duplicate elo/win-pct/pythag with a coarser encoding, and streak
    length is the small-sample trap the stabilized form delta exists to contain.
    """
    from app.features.registry import FS_V1

    assert tc.STREAK_IS_CONTEXT_ONLY is True
    banned = ("streak", "division_rank", "games_behind", "wildcard", "elimination")
    offenders = [
        s.key for s in FS_V1 if any(token in s.key for token in banned)
    ]
    assert offenders == [], (
        f"{offenders} entered the active feature set without walk-forward "
        f"ablation evidence (BACKTEST_PLAN.md §7)."
    )


# --- matchup summary --------------------------------------------------------

def _card_with(home_standing=None, away_standing=None, home_home=None, away_away=None):
    """A minimal GameCard carrying only what the summary rows read."""
    from app.schemas.common import BallparkRef, PitcherRef, TeamRef
    from app.schemas.games import GameCard as CardSchema

    def team(tid, abbr, standing, home_rec, away_rec):
        return TeamRef(
            id=tid, name=abbr, abbreviation=abbr,
            home_record=home_rec, away_record=away_rec, standing=standing,
        )

    return CardSchema(
        game_id=1, season=2024, game_type="R",
        official_date=START.date(), first_pitch_utc=START, status="Preview",
        home=team(1, "HME", home_standing, home_home, None),
        away=team(2, "AWY", away_standing, None, away_away),
        ballpark=BallparkRef(), home_pitcher=PitcherRef(), away_pitcher=PitcherRef(),
    )


def test_summary_is_always_the_same_nine_rows_in_the_same_order():
    """Two games must be comparable line by line."""
    from app.services.matchup_summary import build_matchup_summary

    rows = build_matchup_summary(_card_with(), [])
    assert [r.key for r in rows] == [
        "home_away", "starting_pitcher", "lineup", "bullpen", "recent_form",
        "season_strength", "division", "probability", "confidence",
    ]


def test_an_unconfigured_provider_is_unavailable_not_even():
    """"Even" is a measurement. "Unavailable" is the absence of one."""
    from app.services.matchup_summary import build_matchup_summary

    lineup = next(r for r in build_matchup_summary(_card_with(), []) if r.key == "lineup")
    assert lineup.advantage == "UNAVAILABLE"
    assert lineup.available is False
    assert lineup.required_source == "LINEUP_PROVIDER"
    assert lineup.advantage != "EVEN"


def test_division_rank_is_not_compared_across_different_divisions():
    """#4 in one division beats #5 in another in no sense whatsoever."""
    from app.schemas.common import StandingSummary
    from app.services.matchup_summary import build_matchup_summary

    east = StandingSummary(division_name="East", division_rank=4, games_behind=10.0)
    west = StandingSummary(division_name="West", division_rank=5, games_behind=13.0)

    row = next(
        r for r in build_matchup_summary(_card_with(east, west), []) if r.key == "division"
    )
    assert row.advantage == "EVEN"
    assert row.team is None
    assert "different divisions" in (row.detail or "")

    # Inside one division the comparison is meaningful and is made.
    same = StandingSummary(division_name="East", division_rank=2, games_behind=3.0)
    row = next(
        r for r in build_matchup_summary(_card_with(east, same), []) if r.key == "division"
    )
    assert row.advantage == "AWAY"  # rank 2 beats rank 4


def test_context_rows_are_marked_as_carrying_no_probability_weight():
    from app.schemas.common import RecordSplit, StandingSummary
    from app.services.matchup_summary import build_matchup_summary

    # Records and standings present, so the context rows are actually populated
    # rather than falling through to UNAVAILABLE — an absent row is not a
    # context row.
    card = _card_with(
        home_standing=StandingSummary(division_name="East", division_rank=1),
        away_standing=StandingSummary(division_name="East", division_rank=3),
        home_home=RecordSplit(wins=30, losses=20, win_pct=0.6),
        away_away=RecordSplit(wins=20, losses=30, win_pct=0.4),
    )
    rows = {r.key: r for r in build_matchup_summary(card, [])}
    assert rows["home_away"].is_context is True
    assert rows["division"].is_context is True
    # Contribution rows are not context.
    assert rows["starting_pitcher"].is_context is False
