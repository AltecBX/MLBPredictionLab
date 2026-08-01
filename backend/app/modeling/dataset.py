"""Training and backtest dataset assembly.

Every row's features are produced by the exact same code path that produces a
live prediction — there is no separate "training feature" implementation
(MODELING_PLAN.md §9).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd
from sqlalchemy.orm import Session

from app.core.clock import AsOfPolicy, as_of_for_game
from app.core.config import settings
from app.core.logging import get_logger
from app.features.asof import AsOfStore
from app.features.builder import FEATURE_SET_VERSION, FeatureBuilder, FeatureVector
from app.features.context import GameContext
from app.features.elo import AsOfElo
from app.features.registry import feature_keys

log = get_logger(__name__)

META_COLUMNS = [
    "game_id", "as_of", "official_date", "season", "month",
    "home_team_id", "away_team_id", "completeness", "n_missing",
    "home_starter_known", "away_starter_known", "lineup_confirmed",
    "starter_quality_index",
]

LABEL_COLUMN = "home_win"

# Regular season only by default; postseason has different roster dynamics and
# a tiny sample, so mixing it in adds noise rather than signal.
DEFAULT_GAME_TYPES = ("R",)


@dataclass(slots=True)
class Dataset:
    frame: pd.DataFrame
    feature_names: list[str]
    feature_set_version: str
    as_of_policy: str

    def __len__(self) -> int:
        return len(self.frame)

    @property
    def labelled(self) -> pd.DataFrame:
        return self.frame[self.frame[LABEL_COLUMN].notna()]

    def before(self, cutoff: date) -> pd.DataFrame:
        return self.labelled[self.labelled["official_date"] < cutoff]

    def between(self, start: date, end: date) -> pd.DataFrame:
        rows = self.labelled
        return rows[(rows["official_date"] >= start) & (rows["official_date"] <= end)]


def _starter_quality_index(vector: FeatureVector) -> float | None:
    """Lower FIP is better; index is the better (lower) of the two starters."""
    values = [
        vector.home.get("sp_fip_season").value,
        vector.away.get("sp_fip_season").value,
    ]
    present = [v for v in values if v is not None]
    return min(present) if present else None


def build_dataset(
    session: Session,
    seasons: list[int] | None = None,
    as_of_policy: AsOfPolicy | None = None,
    include_unplayed: bool = False,
    game_types: tuple[str, ...] = DEFAULT_GAME_TYPES,
    store: AsOfStore | None = None,
    feature_set_version: str | None = None,
) -> Dataset:
    """Build the model matrix for every eligible game."""
    policy: AsOfPolicy = as_of_policy or settings.prediction_as_of_policy  # type: ignore[assignment]
    store = store or AsOfStore.load(session, seasons)
    elo = AsOfElo(store.games)
    version = feature_set_version or FEATURE_SET_VERSION
    builder = FeatureBuilder(store, elo, feature_set_version=version)

    games = store.games
    if games.empty:
        return Dataset(pd.DataFrame(), feature_keys(version), version, policy)

    eligible = games[games["game_type"].isin(game_types)]
    if not include_unplayed:
        eligible = eligible[eligible["home_win"].notna()]
    eligible = eligible.sort_values("game_date_utc")

    names = feature_keys(version)
    rows: list[dict[str, object]] = []
    skipped = 0

    for row in eligible.to_dict("records"):
        ctx = GameContext.from_row(row)
        as_of = as_of_for_game(ctx.first_pitch_utc, policy)
        try:
            vector = builder.build(ctx, as_of)
        except Exception as exc:  # a single unbuildable game must not abort
            skipped += 1
            log.warning("dataset.feature_build_failed", game_id=ctx.game_id, error=str(exc))
            continue
        if not vector.is_usable:
            skipped += 1
            continue

        record: dict[str, object] = {
            "game_id": ctx.game_id,
            "as_of": as_of,
            "official_date": ctx.official_date,
            "season": ctx.season,
            "month": ctx.official_date.month,
            "home_team_id": ctx.home_team_id,
            "away_team_id": ctx.away_team_id,
            "completeness": vector.completeness,
            "n_missing": len(vector.missing_features),
            "home_starter_known": bool(vector.features.get("sp_identified_home")),
            "away_starter_known": bool(vector.features.get("sp_identified_away")),
            # Pregame lineup confirmation requires the Phase 2 poller; until it
            # exists this is honestly False for every historical row rather than
            # being inferred from a post-game boxscore.
            "lineup_confirmed": False,
            "starter_quality_index": _starter_quality_index(vector),
            LABEL_COLUMN: row.get("home_win"),
        }
        record.update({name: vector.features.get(name) for name in names})
        rows.append(record)

    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame["official_date"] = pd.to_datetime(frame["official_date"]).dt.date
        frame = frame.sort_values(["official_date", "game_id"]).reset_index(drop=True)

    log.info(
        "dataset.built",
        rows=len(frame),
        skipped=skipped,
        features=len(names),
        policy=policy,
    )
    return Dataset(frame, names, version, policy)


def build_vectors_for_games(
    session: Session,
    game_rows: list[dict],
    as_of: datetime | None = None,
    as_of_policy: AsOfPolicy | None = None,
    store: AsOfStore | None = None,
) -> list[tuple[GameContext, FeatureVector]]:
    """Build feature vectors for specific games (live prediction path)."""
    policy: AsOfPolicy = as_of_policy or settings.prediction_as_of_policy  # type: ignore[assignment]
    store = store or AsOfStore.load(session)
    builder = FeatureBuilder(store)

    out: list[tuple[GameContext, FeatureVector]] = []
    for row in game_rows:
        ctx = GameContext.from_row(row)
        moment = as_of or min(
            as_of_for_game(ctx.first_pitch_utc, policy),
            _now_capped(ctx.first_pitch_utc),
        )
        try:
            out.append((ctx, builder.build(ctx, moment)))
        except Exception as exc:
            log.warning("prediction.feature_build_failed", game_id=ctx.game_id, error=str(exc))
    return out


def _now_capped(first_pitch: datetime) -> datetime:
    """The live as-of is 'now', but never at or after first pitch."""
    from app.core.clock import utcnow

    now = utcnow()
    return min(now, first_pitch - pd.Timedelta(minutes=1).to_pytimedelta())
