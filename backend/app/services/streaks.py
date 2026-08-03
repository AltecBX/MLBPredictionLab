"""Winning and losing streaks: reconstruction, continuation history, and the
adjusted effect against expectation.

Research and display only — nothing here feeds the production model. The
candidate streak *features* live in `app.features.streaks` and go through the
same walk-forward ablation gate as every other candidate; this module is the
reader-facing analysis, and it is built to refuse the classic streak fallacy
rather than encourage it: every continuation rate is shown next to what the
team's pre-game Elo expectation already predicted for those same games, so "a
team on L4 wins 58% of the time" can be read against "it was expected to win
54% of those games anyway."

Ground rules, enforced in code rather than prose:

  * **Completed games only.** Regular season, final, with a recorded winner.
  * **Strict pregame state.** The streak a team carries *into* a game is
    computed from strictly earlier games; the expectation for that game is the
    pre-game Elo probability, whose engine only ever sees earlier games.
  * **Streaks reset at the season boundary.** A streak is a within-season
    object here; an offseason is not a rest day.
  * **Small samples are shrunk, not dramatised.** Every rate is reported raw
    and shrunk toward its own expectation (Beta prior, strength
    ``SHRINKAGE_K``), with a Wilson interval and an explicit insufficient-
    sample flag below ``MIN_OCCURRENCES``.
  * **No odds exist in this database**, so favorite/underdog splits are
    reported as UNAVAILABLE with the provider named — never proxied.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtest.metrics import wilson_interval
from app.core.logging import get_logger
from app.db.models.games import Game, PlayerGameStat
from app.db.models.reference import Player, Team
from app.features.elo import EloEngine

log = get_logger(__name__)

# Streak lengths reported: exactly 2..9, and 10+ pooled.
MIN_LENGTH = 2
MAX_LENGTH = 10  # the "10+" bucket

# Below this many occurrences a cell is flagged insufficient and its shrunk
# value is the only one a reader should lean on.
MIN_OCCURRENCES = 10

# Beta-prior strength for shrinkage. Ten pseudo-games of the expectation:
# strong enough that three lucky games cannot print 100%, weak enough that
# forty real games dominate it.
SHRINKAGE_K = 10.0

# Opposing-starter quality needs at least this many prior starts to be rated.
MIN_PRIOR_STARTS = 3

FIP_CONSTANT = 3.2  # constant offset only; splits compare, they do not price


@dataclass(frozen=True, slots=True)
class StreakCell:
    """One aggregation cell: a (length, sign) streak's next-game history."""

    n: int
    continued: int
    ended: int
    p_continue_raw: float | None
    p_continue_shrunk: float | None
    next_win_rate_raw: float | None
    next_win_rate_shrunk: float | None
    expected_win_rate: float | None
    adjusted_effect: float | None
    adjusted_effect_shrunk: float | None
    avg_next_run_diff: float | None
    ci_low: float | None
    ci_high: float | None
    insufficient: bool

    def to_dict(self) -> dict[str, Any]:
        def r(v: float | None, places: int = 4) -> float | None:
            return None if v is None else round(v, places)

        return {
            "n": self.n,
            "continued": self.continued,
            "ended": self.ended,
            "p_continue_raw": r(self.p_continue_raw),
            "p_continue_shrunk": r(self.p_continue_shrunk),
            "next_win_rate_raw": r(self.next_win_rate_raw),
            "next_win_rate_shrunk": r(self.next_win_rate_shrunk),
            "expected_win_rate": r(self.expected_win_rate),
            "adjusted_effect": r(self.adjusted_effect),
            "adjusted_effect_shrunk": r(self.adjusted_effect_shrunk),
            "avg_next_run_diff": r(self.avg_next_run_diff, 2),
            "ci_low": r(self.ci_low),
            "ci_high": r(self.ci_high),
            "insufficient": self.insufficient,
        }

    def compact(self) -> list[float | int | None]:
        """[n, continued, expected_win_rate, avg_next_run_diff].

        Everything else a cell reports — raw and shrunk rates, the adjusted
        effect, the Wilson interval, the insufficient flag — is a pure
        function of these four plus the payload-level constants, and the page
        derives them with the identical formulas (frontend `lib/streaks.ts`,
        pinned to this module by a shared fixture test). Sending four numbers
        instead of fifteen is what keeps thirty teams of history under half a
        megabyte instead of over four.
        """
        return [
            self.n,
            self.continued,
            None if self.expected_win_rate is None else round(self.expected_win_rate, 4),
            None if self.avg_next_run_diff is None else round(self.avg_next_run_diff, 2),
        ]


def build_team_log(games: pd.DataFrame) -> pd.DataFrame:
    """One row per completed regular-season team-game, with pregame state.

    Input is the `games` table frame (one row per game). Output is two rows
    per game — one per side — carrying, for that team: the result, the streak
    it took *into* the game, its pre-game Elo expectation, rest, and the
    opponent. Everything downstream reads this log and nothing else.
    """
    completed = games[
        games["is_final"].fillna(False)
        & games["home_win"].notna()
        & (games["game_type"] == "R")
    ].sort_values(["game_date_utc", "id"])
    if completed.empty:
        return pd.DataFrame()

    # Pre-game Elo, replayed strictly chronologically. engine.pregame records
    # the rating each side carried into each game before updating on it.
    engine = EloEngine()
    for row in completed.itertuples():
        engine.observe(
            game_id=int(row.id),
            season=int(row.season),
            home_team_id=int(row.home_team_id),
            away_team_id=int(row.away_team_id),
            home_score=int(row.home_score),
            away_score=int(row.away_score),
        )

    sides = []
    for is_home in (True, False):
        us, them = ("home", "away") if is_home else ("away", "home")
        side = pd.DataFrame(
            {
                "game_id": completed["id"].to_numpy(),
                "season": completed["season"].to_numpy(),
                "official_date": completed["official_date"].to_numpy(),
                "game_date_utc": completed["game_date_utc"].to_numpy(),
                # When the result became knowable — the feature layer filters
                # on this; the display layer (completed games only) ignores it.
                "knowledge_time": completed["knowledge_time"].to_numpy()
                if "knowledge_time" in completed.columns
                else completed["game_date_utc"].to_numpy(),
                "team_id": completed[f"{us}_team_id"].to_numpy(),
                "opponent_id": completed[f"{them}_team_id"].to_numpy(),
                "is_home": is_home,
                "runs_for": completed[f"{us}_score"].to_numpy(),
                "runs_against": completed[f"{them}_score"].to_numpy(),
                "won": (
                    completed["home_win"].astype(bool)
                    if is_home
                    else ~completed["home_win"].astype(bool)
                ).to_numpy(),
                "opp_probable_id": completed[f"{them}_probable_pitcher_id"].to_numpy(),
            }
        )
        sides.append(side)
    log_frame = pd.concat(sides, ignore_index=True)
    log_frame["run_diff"] = log_frame["runs_for"] - log_frame["runs_against"]

    # Pre-game ratings and the expectation for this side.
    pregame = engine.pregame
    own = np.array(
        [pregame.get((g, t), 1500.0) for g, t in zip(log_frame["game_id"], log_frame["team_id"], strict=False)]
    )
    opp = np.array(
        [
            pregame.get((g, t), 1500.0)
            for g, t in zip(log_frame["game_id"], log_frame["opponent_id"], strict=False)
        ]
    )
    home_exp = np.array(
        [
            EloEngine.expected_home(h, a, engine.home_advantage)
            for h, a in zip(
                np.where(log_frame["is_home"], own, opp),
                np.where(log_frame["is_home"], opp, own), strict=False,
            )
        ]
    )
    log_frame["elo_expected"] = np.where(log_frame["is_home"], home_exp, 1 - home_exp)
    log_frame["opp_elo_pregame"] = opp

    log_frame = log_frame.sort_values(["team_id", "game_date_utc", "game_id"]).reset_index(
        drop=True
    )

    # The streak carried into each game, reset each season: +k on a k-game
    # winning run, -k on a losing one, 0 before a team's first game.
    signed_after = np.zeros(len(log_frame), dtype=int)
    entering = np.zeros(len(log_frame), dtype=int)
    prev_key: tuple[int, int] | None = None
    current = 0
    for i, row in enumerate(log_frame.itertuples()):
        key = (row.team_id, row.season)
        if key != prev_key:
            current = 0
            prev_key = key
        entering[i] = current
        if row.won:
            current = current + 1 if current > 0 else 1
        else:
            current = current - 1 if current < 0 else -1
        signed_after[i] = current
    log_frame["entering"] = entering
    log_frame["streak_after"] = signed_after

    # Rest: a day between games. Doubleheaders share a date and count as none.
    dates = pd.to_datetime(log_frame["official_date"])
    prev_dates = dates.groupby(
        [log_frame["team_id"], log_frame["season"]]
    ).shift(1)
    gap = (dates - prev_dates).dt.days
    log_frame["rest_days"] = (gap - 1).clip(lower=0)
    log_frame.loc[prev_dates.isna(), "rest_days"] = np.nan
    return log_frame


def attach_context(log_frame: pd.DataFrame, session: Session) -> pd.DataFrame:
    """Opposing-starter hand and prior quality, both pregame-knowable.

    Hand comes from the probable pitcher on the schedule row; quality is a
    FIP-style rate over the starter's own strictly-earlier starts, unrated
    below MIN_PRIOR_STARTS. Both describe information available before first
    pitch — the probable is the pregame designation, and the quality window
    excludes the game itself by construction.
    """
    if log_frame.empty:
        return log_frame

    hands = pd.DataFrame(
        session.execute(select(Player.id, Player.pitch_hand)).mappings().all()
    )
    hand_map = (
        dict(zip(hands["id"], hands["pitch_hand"], strict=False)) if not hands.empty else {}
    )
    log_frame["opp_starter_hand"] = [
        hand_map.get(int(p)) if pd.notna(p) else None
        for p in log_frame["opp_probable_id"]
    ]

    starts = pd.DataFrame(
        session.execute(
            select(
                PlayerGameStat.player_id,
                PlayerGameStat.game_id,
                PlayerGameStat.game_date_utc,
                PlayerGameStat.outs_pitched,
                PlayerGameStat.so_pitched,
                PlayerGameStat.bb_allowed,
                PlayerGameStat.hbp_allowed,
                PlayerGameStat.hr_allowed,
            ).where(PlayerGameStat.games_started == 1)
        )
        .mappings()
        .all()
    )
    if starts.empty:
        log_frame["opp_starter_fip_prior"] = np.nan
        return log_frame

    starts = starts.sort_values(["player_id", "game_date_utc"]).reset_index(drop=True)
    for column in ("outs_pitched", "so_pitched", "bb_allowed", "hbp_allowed", "hr_allowed"):
        # Cumulative strictly BEFORE this start: the running total minus the
        # start's own line. (A plain shift(1) after a grouped cumsum would
        # shift across player boundaries — the classic leak.)
        values = starts[column].fillna(0)
        starts[f"cum_{column}"] = values.groupby(starts["player_id"]).cumsum() - values
    starts["prior_starts"] = starts.groupby("player_id").cumcount()
    innings = starts["cum_outs_pitched"] / 3.0
    starts["fip_prior"] = np.where(
        (starts["prior_starts"] >= MIN_PRIOR_STARTS) & (innings > 0),
        (
            13.0 * starts["cum_hr_allowed"]
            + 3.0 * (starts["cum_bb_allowed"] + starts["cum_hbp_allowed"].fillna(0))
            - 2.0 * starts["cum_so_pitched"]
        )
        / innings
        + FIP_CONSTANT,
        np.nan,
    )
    quality = starts.set_index(["player_id", "game_id"])["fip_prior"]

    keys = pd.MultiIndex.from_arrays(
        [
            log_frame["opp_probable_id"].fillna(-1).astype(int),
            log_frame["game_id"].astype(int),
        ]
    )
    log_frame["opp_starter_fip_prior"] = quality.reindex(keys).to_numpy()
    return log_frame


def _bucket_columns(log_frame: pd.DataFrame) -> pd.DataFrame:
    """Tercile buckets for opponent strength and opposing-starter quality.

    Cutoffs are global over the whole log, so a bucket means the same thing
    in every cell, and they are terciles rather than fixed numbers so each
    bucket carries comparable sample.
    """
    frame = log_frame.copy()
    elo_lo, elo_hi = frame["opp_elo_pregame"].quantile([1 / 3, 2 / 3])
    frame["opp_bucket"] = np.select(
        [frame["opp_elo_pregame"] >= elo_hi, frame["opp_elo_pregame"] <= elo_lo],
        ["strong", "weak"],
        default="average",
    )
    fip = frame["opp_starter_fip_prior"]
    fip_lo, fip_hi = fip.quantile([1 / 3, 2 / 3])
    frame["sp_bucket"] = np.select(
        [fip <= fip_lo, fip >= fip_hi],
        ["strong", "weak"],  # lower FIP is the better starter
        default="average",
    )
    frame.loc[fip.isna(), "sp_bucket"] = "unrated"
    return frame


def _shrink(successes: float, n: int, prior: float | None) -> float | None:
    if n == 0 or prior is None:
        return None
    return (successes + SHRINKAGE_K * prior) / (n + SHRINKAGE_K)


def _cell(rows: pd.DataFrame, sign: int) -> StreakCell:
    n = len(rows)
    if n == 0:
        return StreakCell(
            n=0, continued=0, ended=0, p_continue_raw=None, p_continue_shrunk=None,
            next_win_rate_raw=None, next_win_rate_shrunk=None, expected_win_rate=None,
            adjusted_effect=None, adjusted_effect_shrunk=None, avg_next_run_diff=None,
            ci_low=None, ci_high=None, insufficient=True,
        )
    wins = int(rows["won"].sum())
    win_rate = wins / n
    expected = float(rows["elo_expected"].mean())
    # A winning streak continues by winning; a losing streak by losing.
    continued = wins if sign > 0 else n - wins
    p_continue = continued / n
    expected_continue = expected if sign > 0 else 1 - expected

    shrunk_win = _shrink(wins, n, expected)
    shrunk_continue = _shrink(continued, n, expected_continue)
    low, high = wilson_interval(wins, n)
    return StreakCell(
        n=n,
        continued=continued,
        ended=n - continued,
        p_continue_raw=p_continue,
        p_continue_shrunk=shrunk_continue,
        next_win_rate_raw=win_rate,
        next_win_rate_shrunk=shrunk_win,
        expected_win_rate=expected,
        adjusted_effect=win_rate - expected,
        adjusted_effect_shrunk=(None if shrunk_win is None else shrunk_win - expected),
        avg_next_run_diff=float(rows["run_diff"].mean()),
        ci_low=low,
        ci_high=high,
        insufficient=n < MIN_OCCURRENCES,
    )


def _split_masks(rows: pd.DataFrame) -> dict[str, pd.Series]:
    """Boolean mask per split, computed once — a string query parsed per cell
    was the whole runtime of this module."""
    return {
        "home": rows["is_home"].astype(bool),
        "away": ~rows["is_home"].astype(bool),
        "rest": rows["rest_days"] >= 1,
        "no_rest": rows["rest_days"] == 0,
        "opp_strong": rows["opp_bucket"] == "strong",
        "opp_average": rows["opp_bucket"] == "average",
        "opp_weak": rows["opp_bucket"] == "weak",
        "sp_strong": rows["sp_bucket"] == "strong",
        "sp_average": rows["sp_bucket"] == "average",
        "sp_weak": rows["sp_bucket"] == "weak",
        "sp_hand_L": rows["opp_starter_hand"] == "L",
        "sp_hand_R": rows["opp_starter_hand"] == "R",
    }


def _length_bucket(entering: int) -> int:
    return min(abs(entering), MAX_LENGTH)


def continuation_tables(
    log_frame: pd.DataFrame,
    current_season: int,
    team_id: int | None = None,
) -> dict[str, Any]:
    """Continuation cells for one team (or the league when team_id is None).

    Windows: the current season, the previous three pooled, and everything
    combined. The occurrence unit is a game *entered* on a streak of exactly
    length L (10+ pooled) — a streak that reached 6 contributes one L=2 row,
    one L=3 row and so on, which is what "after a 4-game losing streak" means.
    """
    rows = log_frame if team_id is None else log_frame[log_frame["team_id"] == team_id]
    rows = rows[rows["entering"].abs() >= MIN_LENGTH].copy()
    rows["length"] = rows["entering"].map(_length_bucket)

    windows = {
        "current": rows[rows["season"] == current_season],
        "previous_three": rows[
            (rows["season"] >= current_season - 3) & (rows["season"] < current_season)
        ],
        "combined": rows,
    }
    out: dict[str, Any] = {}
    for window_key, window_rows in windows.items():
        masks = _split_masks(window_rows)
        lengths: dict[str, Any] = {}
        for sign, sign_key in ((1, "W"), (-1, "L")):
            sign_mask = np.sign(window_rows["entering"]) == sign
            for length in range(MIN_LENGTH, MAX_LENGTH + 1):
                bucket_mask = sign_mask & (window_rows["length"] == length)
                bucket = window_rows[bucket_mask]
                if bucket.empty:
                    continue
                label = f"{sign_key}{length}" if length < MAX_LENGTH else f"{sign_key}{MAX_LENGTH}+"
                entry: dict[str, Any] = {"overall": _cell(bucket, sign).compact()}
                for split_key, mask in masks.items():
                    subset = window_rows[bucket_mask & mask]
                    if subset.empty:
                        continue
                    entry[split_key] = _cell(subset, sign).compact()
                lengths[label] = entry
        out[window_key] = lengths
    return out


def _streak_string(value: int) -> str | None:
    if value == 0:
        return None
    return f"{'W' if value > 0 else 'L'}{abs(value)}"


def _venue_streak(rows: pd.DataFrame) -> int:
    """Consecutive results over this venue subset, most recent backwards."""
    if rows.empty:
        return 0
    results = rows.sort_values(["game_date_utc", "game_id"])["won"].to_numpy()
    last = results[-1]
    count = 0
    for value in results[::-1]:
        if value != last:
            break
        count += 1
    return count if last else -count


def _reach_counts(rows: pd.DataFrame) -> dict[str, int]:
    """How many distinct streaks reached each length at least once.

    Counted on distinct streaks, not games: one seven-game run reaches W2
    through W7 exactly once each.
    """
    counts = {f"W{i}": 0 for i in range(MIN_LENGTH, MAX_LENGTH)}
    counts.update({f"L{i}": 0 for i in range(MIN_LENGTH, MAX_LENGTH)})
    counts[f"W{MAX_LENGTH}+"] = 0
    counts[f"L{MAX_LENGTH}+"] = 0
    if rows.empty:
        return counts
    ordered = rows.sort_values(["game_date_utc", "game_id"])
    previous = 0
    for value in ordered["streak_after"]:
        magnitude, sign = abs(value), np.sign(value)
        # A streak "reaches" length k the first time streak_after hits ±k.
        if magnitude >= MIN_LENGTH and (np.sign(previous) != sign or abs(previous) < magnitude):
            if magnitude < MAX_LENGTH:
                counts[f"{'W' if sign > 0 else 'L'}{magnitude}"] += 1
            elif magnitude == MAX_LENGTH:
                counts[f"{'W' if sign > 0 else 'L'}{MAX_LENGTH}+"] += 1
        previous = value
    return counts


def team_current_block(
    log_frame: pd.DataFrame,
    team_id: int,
    current_season: int,
    team_names: dict[int, dict[str, str]],
) -> dict[str, Any] | None:
    """The reader-facing current-streak block for one team."""
    rows = log_frame[
        (log_frame["team_id"] == team_id) & (log_frame["season"] == current_season)
    ].sort_values(["game_date_utc", "game_id"])
    if rows.empty:
        return None

    current = int(rows["streak_after"].iloc[-1])
    streak_rows = rows.tail(abs(current)) if current != 0 else rows.iloc[0:0]
    inside = [
        {
            "date": str(r.official_date),
            "opponent": team_names.get(int(r.opponent_id), {}).get("abbreviation", "?"),
            "home": bool(r.is_home),
            "result": f"{'W' if r.won else 'L'} {int(r.runs_for)}-{int(r.runs_against)}",
        }
        for r in streak_rows.itertuples()
    ]

    after = rows["streak_after"]
    return {
        "team_id": int(team_id),
        "abbreviation": team_names.get(int(team_id), {}).get("abbreviation", "?"),
        "name": team_names.get(int(team_id), {}).get("name", str(team_id)),
        "current_streak": _streak_string(current),
        "current_streak_start": str(streak_rows["official_date"].iloc[0]) if current else None,
        "streak_games": inside,
        "home_streak": _streak_string(_venue_streak(rows[rows["is_home"]])),
        "away_streak": _streak_string(_venue_streak(rows[~rows["is_home"]])),
        "longest_win_streak": int(after.max()) if (after > 0).any() else 0,
        "longest_loss_streak": int(-after.min()) if (after < 0).any() else 0,
        "reach_counts_season": _reach_counts(rows),
        "reach_counts_combined": _reach_counts(log_frame[log_frame["team_id"] == team_id]),
        "games_played": int(len(rows)),
    }


def load_games_frame(session: Session) -> pd.DataFrame:
    rows = session.execute(
        select(
            Game.id, Game.season, Game.game_type, Game.official_date,
            Game.game_date_utc, Game.home_team_id, Game.away_team_id,
            Game.home_score, Game.away_score, Game.home_win, Game.is_final,
            Game.home_probable_pitcher_id, Game.away_probable_pitcher_id,
            Game.knowledge_time,
        )
    ).mappings().all()
    return pd.DataFrame(rows)


def load_team_names(session: Session) -> dict[int, dict[str, str]]:
    rows = session.execute(select(Team.id, Team.abbreviation, Team.name)).mappings().all()
    return {int(r["id"]): {"abbreviation": r["abbreviation"], "name": r["name"]} for r in rows}


def build_streaks_payload(session: Session, today: date | None = None) -> dict[str, Any]:
    """Everything the streaks page shows, computed from completed games only."""
    games = load_games_frame(session)
    if games.empty:
        return {"available": False, "reason": "No games are ingested."}

    log_frame = build_team_log(games)
    if log_frame.empty:
        return {"available": False, "reason": "No completed regular-season games."}
    log_frame = attach_context(log_frame, session)
    log_frame = _bucket_columns(log_frame)

    current_season = int(log_frame["season"].max())
    team_names = load_team_names(session)
    team_ids = sorted(log_frame["team_id"].unique())

    teams = []
    for team_id in team_ids:
        block = team_current_block(log_frame, team_id, current_season, team_names)
        if block is None:
            continue
        block["continuation"] = continuation_tables(log_frame, current_season, team_id)
        teams.append(block)

    payload: dict[str, Any] = {
        "available": True,
        "current_season": current_season,
        "seasons": sorted(int(s) for s in log_frame["season"].unique()),
        "min_occurrences": MIN_OCCURRENCES,
        "shrinkage_k": SHRINKAGE_K,
        "expectation_model": (
            "Pre-game Elo with home advantage, replayed strictly chronologically. "
            "Adjusted effect = actual next-game win rate minus this expectation."
        ),
        "favorite_underdog": {
            "available": False,
            "reason": (
                "No historical odds exist in this database. Favorite/underdog "
                "splits require a licensed odds provider (ODDS_PROVIDER)."
            ),
        },
        "league": continuation_tables(log_frame, current_season, None),
        "teams": teams,
        "next_games": _next_games(session, games, log_frame, team_names, today),
    }
    return payload


def _next_games(
    session: Session,
    games: pd.DataFrame,
    log_frame: pd.DataFrame,
    team_names: dict[int, dict[str, str]],
    today: date | None,
) -> list[dict[str, Any]]:
    """Today's not-yet-final games with each side's streak context.

    The model probability is the latest stored prediction for the game — the
    same immutable record the Game Center serves — or absent, never invented.
    """
    from app.db.models.modeling import Prediction

    today = today or pd.Timestamp.utcnow().date()
    slate = games[
        (games["official_date"] == today) & ~games["is_final"].fillna(False)
    ].sort_values("game_date_utc")
    if slate.empty:
        return []

    predictions = pd.DataFrame(
        session.execute(
            select(
                Prediction.game_id, Prediction.home_win_prob, Prediction.as_of
            ).where(Prediction.game_id.in_([int(g) for g in slate["id"]]))
        ).mappings().all()
    )
    latest: dict[int, float] = {}
    if not predictions.empty:
        newest = predictions.sort_values("as_of").groupby("game_id").tail(1)
        latest = {
            int(r["game_id"]): float(r["home_win_prob"]) for _, r in newest.iterrows()
        }

    current_season = int(log_frame["season"].max())

    def side_context(team_id: int) -> dict[str, Any]:
        rows = log_frame[log_frame["team_id"] == team_id]
        # Streaks are within-season objects: a team with no completed game
        # this season carries no streak, whatever last season ended on.
        season_rows = rows[rows["season"] == current_season]
        current = int(season_rows["streak_after"].iloc[-1]) if len(season_rows) else 0
        context: dict[str, Any] = {
            "team_id": int(team_id),
            "abbreviation": team_names.get(int(team_id), {}).get("abbreviation", "?"),
            "current_streak": _streak_string(current),
        }
        if abs(current) >= MIN_LENGTH:
            sign = 1 if current > 0 else -1
            occurrences = rows[
                (rows["entering"].abs() >= MIN_LENGTH)
                & (np.sign(rows["entering"]) == sign)
                & (rows["entering"].abs().map(_length_bucket) == _length_bucket(current))
            ]
            context["history"] = _cell(occurrences, sign).to_dict()
        return context

    out = []
    for row in slate.itertuples():
        out.append(
            {
                "game_id": int(row.id),
                "first_pitch_utc": str(row.game_date_utc),
                "home": side_context(int(row.home_team_id)),
                "away": side_context(int(row.away_team_id)),
                "model_home_win_prob": latest.get(int(row.id)),
            }
        )
    return out
