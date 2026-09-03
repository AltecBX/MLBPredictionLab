"""As-of data access.

The single place where the as-of rule is implemented:

    A feature computed for a prediction at time T may read a fact only if that
    fact was knowable strictly before T.

Every query here applies BOTH conditions:
  * ``knowledge_time <= as_of``  — the fact was knowable, and
  * ``game_date_utc  <  as_of``  — the game itself is in the past.

Filtering is on ``knowledge_time``, never on ``retrieved_at``
(LEAKAGE_PREVENTION.md §1).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import LeakageError
from app.core.logging import get_logger
from app.db.models import (
    Ballpark,
    Game,
    Injury,
    Player,
    PlayerGameStat,
    TeamGameStat,
    Weather,
)
from app.features.batter_agg import (
    load_batter_statcast,
    load_batting_orders,
    load_pitcher_arsenal,
)
from app.features.statcast_agg import load_pitcher_statcast

log = get_logger(__name__)

# Column names whose presence in a feature-facing frame would leak the label.
OUTCOME_COLUMNS = frozenset(
    {"home_win", "home_score", "away_score", "is_final", "innings_played", "game_end_utc"}
)


@dataclass(frozen=True, slots=True)
class Window:
    """A trailing window ending strictly before ``as_of``."""

    key: str
    days: int | None = None          # None -> season-to-date
    season_to_date: bool = False

    def start(self, as_of: datetime, season_start: datetime) -> datetime:
        if self.season_to_date or self.days is None:
            return season_start
        return as_of - timedelta(days=self.days)


W7 = Window("w7", days=7)
W14 = Window("w14", days=14)
W30 = Window("w30", days=30)
W60 = Window("w60", days=60)
SEASON = Window("season", season_to_date=True)

WINDOWS = {w.key: w for w in (W7, W14, W30, W60, SEASON)}


def _ns(moment: datetime) -> int:
    """UTC nanoseconds since epoch. Comparing int64 avoids numpy's tz-naive trap."""
    return pd.Timestamp(moment).tz_convert("UTC").value


def _ns_array(series: pd.Series) -> np.ndarray:
    return pd.DatetimeIndex(series).tz_convert("UTC").asi8


def season_start_utc(season: int) -> datetime:
    """Conservative season boundary. Spring training games are excluded by game_type."""
    return datetime(season, 1, 1, tzinfo=UTC)


#: Seasons loaded BEFORE the earliest season a caller asks for. The
#: multi-season projections (features/projections.py) read up to three prior
#: seasons of a starter's starts and two of a team's games; a store cut to
#: exactly the seasons being scored would hand the earliest of them no history
#: at all, and a measurement on that store would be measuring the cut rather
#: than the feature. Production loads everything, so this is what makes a
#: `--seasons` measurement look like production. A test pins it to the
#: projection weights.
LOOKBACK_SEASONS = 3


def seasons_to_load(seasons: list[int] | None, lookback: int = LOOKBACK_SEASONS) -> list[int] | None:
    """The requested seasons plus the lookback before the earliest of them."""
    if not seasons:
        return None
    earliest = min(seasons)
    return sorted(set(seasons) | set(range(earliest - lookback, earliest)))


class AsOfStore:
    """In-memory, as-of-filtered view of the fact tables.

    Loading once and slicing in memory keeps a multi-season walk-forward
    backtest tractable while keeping the as-of cut in exactly one place.
    """

    def __init__(
        self,
        games: pd.DataFrame,
        team_games: pd.DataFrame,
        pitcher_games: pd.DataFrame,
        players: pd.DataFrame,
        ballparks: pd.DataFrame,
        pitcher_statcast: pd.DataFrame | None = None,
        batter_statcast: pd.DataFrame | None = None,
        pitcher_arsenal: pd.DataFrame | None = None,
        batting_orders: pd.DataFrame | None = None,
        weather: pd.DataFrame | None = None,
        batter_games: pd.DataFrame | None = None,
        injuries: pd.DataFrame | None = None,
    ) -> None:
        self.games = games
        self.pitcher_games = pitcher_games
        self.players = players
        self.ballparks = ballparks
        # Optional: empty until Statcast has been ingested. Absent is absent —
        # every feature built from it reports UNAVAILABLE rather than zero.
        self.pitcher_statcast = (
            pd.DataFrame() if pitcher_statcast is None else pitcher_statcast
        )
        self.batter_statcast = (
            pd.DataFrame() if batter_statcast is None else batter_statcast
        )
        self.pitcher_arsenal = (
            pd.DataFrame() if pitcher_arsenal is None else pitcher_arsenal
        )
        self.batting_orders = (
            pd.DataFrame() if batting_orders is None else batting_orders
        )
        # Forecast weather. Append-only snapshots keyed by knowledge_time, so a
        # prediction reads whichever forecast existed when it was made.
        self.weather = pd.DataFrame() if weather is None else weather
        # Per-batter game lines, for the share of a team's production that a
        # missing player took with him. Only the columns the availability group
        # reads are loaded: this is the widest table in the database and the
        # rest of it has no caller.
        self.batter_games = pd.DataFrame() if batter_games is None else batter_games
        # Roster transactions, sorted by when they became knowable. Sorted once
        # here because every lookup is a window found by binary search.
        self.injuries = pd.DataFrame() if injuries is None else injuries
        if not self.injuries.empty:
            self.injuries = self.injuries.sort_values(
                ["knowledge_time", "id"]
            ).reset_index(drop=True)
            self._injury_ns = _ns_array(self.injuries["knowledge_time"])
        else:
            self._injury_ns = np.array([], dtype="int64")
        self._unavailable_cache: dict[int, frozenset[int]] = {}
        self.team_games = self._attach_opponent_starter_hand(
            team_games, pitcher_games, players
        )

        self._assert_no_outcome_columns(self.team_games, "team_games")
        self._assert_no_outcome_columns(pitcher_games, "pitcher_games")
        self._assert_no_outcome_columns(self.batter_games, "batter_games")

        self._team_index = self._build_index(self.team_games, "team_id")
        self._pitcher_index = self._build_index(pitcher_games, "player_id")
        self._pitcher_statcast_index = self._build_index(
            self.pitcher_statcast, "player_id"
        )
        self._batter_statcast_index = self._build_index(self.batter_statcast, "player_id")
        self._arsenal_index = self._build_index(self.pitcher_arsenal, "player_id")
        self._orders_index = self._build_index(self.batting_orders, "team_id")
        self._team_pitchers_index = self._build_index(pitcher_games, "team_id")
        self._team_batters_index = self._build_index(self.batter_games, "team_id")
        self._schedule_index = self._build_schedule_index(games)
        self._weather_index = (
            {}
            if self.weather.empty
            else {
                int(gid): frame.sort_values("knowledge_time")
                for gid, frame in self.weather.groupby("game_id")
            }
        )
        self._player_hand = dict(zip(players["id"], players["pitch_hand"], strict=False))
        self._park = ballparks.set_index("id") if not ballparks.empty else ballparks

    # -- construction ------------------------------------------------------
    @classmethod
    def load(
        cls,
        session: Session,
        seasons: list[int] | None = None,
        lookback_seasons: int = LOOKBACK_SEASONS,
    ) -> AsOfStore:
        """Load the fact tables for ``seasons`` and the lookback before them.

        ``seasons`` is which games a caller means to score; the store also
        carries ``lookback_seasons`` before the earliest of them so that every
        feature which reads prior seasons — projections, Elo's carried rating,
        a starter's experience — sees what production sees. Restricting the
        scored games back to ``seasons`` is the dataset builder's job.
        """
        seasons = seasons_to_load(seasons, lookback_seasons)
        game_cols = [
            Game.id, Game.season, Game.game_type, Game.game_date_utc, Game.official_date,
            Game.home_team_id, Game.away_team_id, Game.venue_id, Game.day_night,
            Game.doubleheader, Game.game_number, Game.home_score, Game.away_score,
            Game.home_win, Game.is_final, Game.innings_played, Game.knowledge_time,
            Game.home_probable_pitcher_id, Game.away_probable_pitcher_id,
        ]
        stmt = select(*game_cols)
        if seasons:
            stmt = stmt.where(Game.season.in_(seasons))
        games = pd.DataFrame(session.execute(stmt).mappings().all())

        tg_stmt = select(TeamGameStat)
        if seasons:
            tg_stmt = tg_stmt.join(Game, Game.id == TeamGameStat.game_id).where(
                Game.season.in_(seasons)
            )
        team_games = pd.DataFrame(
            [
                {c.name: getattr(row, c.name) for c in TeamGameStat.__table__.columns}
                for row in session.scalars(tg_stmt).all()
            ]
        )

        pg_stmt = select(PlayerGameStat).where(PlayerGameStat.role == "pitcher")
        if seasons:
            pg_stmt = pg_stmt.join(Game, Game.id == PlayerGameStat.game_id).where(
                Game.season.in_(seasons)
            )
        pitcher_games = pd.DataFrame(
            [
                {c.name: getattr(row, c.name) for c in PlayerGameStat.__table__.columns}
                for row in session.scalars(pg_stmt).all()
            ]
        )

        # Batter lines, narrowed to the columns the availability group reads.
        # `player_game_stats` is 333k rows across both roles and forty-odd
        # columns; loading the whole thing to divide two sums would cost more
        # memory than every other frame here put together.
        bat_stmt = select(
            PlayerGameStat.game_id, PlayerGameStat.player_id, PlayerGameStat.team_id,
            PlayerGameStat.game_date_utc, PlayerGameStat.knowledge_time,
            PlayerGameStat.pa, PlayerGameStat.hits, PlayerGameStat.doubles,
            PlayerGameStat.triples, PlayerGameStat.home_runs, PlayerGameStat.bb,
            PlayerGameStat.ibb, PlayerGameStat.hbp,
        ).where(PlayerGameStat.role == "batter")
        if seasons:
            bat_stmt = bat_stmt.join(Game, Game.id == PlayerGameStat.game_id).where(
                Game.season.in_(seasons)
            )
        batter_games = pd.DataFrame(session.execute(bat_stmt).mappings().all())
        if not batter_games.empty:
            batter_games = cls._to_utc(batter_games, "game_date_utc", "knowledge_time")
            batter_games = batter_games.sort_values("knowledge_time").reset_index(drop=True)

        # Roster transactions. Deliberately NOT season-filtered: a placement
        # made in September is what a March prediction needs to know it is still
        # in force, and `seasons` restricts which games are scored, not which
        # facts were knowable when they were played.
        injuries = pd.DataFrame(
            session.execute(
                select(
                    Injury.id, Injury.player_id, Injury.team_id, Injury.status,
                    Injury.knowledge_time,
                )
            ).mappings().all()
        )
        if not injuries.empty:
            injuries = cls._to_utc(injuries, "knowledge_time")

        players = pd.DataFrame(
            session.execute(
                select(Player.id, Player.full_name, Player.pitch_hand, Player.bat_side)
            ).mappings().all()
        )
        ballparks = pd.DataFrame(
            session.execute(
                select(
                    Ballpark.id, Ballpark.name, Ballpark.latitude, Ballpark.longitude,
                    Ballpark.elevation_ft, Ballpark.roof_type, Ballpark.timezone,
                )
            ).mappings().all()
        )

        def season_filtered(frame: pd.DataFrame) -> pd.DataFrame:
            if not seasons or frame.empty or "season" not in frame.columns:
                return frame
            return frame[frame["season"].isin(seasons)].reset_index(drop=True)

        pitcher_statcast = season_filtered(load_pitcher_statcast(session))
        batter_statcast = season_filtered(load_batter_statcast(session))
        pitcher_arsenal = season_filtered(load_pitcher_arsenal(session))
        batting_orders = load_batting_orders(session)
        if seasons and not batting_orders.empty and not games.empty:
            keep = set(games["id"].tolist())
            batting_orders = batting_orders[
                batting_orders["game_id"].isin(keep)
            ].reset_index(drop=True)

        weather = pd.read_sql(
            select(
                Weather.game_id, Weather.temperature_f, Weather.wind_speed_mph,
                Weather.wind_field_relative, Weather.air_density_kg_m3,
                Weather.precipitation_prob, Weather.roof_status,
                Weather.knowledge_time,
            ).where(Weather.observation_type == "FORECAST"),
            session.bind,
        )
        if seasons and not weather.empty and not games.empty:
            weather = weather[
                weather["game_id"].isin(set(games["id"].tolist()))
            ].reset_index(drop=True)
        if not weather.empty:
            weather = cls._to_utc(weather, "knowledge_time")

        return cls(
            cls._prepare_games(games),
            cls._prepare_team_games(team_games, games),
            cls._prepare_pitcher_games(pitcher_games),
            players,
            ballparks,
            pitcher_statcast,
            batter_statcast,
            pitcher_arsenal,
            batting_orders,
            weather,
            batter_games,
            injuries,
        )

    # -- preparation -------------------------------------------------------
    @staticmethod
    def _to_utc(frame: pd.DataFrame, *columns: str) -> pd.DataFrame:
        for column in columns:
            if column in frame.columns:
                frame[column] = pd.to_datetime(frame[column], utc=True)
        return frame

    @classmethod
    def _prepare_games(cls, games: pd.DataFrame) -> pd.DataFrame:
        if games.empty:
            return games
        games = cls._to_utc(games.copy(), "game_date_utc", "knowledge_time")
        return games.sort_values("game_date_utc").reset_index(drop=True)

    @classmethod
    def _prepare_team_games(cls, tg: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
        if tg.empty:
            return tg
        tg = cls._to_utc(tg.copy(), "game_date_utc", "knowledge_time")
        tg = tg.drop(columns=[c for c in ("id",) if c in tg.columns])
        if not games.empty:
            meta = games[["id", "season", "game_type", "venue_id", "innings_played"]].rename(
                columns={"id": "game_id", "innings_played": "game_innings"}
            )
            tg = tg.merge(meta, on="game_id", how="left")
        # Derived per-game outcome for the TEAM'S OWN past games. This is a
        # past fact about a completed game, guarded by knowledge_time; it is
        # never the target game (LEAKAGE_PREVENTION.md §4).
        tg["won"] = (tg["runs"].fillna(0) > tg["runs_allowed"].fillna(0)).astype(float)
        return tg.sort_values("knowledge_time").reset_index(drop=True)

    @classmethod
    def _prepare_pitcher_games(cls, pg: pd.DataFrame) -> pd.DataFrame:
        if pg.empty:
            return pg
        pg = cls._to_utc(pg.copy(), "game_date_utc", "knowledge_time")
        return pg.sort_values("knowledge_time").reset_index(drop=True)

    @staticmethod
    def _attach_opponent_starter_hand(
        team_games: pd.DataFrame, pitcher_games: pd.DataFrame, players: pd.DataFrame
    ) -> pd.DataFrame:
        """Label each past team-game with the handedness of the starter it faced.

        Derived from the opposing team's actual starting pitcher in that game —
        a completed-game fact, guarded by the same knowledge_time cut.
        """
        if team_games.empty:
            return team_games
        team_games = team_games.copy()
        team_games["opp_starter_hand"] = None
        if pitcher_games.empty or players.empty:
            return team_games

        starters = pitcher_games[pitcher_games["is_starter"].astype(bool)]
        if starters.empty:
            return team_games
        hand_by_player = dict(zip(players["id"], players["pitch_hand"], strict=False))
        starter_hand = {
            (int(row.game_id), int(row.team_id)): hand_by_player.get(int(row.player_id))
            for row in starters.itertuples()
        }
        team_games["opp_starter_hand"] = [
            starter_hand.get((int(g), int(o)))
            for g, o in zip(team_games["game_id"], team_games["opponent_team_id"], strict=False)
        ]
        return team_games

    @staticmethod
    def _build_index(frame: pd.DataFrame, key: str) -> dict[int, tuple[pd.DataFrame, np.ndarray]]:
        index: dict[int, tuple[pd.DataFrame, np.ndarray]] = {}
        if frame.empty or key not in frame.columns:
            return index
        for value, group in frame.groupby(key, sort=False):
            group = group.sort_values("knowledge_time")
            index[int(value)] = (group, _ns_array(group["knowledge_time"]))
        return index

    @staticmethod
    def _build_schedule_index(games: pd.DataFrame) -> dict[int, pd.DataFrame]:
        """Per-team chronological schedule (both home and away rows)."""
        index: dict[int, pd.DataFrame] = {}
        if games.empty:
            return index
        home = games.assign(team_id=games["home_team_id"], is_home=True,
                            opponent_team_id=games["away_team_id"])
        away = games.assign(team_id=games["away_team_id"], is_home=False,
                            opponent_team_id=games["home_team_id"])
        stacked = pd.concat([home, away], ignore_index=True)
        for team_id, group in stacked.groupby("team_id", sort=False):
            index[int(team_id)] = group.sort_values("game_date_utc").reset_index(drop=True)
        return index

    @staticmethod
    def _assert_no_outcome_columns(frame: pd.DataFrame, name: str) -> None:
        if frame.empty:
            return
        leaked = OUTCOME_COLUMNS & set(frame.columns)
        # team_games legitimately carries the team's own past runs; the guard is
        # about the TARGET game's label columns arriving on a feature frame.
        forbidden = leaked - {"innings_played"}
        if forbidden:
            raise LeakageError(
                f"Frame {name!r} carries outcome columns {sorted(forbidden)}."
            )

    # -- as-of slicing -----------------------------------------------------
    @staticmethod
    def _slice(
        index: dict[int, tuple[pd.DataFrame, np.ndarray]],
        key: int,
        as_of: datetime,
        start: datetime | None,
    ) -> pd.DataFrame:
        entry = index.get(int(key))
        if entry is None:
            return pd.DataFrame()
        frame, knowledge = entry
        cutoff = int(np.searchsorted(knowledge, _ns(as_of), side="right"))
        if cutoff == 0:
            return frame.iloc[:0]
        window = frame.iloc[:cutoff]
        # Belt and braces: the game itself must also be in the past.
        mask = window["game_date_utc"] < as_of
        if start is not None:
            mask &= window["game_date_utc"] >= start
        return window[mask]

    def team_games_asof(
        self, team_id: int, as_of: datetime, start: datetime | None = None
    ) -> pd.DataFrame:
        return self._slice(self._team_index, team_id, as_of, start)

    def pitcher_games_asof(
        self,
        pitcher_id: int,
        as_of: datetime,
        start: datetime | None = None,
        starters_only: bool = False,
    ) -> pd.DataFrame:
        frame = self._slice(self._pitcher_index, pitcher_id, as_of, start)
        if starters_only and not frame.empty:
            frame = frame[frame["is_starter"]]
        return frame

    def pitcher_statcast_asof(
        self,
        pitcher_id: int,
        as_of: datetime,
        start: datetime | None = None,
        starters_only: bool = False,
    ) -> pd.DataFrame:
        """Per-game Statcast aggregates for one pitcher, cut at ``as_of``."""
        frame = self._slice(self._pitcher_statcast_index, pitcher_id, as_of, start)
        if starters_only and not frame.empty:
            frame = frame[frame["is_starter"]]
        return frame

    def batter_statcast_asof(
        self, batter_id: int, as_of: datetime, start: datetime | None = None
    ) -> pd.DataFrame:
        return self._slice(self._batter_statcast_index, batter_id, as_of, start)

    def arsenal_asof(
        self,
        pitcher_id: int,
        as_of: datetime,
        start: datetime | None = None,
        starters_only: bool = False,
    ) -> pd.DataFrame:
        frame = self._slice(self._arsenal_index, pitcher_id, as_of, start)
        if starters_only and not frame.empty:
            frame = frame[frame["is_starter"]]
        return frame

    def batting_orders_asof(
        self, team_id: int, as_of: datetime, start: datetime | None = None
    ) -> pd.DataFrame:
        """Completed starts for a team, cut at ``as_of``.

        The lineup of the game being predicted is never in here: its
        knowledge_time is first pitch + 3h30m and as_of precedes first pitch, so
        the slice excludes it by the same rule everything else obeys.
        """
        return self._slice(self._orders_index, team_id, as_of, start)

    @property
    def has_statcast(self) -> bool:
        return not self.pitcher_statcast.empty

    @property
    def has_batter_statcast(self) -> bool:
        return not self.batter_statcast.empty and not self.batting_orders.empty

    def league_batter_statcast_asof(
        self, as_of: datetime, start: datetime | None = None
    ) -> pd.DataFrame:
        if self.batter_statcast.empty:
            return self.batter_statcast
        cutoff = int(
            np.searchsorted(
                _ns_array(self.batter_statcast["knowledge_time"]), _ns(as_of), side="right"
            )
        )
        window = self.batter_statcast.iloc[:cutoff]
        mask = window["game_date_utc"] < as_of
        if start is not None:
            mask &= window["game_date_utc"] >= start
        return window[mask]

    def league_pitcher_statcast_asof(
        self, as_of: datetime, start: datetime | None = None, starters_only: bool = False
    ) -> pd.DataFrame:
        if self.pitcher_statcast.empty:
            return self.pitcher_statcast
        cutoff = int(
            np.searchsorted(
                _ns_array(self.pitcher_statcast["knowledge_time"]), _ns(as_of),
                side="right",
            )
        )
        window = self.pitcher_statcast.iloc[:cutoff]
        mask = window["game_date_utc"] < as_of
        if start is not None:
            mask &= window["game_date_utc"] >= start
        if starters_only:
            mask &= window["is_starter"]
        return window[mask]

    def team_pitcher_games_asof(
        self,
        team_id: int,
        as_of: datetime,
        start: datetime | None = None,
        relievers_only: bool = False,
    ) -> pd.DataFrame:
        frame = self._slice(self._team_pitchers_index, team_id, as_of, start)
        if relievers_only and not frame.empty:
            frame = frame[~frame["is_starter"].astype(bool)]
        return frame

    def team_batter_games_asof(
        self, team_id: int, as_of: datetime, start: datetime | None = None
    ) -> pd.DataFrame:
        """Per-batter game lines for one team, guarded like every other slice."""
        return self._slice(self._team_batters_index, team_id, as_of, start)

    def unavailable_asof(self, as_of: datetime) -> frozenset[int]:
        """Players on the injured list as far as ``as_of`` can know.

        Cached by the minute rather than the exact instant. Every game on a
        slate is predicted at its own first pitch minus three hours, so the
        as-of values differ, but the answer changes only when a transaction is
        reported and those arrive perhaps a hundred times a day. Without the
        cache this is the single most expensive call in a walk-forward.
        """
        from app.features.availability import unavailable_as_of

        if self.injuries.empty:
            return frozenset()
        key = _ns(as_of) // 60_000_000_000
        cached = self._unavailable_cache.get(key)
        if cached is None:
            cached = frozenset(
                unavailable_as_of(self.injuries, self._injury_ns, as_of)
            )
            self._unavailable_cache[key] = cached
        return cached

    def league_pitcher_games_asof(
        self, as_of: datetime, start: datetime | None = None
    ) -> pd.DataFrame:
        if self.pitcher_games.empty:
            return self.pitcher_games
        cutoff = int(
            np.searchsorted(_ns_array(self.pitcher_games["knowledge_time"]), _ns(as_of),
                            side="right")
        )
        window = self.pitcher_games.iloc[:cutoff]
        mask = window["game_date_utc"] < as_of
        if start is not None:
            mask &= window["game_date_utc"] >= start
        return window[mask]

    def league_team_games_asof(
        self, as_of: datetime, start: datetime | None = None
    ) -> pd.DataFrame:
        if self.team_games.empty:
            return self.team_games
        cutoff = int(
            np.searchsorted(_ns_array(self.team_games["knowledge_time"]), _ns(as_of),
                            side="right")
        )
        window = self.team_games.iloc[:cutoff]
        mask = window["game_date_utc"] < as_of
        if start is not None:
            mask &= window["game_date_utc"] >= start
        return window[mask]


    def weather_asof(self, game_id: int, as_of: datetime) -> dict[str, Any] | None:
        """The latest forecast for one game that was knowable at ``as_of``.

        Latest rather than first: forecasts are append-only, so a game may have
        several, and the one a prediction should use is the most recent that
        existed when the prediction was made. Returning the earliest would
        answer a question nobody asked.
        """
        if self.weather.empty:
            return None
        rows = self._weather_index.get(int(game_id))
        if rows is None or rows.empty:
            return None
        usable = rows[_ns_array(rows["knowledge_time"]) <= _ns(as_of)]
        if usable.empty:
            return None
        return usable.iloc[-1].to_dict()

    SCHEDULE_COLUMNS = [
        "id", "game_date_utc", "official_date", "venue_id", "day_night",
        "doubleheader", "game_number", "is_home", "opponent_team_id", "is_final",
        "season",
    ]

    def team_schedule_before(self, team_id: int, as_of: datetime) -> pd.DataFrame:
        """Schedule-only projection of a team's past games.

        Score columns are dropped so a scheduling feature cannot read a result.
        """
        frame = self._schedule_index.get(int(team_id))
        if frame is None:
            return pd.DataFrame()
        past = frame[frame["game_date_utc"] < as_of]
        columns = [c for c in self.SCHEDULE_COLUMNS if c in past.columns]
        return past[columns]

    def team_schedule_all(self, team_id: int) -> pd.DataFrame:
        frame = self._schedule_index.get(int(team_id))
        return pd.DataFrame() if frame is None else frame

    # -- lookups -----------------------------------------------------------
    def pitcher_hand(self, pitcher_id: int | None) -> str | None:
        if pitcher_id is None:
            return None
        return self._player_hand.get(int(pitcher_id))

    def ballpark(self, venue_id: int | None) -> pd.Series | None:
        if venue_id is None or self._park.empty or int(venue_id) not in self._park.index:
            return None
        return self._park.loc[int(venue_id)]

    def game_starters(self, game_id: int) -> dict[int, int]:
        """team_id -> starting pitcher id for a completed game."""
        if self.pitcher_games.empty:
            return {}
        rows = self.pitcher_games[
            (self.pitcher_games["game_id"] == game_id) & self.pitcher_games["is_starter"]
        ]
        return {int(r.team_id): int(r.player_id) for r in rows.itertuples()}

    # -- integrity ---------------------------------------------------------
    def assert_as_of(self, frame: pd.DataFrame, as_of: datetime, label: str) -> None:
        """Fail loudly if a slice ever contains a fact from the future."""
        if frame.empty:
            return
        if (frame["game_date_utc"] >= as_of).any():
            raise LeakageError(f"{label}: slice contains a game at or after as_of={as_of}.")
        if (frame["knowledge_time"] > as_of).any():
            raise LeakageError(f"{label}: slice contains a fact knowable only after as_of.")
