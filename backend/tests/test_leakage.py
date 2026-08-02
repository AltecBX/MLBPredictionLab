"""Leakage enforcement tests.

One test per vector in LEAKAGE_PREVENTION.md §13. These are the tests that make
the as-of guarantees real rather than aspirational.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from app.core.clock import as_of_for_game
from app.core.errors import LeakageError
from app.features.asof import OUTCOME_COLUMNS
from app.features.builder import FeatureBuilder
from app.features.context import FORBIDDEN_CONTEXT_FIELDS, GameContext
from app.features.elo import AsOfElo, EloEngine
from app.providers.mlb_statsapi.client import FORBIDDEN_PATH_FRAGMENTS, MlbStatsApiClient
from app.providers.mlb_statsapi.mappers import RESULT_KNOWLEDGE_LAG, result_knowledge_time
from tests.conftest import make_store

BACKEND_ROOT = Path(__file__).resolve().parents[1]


# --- Vector 1: season-aggregate stats endpoints -----------------------------

def test_no_season_stat_endpoints_are_reachable():
    """The client refuses season-aggregate endpoints outright."""
    client = MlbStatsApiClient()
    try:
        for fragment in FORBIDDEN_PATH_FRAGMENTS:
            with pytest.raises(ValueError, match="season-aggregate"):
                client.get(fragment)
    finally:
        client.close()


def test_provider_layer_never_calls_a_season_stat_endpoint():
    """Static scan of call sites: no provider builds a season-stat request path.

    ``client.py`` is excluded because it is where the denylist itself lives; the
    test above proves that denylist is enforced.
    """
    offenders = []
    for path in (BACKEND_ROOT / "app" / "providers").rglob("*.py"):
        if path.name == "client.py":
            continue
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if "stats=season" in line or '_client.get("/stats' in line:
                offenders.append(f"{path.relative_to(BACKEND_ROOT)}:{number}")
    assert not offenders, f"Season-aggregate endpoint referenced in: {offenders}"


def test_endpoints_actually_used_are_all_per_game_or_reference():
    """Every endpoint the provider requests is a schedule, boxscore or reference call."""
    text = (BACKEND_ROOT / "app" / "providers" / "mlb_statsapi" / "provider.py").read_text()
    endpoints = {
        line.split('"')[1]
        for line in text.splitlines()
        if "endpoint" in line and '"/' in line
    }
    allowed_prefixes = ("/teams", "/venues", "/people", "/schedule", "/game/")
    for endpoint in endpoints:
        assert endpoint.startswith(allowed_prefixes), f"Unexpected endpoint {endpoint}"


def test_rolling_stats_exclude_the_target_game(fixture_frames):
    """A feature for game G is identical whether or not G's own rows exist."""
    ctx_row = fixture_frames["games"].iloc[35].to_dict()
    with_target = make_store(fixture_frames)

    trimmed = {
        "games": fixture_frames["games"],
        "team_games": fixture_frames["team_games"][
            fixture_frames["team_games"]["game_id"] != ctx_row["id"]
        ],
        "pitcher_games": fixture_frames["pitcher_games"][
            fixture_frames["pitcher_games"]["game_id"] != ctx_row["id"]
        ],
        "players": fixture_frames["players"],
        "ballparks": fixture_frames["ballparks"],
    }
    without_target = make_store(trimmed)

    ctx = GameContext.from_row(ctx_row)
    as_of = as_of_for_game(ctx.first_pitch_utc)

    a = FeatureBuilder(with_target, AsOfElo(with_target.games)).build(ctx, as_of)
    b = FeatureBuilder(without_target, AsOfElo(without_target.games)).build(ctx, as_of)
    assert a.features == b.features


# --- Vector 2: the target game's own result ---------------------------------

def test_game_context_omits_every_outcome_field():
    fields = set(GameContext.__dataclass_fields__)
    assert not (fields & FORBIDDEN_CONTEXT_FIELDS)
    assert not (fields & OUTCOME_COLUMNS)


def test_feature_keys_contain_no_outcome_fields(builder, target_game):
    vector = builder.build(target_game, as_of_for_game(target_game.first_pitch_utc))
    for key in vector.features:
        assert "home_win" not in key
        assert "score" not in key
        assert "actual" not in key


def test_label_never_in_feature_vector(builder, target_game):
    vector = builder.build(target_game, as_of_for_game(target_game.first_pitch_utc))
    assert "home_win" not in vector.features


def test_as_of_must_precede_first_pitch(builder, target_game):
    with pytest.raises(ValueError, match="not before first pitch"):
        builder.build(target_game, target_game.first_pitch_utc)


# --- Vector 3/4: snapshot tables respect knowledge_time ---------------------

def test_slice_never_contains_a_future_fact(store, target_game):
    as_of = as_of_for_game(target_game.first_pitch_utc)
    frame = store.team_games_asof(target_game.home_team_id, as_of)
    store.assert_as_of(frame, as_of, "home team window")
    assert (frame["game_date_utc"] < as_of).all()
    assert (frame["knowledge_time"] <= as_of).all()


def test_assert_as_of_raises_on_future_rows(store, target_game):
    as_of = as_of_for_game(target_game.first_pitch_utc)
    everything = store.team_games
    with pytest.raises(LeakageError):
        store.assert_as_of(everything, as_of, "unfiltered frame")


def test_a_game_finishing_after_as_of_is_invisible(store):
    """A game played the same day but finishing after as_of stays excluded."""
    row = store.games.iloc[20].to_dict()
    ctx = GameContext.from_row(row)
    just_before = ctx.first_pitch_utc - timedelta(minutes=1)
    frame = store.team_games_asof(ctx.home_team_id, just_before)
    assert row["id"] not in set(frame["game_id"])


# --- Vector 6: scaler/imputer fit only on train ------------------------------

def test_scaler_fit_only_on_train():

    from app.modeling.logistic import LogisticWinModel

    names = ["a", "b"]
    frame = pd.DataFrame(
        {"a": list(range(100)), "b": list(range(100, 200)),
         "home_win": [i % 2 for i in range(100)]}
    )
    train = frame.iloc[:60]
    model = LogisticWinModel(feature_names=names, C=0.1).fit(train)
    fitted_mean = model.pipeline.named_steps["scale"].mean_[0]

    assert fitted_mean == pytest.approx(train["a"].mean())
    assert fitted_mean != pytest.approx(frame["a"].mean())


# --- Vector 7: no random cross-validation -----------------------------------

def test_no_random_cv_in_modeling_or_backtest():
    banned = ("KFold", "StratifiedKFold", "ShuffleSplit", "GridSearchCV",
              "RandomizedSearchCV", "cross_val_score", "train_test_split")
    offenders = []
    for package in ("modeling", "backtest"):
        for path in (BACKEND_ROOT / "app" / package).rglob("*.py"):
            text = path.read_text()
            for token in banned:
                if token in text:
                    offenders.append(f"{path.relative_to(BACKEND_ROOT)}:{token}")
    assert not offenders, f"Random cross-validation constructs found: {offenders}"


def test_walk_forward_steps_are_chronological():
    from app.backtest.walkforward import make_steps

    frame = pd.DataFrame(
        {"official_date": pd.date_range("2024-04-01", periods=200, freq="D").date}
    )
    steps = make_steps(frame, step_days=30, validation_days=15)
    assert steps
    for step in steps:
        assert step.train_end < step.test_start
        assert step.test_start <= step.test_end
        assert step.validation_start <= step.train_end
    for earlier, later in zip(steps, steps[1:], strict=False):
        assert earlier.test_end < later.test_start


# --- Vector 8: Elo uses the pre-game rating ---------------------------------

def test_elo_pregame_rating_excludes_current_game():
    engine = EloEngine()
    before_first = engine.rating(1)
    engine.observe(game_id=1, season=2024, home_team_id=1, away_team_id=2,
                   home_score=5, away_score=1)
    after_first = engine.rating(1)
    engine.observe(game_id=2, season=2024, home_team_id=1, away_team_id=2,
                   home_score=2, away_score=1)

    assert engine.rating_before(1, 1) == before_first
    assert engine.rating_before(2, 1) == after_first
    assert engine.rating_before(2, 1) != engine.rating(1)


def test_as_of_elo_ignores_games_not_yet_knowable(store):
    elo = AsOfElo(store.games)
    first = store.games.iloc[0]
    before_any = first["game_date_utc"] - timedelta(days=1)
    assert elo.games_rated(int(first["home_team_id"]), pd.Timestamp(before_any)) == 0
    assert elo.rating_at(int(first["home_team_id"]), pd.Timestamp(before_any)) == 1500.0


# --- Vector 9: season windows are bounded by as_of --------------------------

def test_season_window_is_bounded_by_as_of(store, target_game):
    from app.features.asof import season_start_utc

    as_of = as_of_for_game(target_game.first_pitch_utc)
    frame = store.team_games_asof(
        target_game.home_team_id, as_of, season_start_utc(target_game.season)
    )
    later = store.team_games[
        (store.team_games["team_id"] == target_game.home_team_id)
        & (store.team_games["game_date_utc"] >= as_of)
    ]
    assert len(later) > 0, "fixture must contain later games for this to be meaningful"
    assert not set(frame["game_id"]) & set(later["game_id"])


# --- Vector 10: backfill clock ----------------------------------------------

def test_backfilled_result_knowledge_time_is_derived_from_the_game():
    from app.providers.base import RawGame

    first_pitch = datetime(2023, 5, 1, 23, 5, tzinfo=UTC)
    game = RawGame(
        id=1, season=2023, game_type="R", game_date_utc=first_pitch,
        official_date=first_pitch.date(), home_team_id=1, away_team_id=2,
        venue_id=3, is_final=True, home_score=4, away_score=2,
    )
    knowledge = result_knowledge_time(game)
    assert knowledge == first_pitch + RESULT_KNOWLEDGE_LAG
    assert knowledge > first_pitch
    assert knowledge.year == 2023  # not the ingestion year


# --- Vector 11: doubleheaders use timestamps, not dates ---------------------

def test_doubleheader_uses_timestamp_not_date(fixture_frames):
    """Game 1 of a doubleheader is visible to game 2 only once it has finished."""
    games = fixture_frames["games"].copy()
    team_games = fixture_frames["team_games"].copy()

    base = games.iloc[10].to_dict()
    game_one_start = base["game_date_utc"].replace(hour=17)
    game_two_start = base["game_date_utc"].replace(hour=23)

    games.loc[games["id"] == base["id"], "game_date_utc"] = game_one_start
    games.loc[games["id"] == base["id"], "knowledge_time"] = (
        game_one_start + RESULT_KNOWLEDGE_LAG
    )
    team_games.loc[team_games["game_id"] == base["id"], "game_date_utc"] = game_one_start
    team_games.loc[team_games["game_id"] == base["id"], "knowledge_time"] = (
        game_one_start + RESULT_KNOWLEDGE_LAG
    )

    frames = {**fixture_frames, "games": games, "team_games": team_games}
    store = make_store(frames)

    # Game two's as-of is after game one finished -> game one is visible.
    visible = store.team_games_asof(int(base["home_team_id"]), game_two_start)
    assert base["id"] in set(visible["game_id"])

    # An as-of before game one finished -> invisible, even on the same date.
    hidden = store.team_games_asof(
        int(base["home_team_id"]), game_one_start + timedelta(minutes=30)
    )
    assert base["id"] not in set(hidden["game_id"])


# --- Vector 5/12: odds isolation --------------------------------------------

def test_clv_and_odds_are_not_reachable_from_the_feature_layer():
    """No feature module imports odds. Market features are opt-in per version."""
    offenders = []
    for path in (BACKEND_ROOT / "app" / "features").rglob("*.py"):
        text = path.read_text()
        if "OddsSnapshot" in text or "odds_snapshots" in text:
            offenders.append(str(path.relative_to(BACKEND_ROOT)))
    assert not offenders, f"Feature layer references odds: {offenders}"


def test_active_feature_set_contains_no_market_features():
    from app.features.registry import FS_V1

    assert not [s for s in FS_V1 if s.source_category == "odds"]


# --- Cross-cutting: forbidden language --------------------------------------

def test_no_lock_or_guaranteed_language_in_user_facing_strings():
    """'Lock' and 'guaranteed' must never appear in a user-facing label."""
    banned = ("guaranteed win", "sure thing", "lock of the day", "can't lose")
    offenders = []
    for path in (BACKEND_ROOT / "app").rglob("*.py"):
        lowered = path.read_text().lower()
        for phrase in banned:
            if phrase in lowered:
                offenders.append(f"{path.relative_to(BACKEND_ROOT)}:{phrase}")
    assert not offenders, f"Overconfident language found: {offenders}"


def test_recommendation_labels_never_promise_certainty():
    from app.services.confidence import recommendation_label

    labels = {
        recommendation_label(p / 100, c / 100, 1.0, 1.0)
        for p in range(1, 100, 3)
        for c in (30, 60, 90)
    }
    assert labels <= {
        "STRONG_LEAN", "MODERATE_LEAN", "SMALL_LEAN",
        "NO_MEANINGFUL_ADVANTAGE", "INSUFFICIENT_DATA",
    }


# --------------------------------------------------------------------------
# Columns that encode the future
# --------------------------------------------------------------------------

#: Statcast's export carries two fields that describe when a player's *next*
#: appearance will be. They are fully populated and sit beside the harmless
#: `pitcher_days_since_prev_game`, one character apart in meaning and nothing
#: apart in appearance, so a wildcard column selection would pull them in
#: without anyone noticing. Nothing may read them.
FUTURE_LEAKING_STATCAST_FIELDS = (
    "pitcher_days_until_next_game",
    "batter_days_until_next_game",
)


def test_no_future_dated_statcast_field_is_ingested():
    """A column naming the player's NEXT game is the future, by definition.

    Found while surveying Baseball Savant's export rather than by a failure:
    the mapper happens not to read these today. This asserts that it stays
    that way, because the safe field and the leaking ones differ only by the
    words "since prev" versus "until next".
    """
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "app"
    offenders = []
    for path in source.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for field in FUTURE_LEAKING_STATCAST_FIELDS:
            if field in text:
                offenders.append(f"{path.relative_to(source)} references {field}")
    assert not offenders, (
        "These fields describe a game that has not happened yet:\n  "
        + "\n  ".join(offenders)
    )


def test_the_pitch_model_has_no_forward_looking_column():
    """The stored schema must not be able to hold the future either."""
    from app.db.models import Pitch

    columns = {c.name for c in Pitch.__table__.columns}
    assert not [c for c in columns if "until_next" in c or "days_until" in c]
    # The backward-looking counterpart is the one that is allowed to exist.
    assert "pitcher_days_since_prev" in columns
