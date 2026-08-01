"""Statcast client refusals, normalization, and two-source reconciliation.

The frames here are TEST FIXTURES built in memory — explicitly synthetic, never
served to a user and never written to the application database. The column names
are the ones Baseball Savant's `statcast_search/csv` export actually uses, taken
from a real 2024-07-04 response (119 columns; the ones this system reads are
reproduced below), so a rename upstream fails here first.

The database-backed tests run against a throwaway PostgreSQL database and skip,
rather than silently pass, if none is reachable.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.base import Base
from app.db.models import (
    BattedBallEvent,
    Game,
    Pitch,
    Player,
    Team,
    TeamGameStat,
)
from app.ingestion import statcast as ingest
from app.providers.base import DataCategory, ProviderResult, ProviderStatus
from app.providers.baseball_savant import mappers
from app.providers.baseball_savant.client import ALLOWED_PATH, BaseballSavantClient

TEST_DB = "jerry_mlb_statcast_test"

GAME_ID = 745001
HOME_TEAM, AWAY_TEAM = 111, 146
PITCHER, BATTER = 660271, 592450
FIRST_PITCH = datetime(2024, 7, 4, 23, 5, tzinfo=UTC)
GAME_END = FIRST_PITCH + timedelta(hours=3)

# Every date bound a legal request must carry.
BOUNDED = {"game_date_gt": "2024-07-04", "game_date_lt": "2024-07-04"}


# --------------------------------------------------------------------------
# Client: what it refuses to ask for
# --------------------------------------------------------------------------


def test_client_refuses_any_path_but_the_search_export():
    """Savant's leaderboards are current-season totals (LEAKAGE_PREVENTION §14)."""
    with pytest.raises(ValueError, match="only issues"):
        BaseballSavantClient._guard("/leaderboard/expected_statistics", BOUNDED)


def test_client_refuses_a_request_with_no_date_bounds():
    with pytest.raises(ValueError, match="unbounded"):
        BaseballSavantClient._guard(ALLOWED_PATH, {"all": "true"})


def test_client_refuses_a_half_bounded_request():
    with pytest.raises(ValueError, match="game_date_lt"):
        BaseballSavantClient._guard(ALLOWED_PATH, {"game_date_gt": "2024-07-04"})


def test_client_accepts_a_bounded_search():
    BaseballSavantClient._guard(ALLOWED_PATH, BOUNDED)  # does not raise


# --------------------------------------------------------------------------
# Description vocabulary
# --------------------------------------------------------------------------


def test_unknown_description_raises_rather_than_defaulting():
    with pytest.raises(ValueError, match="vocabulary has changed"):
        mappers.classify_description("laser_beam")


@pytest.mark.parametrize(
    "description,swing,whiff,called",
    [
        ("swinging_strike", True, True, False),
        ("swinging_strike_blocked", True, True, False),
        ("foul", True, False, False),
        ("foul_tip", True, False, False),
        ("hit_into_play", True, False, False),
        ("called_strike", False, False, True),
        ("ball", False, False, False),
        ("blocked_ball", False, False, False),
        ("hit_by_pitch", False, False, False),
        # Awarded without a pitch: not a swing, and emphatically not a take the
        # batter can be credited with.
        ("automatic_ball", False, False, False),
        ("automatic_strike", False, False, False),
    ],
)
def test_description_classification(description, swing, whiff, called):
    assert mappers.classify_description(description) == (swing, whiff, called)


def test_every_description_seen_in_a_real_export_is_classified():
    """The twelve distinct values in the recorded 2024-07-04 response."""
    observed = {
        "ball", "called_strike", "foul", "hit_into_play", "swinging_strike",
        "automatic_ball", "blocked_ball", "swinging_strike_blocked",
        "hit_by_pitch", "pitchout", "foul_tip", "foul_bunt", "automatic_strike",
    }
    for description in sorted(observed):
        mappers.classify_description(description)  # must not raise


def test_awarded_balls_and_strikes_are_not_pitches():
    assert mappers.is_real_pitch("automatic_ball") is False
    assert mappers.is_real_pitch("automatic_strike") is False
    # A pitchout is thrown on purpose, wide — it is still a pitch.
    assert mappers.is_real_pitch("pitchout") is True
    assert mappers.is_real_pitch("ball") is True
    assert mappers.is_real_pitch(None) is None


# --------------------------------------------------------------------------
# Spray angle and pull direction
# --------------------------------------------------------------------------


def test_spray_angle_is_zero_up_the_middle():
    assert mappers.spray_angle(125.42, 100.0) == 0.0


def test_spray_angle_sign_follows_the_coordinate_frame():
    # Larger hc_x is toward the first-base side, which is positive here.
    assert mappers.spray_angle(180.0, 100.0) > 0
    assert mappers.spray_angle(70.0, 100.0) < 0


def test_pull_direction_flips_with_the_batter_s_handedness():
    """A right-hander pulls to the left side, which is negative in this frame.

    Getting this backwards inverts every pull/oppo feature while still looking
    entirely plausible, which is why it is asserted in both directions.
    """
    left_side, right_side = -30.0, 30.0
    assert mappers.field_direction(left_side, "R") == "PULL"
    assert mappers.field_direction(left_side, "L") == "OPPO"
    assert mappers.field_direction(right_side, "L") == "PULL"
    assert mappers.field_direction(right_side, "R") == "OPPO"
    assert mappers.field_direction(0.0, "R") == "CENT"


def test_pull_direction_is_unknown_without_handedness():
    assert mappers.field_direction(30.0, None) is None
    assert mappers.field_direction(None, "R") is None


# --------------------------------------------------------------------------
# Knowledge time
# --------------------------------------------------------------------------


def test_knowledge_time_is_the_final_out_when_known():
    assert mappers.knowledge_time_for(GAME_END, FIRST_PITCH) == GAME_END


def test_knowledge_time_falls_back_to_a_lag_after_first_pitch():
    got = mappers.knowledge_time_for(None, FIRST_PITCH)
    assert got == FIRST_PITCH + mappers.STATCAST_KNOWLEDGE_LAG
    assert got > FIRST_PITCH


# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------


def _row(**overrides) -> dict:
    base = {
        "game_pk": GAME_ID,
        "at_bat_number": 1,
        "pitch_number": 1,
        "pitcher": PITCHER,
        "batter": BATTER,
        "inning": 1,
        "inning_topbot": "Top",
        "balls": 0,
        "strikes": 0,
        "outs_when_up": 0,
        "pitch_type": "FF",
        "pitch_name": "4-Seam Fastball",
        "release_speed": 95.4,
        "effective_speed": 95.9,
        "release_spin_rate": 2350,
        "spin_axis": 210,
        "pfx_x": -0.5,
        "pfx_z": 1.4,
        "plate_x": 0.1,
        "plate_z": 2.5,
        "release_extension": 6.5,
        "zone": 5,
        "description": "ball",
        "type": "B",
        "stand": "R",
        "p_throws": "R",
        "n_thruorder_pitcher": 1,
        "pitcher_days_since_prev_game": 5,
        "bat_speed": None,
        "swing_length": None,
        "events": None,
        "woba_value": None,
        "woba_denom": None,
        "launch_speed": None,
        "launch_angle": None,
        "hit_distance_sc": None,
        "launch_speed_angle": None,
        "bb_type": None,
        "estimated_woba_using_speedangle": None,
        "estimated_ba_using_speedangle": None,
        "estimated_slg_using_speedangle": None,
        "hc_x": None,
        "hc_y": None,
    }
    base.update(overrides)
    return base


KNOWN = {GAME_ID}
KNOWLEDGE = {GAME_ID: GAME_END}


def _pitches(records):
    return mappers.pitch_rows(pd.DataFrame(records), KNOWN, KNOWLEDGE, "test-fixture")


def _balls(records):
    return mappers.batted_ball_rows(
        pd.DataFrame(records), KNOWN, KNOWLEDGE, "test-fixture"
    )


def test_a_pitch_for_an_unknown_game_is_rejected_not_invented():
    """The schedule ingest is the sole authority on which games exist."""
    rows, counts = _pitches([_row(game_pk=999999)])
    assert rows == []
    assert counts["unknown_game"] == 1


def test_a_pitch_with_no_player_ids_is_rejected():
    rows, counts = _pitches([_row(pitcher=None)])
    assert rows == []
    assert counts["missing_ids"] == 1


def test_pitch_row_carries_knowledge_time_and_derived_flags():
    rows, _ = _pitches([_row(description="swinging_strike", type="S", zone=13)])
    row = rows[0]
    assert row["knowledge_time"] == GAME_END
    assert (row["is_pitch"], row["is_swing"], row["is_whiff"]) == (True, True, True)
    assert row["is_in_zone"] is False  # zone 11-14 is outside
    assert row["is_called_strike"] is False


def test_pitch_row_carries_the_plate_appearance_outcome():
    rows, _ = _pitches([_row(events="strikeout", woba_value=0.0, woba_denom=1)])
    assert rows[0]["pa_event"] == "strikeout"
    assert rows[0]["woba_denom"] == 1


def test_a_pitch_that_ended_no_plate_appearance_has_no_outcome():
    rows, _ = _pitches([_row()])
    assert rows[0]["pa_event"] is None


def test_zone_flag_is_unknown_when_the_zone_is():
    rows, _ = _pitches([_row(zone=None)])
    assert rows[0]["is_in_zone"] is None


def test_a_foul_ball_with_launch_data_is_not_a_batted_ball():
    """Statcast measures fouls. Counting them inflated batted balls by ~85%."""
    rows, _ = _balls(
        [_row(description="foul", type="S", launch_speed=88.0, launch_angle=45.0)]
    )
    assert rows == []


def test_a_ball_in_play_is_a_batted_ball():
    rows, _ = _balls(
        [
            _row(
                description="hit_into_play",
                type="X",
                events="home_run",
                launch_speed=104.2,
                launch_angle=27.0,
                launch_speed_angle=6,
                bb_type="fly_ball",
                hc_x=60.0,
                hc_y=80.0,
            )
        ]
    )
    assert len(rows) == 1
    assert rows[0]["outcome"] == "home_run"
    assert rows[0]["is_barrel"] is True
    assert rows[0]["is_hard_hit"] is True
    assert rows[0]["field_direction"] == "PULL"  # RHB, third-base side
    assert rows[0]["knowledge_time"] == GAME_END


def test_a_ball_in_play_with_no_tracking_is_stored_as_missing_not_dropped():
    rows, _ = _balls([_row(description="hit_into_play", type="X", events="single")])
    assert len(rows) == 1
    assert rows[0]["launch_speed"] is None
    # Unknown, not False: nothing was measured.
    assert rows[0]["is_hard_hit"] is None
    assert rows[0]["is_barrel"] is None


def test_barrel_comes_from_savants_own_classification():
    hit = {"description": "hit_into_play", "type": "X", "launch_speed": 99.0}
    barrelled, _ = _balls([_row(**hit, launch_speed_angle=6)])
    solid, _ = _balls([_row(**hit, launch_speed_angle=5)])
    assert barrelled[0]["is_barrel"] is True
    assert solid[0]["is_barrel"] is False


# --------------------------------------------------------------------------
# Database-backed: resumability and reconciliation
# --------------------------------------------------------------------------


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


# One synthetic half-inning: seven pitches, of which one is an awarded ball that
# was never thrown, ending in a strikeout, a home run and a groundout.
EXPORT = [
    _row(at_bat_number=1, pitch_number=1, description="called_strike", type="S"),
    _row(at_bat_number=1, pitch_number=2, description="swinging_strike", type="S"),
    _row(
        at_bat_number=1, pitch_number=3, description="swinging_strike", type="S",
        events="strikeout", woba_value=0.0, woba_denom=1,
    ),
    _row(at_bat_number=2, pitch_number=1, description="automatic_ball", type="B",
         pitch_type=None, release_speed=None),
    _row(
        at_bat_number=2, pitch_number=2, description="hit_into_play", type="X",
        events="home_run", launch_speed=105.0, launch_angle=28.0,
        launch_speed_angle=6, bb_type="fly_ball", hc_x=60.0, hc_y=80.0,
        woba_value=2.0, woba_denom=1,
    ),
    _row(at_bat_number=3, pitch_number=1, description="ball", type="B"),
    _row(
        at_bat_number=3, pitch_number=2, description="hit_into_play", type="X",
        events="field_out", launch_speed=78.0, launch_angle=-5.0,
        launch_speed_angle=1, bb_type="ground_ball", hc_x=140.0, hc_y=140.0,
        woba_value=0.0, woba_denom=1,
    ),
]

# What the box score says about the same half-inning, split across the two team
# lines the way the MLB Stats API reports it. Six pitches thrown — the awarded
# ball is not one — one strikeout, one home run, two balls in play.
BOX = {
    "pitches_thrown": 6, "strikeouts_pitched": 1, "home_runs_allowed": 1,
    "at_bats": 3, "strikeouts": 1, "sac_flies": 0, "sac_bunts": 0,
}


class FakeSavant:
    """Returns the fixture export for any date. Makes no network call."""

    name = "test-fixture"

    def __init__(self, frame: pd.DataFrame | None = None):
        self.frame = pd.DataFrame(EXPORT) if frame is None else frame
        self.calls: list[tuple[date, date]] = []

    def fetch_statcast_range(self, start, end, season=None):
        self.calls.append((start, end))
        now = datetime.now(UTC)
        return ProviderResult(
            status=ProviderStatus.OK,
            source_name=self.name,
            category=DataCategory.STATCAST,
            retrieved_at=now,
            knowledge_time=now,
            data=self.frame,
            raw_payload=None,
            endpoint=None,
        )


def _seed(session, *, box: dict | None = None) -> None:
    session.execute(
        text(
            "TRUNCATE pitches, batted_ball_events, team_game_stats, games, "
            "players, teams, job_runs, data_source_status RESTART IDENTITY CASCADE"
        )
    )
    stamp = {"source_name": "test-fixture", "retrieved_at": FIRST_PITCH,
             "knowledge_time": GAME_END}
    for tid, name in ((HOME_TEAM, "Home"), (AWAY_TEAM, "Away")):
        session.add(Team(id=tid, name=name, abbreviation=name[:3].upper(), **stamp))
    for pid, name in ((PITCHER, "Test Pitcher"), (BATTER, "Test Batter")):
        session.add(Player(id=pid, full_name=name, **stamp))
    # These models carry foreign keys but no relationships, so the unit of work
    # has nothing to order them by. Flush the referenced rows first.
    session.flush()
    session.add(
        Game(
            id=GAME_ID, season=2024, game_type="R", game_date_utc=FIRST_PITCH,
            official_date=FIRST_PITCH.date(), home_team_id=HOME_TEAM,
            away_team_id=AWAY_TEAM, home_score=1, away_score=0, home_win=True,
            is_final=True, game_end_utc=GAME_END, **stamp,
        )
    )
    session.flush()
    line = dict(BOX if box is None else box)
    session.add(
        TeamGameStat(
            game_id=GAME_ID, team_id=HOME_TEAM, opponent_team_id=AWAY_TEAM,
            is_home=True, game_date_utc=FIRST_PITCH, **line, **stamp,
        )
    )
    # The opposing line contributes nothing: the fixture is one half-inning.
    session.add(
        TeamGameStat(
            game_id=GAME_ID, team_id=AWAY_TEAM, opponent_team_id=HOME_TEAM,
            is_home=False, game_date_utc=FIRST_PITCH,
            pitches_thrown=0, strikeouts_pitched=0, home_runs_allowed=0,
            at_bats=0, strikeouts=0, sac_flies=0, sac_bunts=0, **stamp,
        )
    )
    session.commit()


@pytest.fixture()
def session(engine):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as s:
        _seed(s)
        yield s


def _run(session, monkeypatch, provider=None, **kwargs):
    provider = provider or FakeSavant()
    monkeypatch.setattr(ingest, "get_statcast_provider", lambda: provider)
    return ingest.ingest_statcast_range(
        session, FIRST_PITCH.date(), FIRST_PITCH.date(), **kwargs
    ), provider


def test_ingest_stores_pitches_and_balls_in_play(session, monkeypatch):
    result, _ = _run(session, monkeypatch)
    assert result.pitches == len(EXPORT)
    assert result.batted_balls == 2  # the home run and the groundout, not the fouls
    assert result.games == 1
    assert result.rejected_unknown_game == 0
    assert result.rejected_unknown_player == 0


def test_ingest_reconciles_cleanly_against_the_box_score(session, monkeypatch):
    result, _ = _run(session, monkeypatch)
    assert result.discrepancies == []


def test_the_awarded_ball_is_stored_but_is_not_counted_as_a_pitch(session, monkeypatch):
    _run(session, monkeypatch)
    stored = session.scalar(select(func.count()).select_from(Pitch))
    thrown = session.scalar(
        select(func.count()).select_from(Pitch).where(Pitch.is_pitch.is_(True))
    )
    assert stored == len(EXPORT)
    assert thrown == BOX["pitches_thrown"]


def test_reingesting_the_same_date_writes_nothing_new(session, monkeypatch):
    _run(session, monkeypatch)
    before = session.scalar(select(func.count()).select_from(Pitch))

    second, provider = _run(session, monkeypatch)
    assert second.dates == 0
    assert provider.calls == []  # not even requested
    assert session.scalar(select(func.count()).select_from(Pitch)) == before


def test_a_partially_ingested_date_is_still_pending(session, monkeypatch):
    """Resumability: one game done does not mark the whole date done."""
    _run(session, monkeypatch)
    day = FIRST_PITCH.date()
    assert ingest.pending_statcast_dates(session, day, day) == []

    stamp = {"source_name": "test-fixture", "retrieved_at": FIRST_PITCH,
             "knowledge_time": GAME_END}
    session.add(
        Game(
            id=GAME_ID + 1, season=2024, game_type="R",
            game_date_utc=FIRST_PITCH + timedelta(hours=3),
            official_date=day, home_team_id=AWAY_TEAM, away_team_id=HOME_TEAM,
            home_score=2, away_score=1, home_win=True, is_final=True, **stamp,
        )
    )
    session.commit()
    assert ingest.pending_statcast_dates(session, day, day) == [day]


def test_a_pitch_count_gap_is_reported(session, monkeypatch):
    _seed(session, box={**BOX, "pitches_thrown": 40})
    result, _ = _run(session, monkeypatch)
    checks = {d.check for d in result.discrepancies}
    assert "pitch_count" in checks


def test_a_strikeout_gap_is_reported(session, monkeypatch):
    _seed(session, box={**BOX, "strikeouts_pitched": 9, "strikeouts": 9})
    result, _ = _run(session, monkeypatch)
    assert "strikeouts" in {d.check for d in result.discrepancies}


def test_a_home_run_gap_is_reported(session, monkeypatch):
    _seed(session, box={**BOX, "home_runs_allowed": 4})
    result, _ = _run(session, monkeypatch)
    assert "home_runs" in {d.check for d in result.discrepancies}


def test_a_missing_ball_in_play_is_reported(session, monkeypatch):
    """The table every contact-quality feature reads gets its own check."""
    _seed(session, box={**BOX, "at_bats": 12})
    result, _ = _run(session, monkeypatch)
    assert "balls_in_play" in {d.check for d in result.discrepancies}


def test_a_discrepancy_still_stores_the_rows(session, monkeypatch):
    """A finding names the affected game; it does not throw the data away."""
    _seed(session, box={**BOX, "pitches_thrown": 40})
    result, _ = _run(session, monkeypatch)
    assert result.discrepancies
    assert session.scalar(select(func.count()).select_from(Pitch)) == len(EXPORT)
    assert result.discrepancies[0].game_id == GAME_ID
    assert "statcast=" in result.discrepancies[0].describe()


def test_pitches_from_an_unrecognised_game_are_dropped_not_attributed(
    session, monkeypatch
):
    frame = pd.DataFrame([_row(game_pk=424242, **{"at_bat_number": 9})] + EXPORT)
    result, _ = _run(session, monkeypatch, provider=FakeSavant(frame))
    assert result.rejected_unknown_game == 1
    assert result.pitches == len(EXPORT)
    assert session.scalars(select(Pitch.game_id).distinct()).all() == [GAME_ID]


def test_ingest_refuses_when_no_statcast_provider_is_configured(session, monkeypatch):
    """UNAVAILABLE, never estimated."""
    from app.providers.unavailable import UnavailableStatcastProvider

    monkeypatch.setattr(
        ingest, "get_statcast_provider", lambda: UnavailableStatcastProvider()
    )
    with pytest.raises(RuntimeError, match="STATCAST_PROVIDER"):
        ingest.ingest_statcast_range(session, FIRST_PITCH.date(), FIRST_PITCH.date())


def test_stored_knowledge_time_is_never_before_first_pitch(session, monkeypatch):
    """LEAKAGE_PREVENTION §13: a pitch cannot be known before it is thrown."""
    _run(session, monkeypatch)
    leaked = session.scalar(
        select(func.count())
        .select_from(Pitch)
        .join(Game, Game.id == Pitch.game_id)
        .where(Pitch.knowledge_time <= Game.game_date_utc)
    )
    assert leaked == 0
    bbe_leaked = session.scalar(
        select(func.count())
        .select_from(BattedBallEvent)
        .join(Game, Game.id == BattedBallEvent.game_id)
        .where(BattedBallEvent.knowledge_time <= Game.game_date_utc)
    )
    assert bbe_leaked == 0
