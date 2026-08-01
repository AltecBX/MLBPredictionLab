"""Standings, home/road splits and streaks — display context, not model input.

Everything here is *derived from already-ingested game results*. There is no new
provider and no new external dependency: `teams` already carries league and
division, and `games` already carries a dated, knowledge-time-guarded outcome
for every completed game. Nothing is fetched, nothing is estimated, and a team
with no completed games returns empty rather than zeroes.

Why this is display-only, deliberately:

The model already contains most of what a standings table encodes. `elo_diff`,
`team_win_pct_season_diff`, `team_pythag_win_pct_diff` and
`team_home_away_split_diff` are all fitted features derived from the same
results. Feeding rank or games-behind back in would be close to duplicating
those with a coarser, noisier encoding, and streak length in particular is a
classic small-sample trap — six wins is six games, and the stabilized 14-day
form delta already carries that signal with shrinkage applied.

So: this module never writes to a feature vector. `STREAK_IS_CONTEXT_ONLY`
below is asserted by a test. If a streak feature is ever proposed, it goes
through `run_ablation` first and only survives if it improves out-of-sample log
loss (BACKTEST_PLAN.md §7).

As-of correctness is preserved even though this is display data: every query
filters on `knowledge_time <= as_of` and `game_date_utc < as_of`, the same cut
the feature layer uses. A game's own result can therefore never appear in the
standings shown beside its prediction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Game, Team

# This module must never contribute to a model input. Asserted by test.
STREAK_IS_CONTEXT_ONLY = True

# Regular season only: spring training and postseason games do not belong in a
# standings computation, and their game_type codes are distinct.
REGULAR_SEASON = "R"

SCHEDULED_GAMES = 162

# MLB's current format: three division winners plus three wild cards per league.
WILDCARD_SPOTS = 3


@dataclass(frozen=True, slots=True)
class Record:
    wins: int
    losses: int

    @property
    def games(self) -> int:
        return self.wins + self.losses

    @property
    def win_pct(self) -> float | None:
        """None rather than .000 when nothing has been played.

        A team with no games has no win percentage. Returning 0.0 would render
        as a real, terrible record.
        """
        return round(self.wins / self.games, 4) if self.games else None


@dataclass(frozen=True, slots=True)
class StreakGame:
    """One game inside the current streak, so the reader can judge its weight."""

    game_id: int
    date: str
    opponent_id: int
    opponent: str
    is_home: bool
    runs_for: int
    runs_against: int


@dataclass(frozen=True, slots=True)
class Streak:
    kind: str  # 'W' | 'L'
    length: int
    games: list[StreakGame] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.kind}{self.length}"


@dataclass(frozen=True, slots=True)
class Standing:
    division_name: str | None
    division_rank: int | None
    games_behind: float | None
    league_name: str | None
    league_rank: int | None
    wildcard_rank: int | None
    wildcard_games_behind: float | None
    in_playoff_position: bool
    elimination_number: int | None
    clinched_division: bool
    eliminated: bool


@dataclass(frozen=True, slots=True)
class TeamContext:
    team_id: int
    overall: Record
    home: Record
    away: Record
    streak: Streak | None
    standing: Standing | None
    as_of: datetime


def _completed_games(session: Session, season: int, as_of: datetime) -> list[Game]:
    """Every completed regular-season game knowable at ``as_of``.

    The two filters are the same pair the feature layer uses: `knowledge_time`
    for when the result became knowable, and `game_date_utc` so a game cannot
    contribute to context shown beside its own prediction.
    """
    stmt = (
        select(Game)
        .where(
            Game.season == season,
            Game.game_type == REGULAR_SEASON,
            Game.is_final.is_(True),
            Game.home_win.isnot(None),
            Game.knowledge_time <= as_of,
            Game.game_date_utc < as_of,
        )
        .order_by(Game.game_date_utc)
    )
    return list(session.scalars(stmt))


def _split_records(games: list[Game]) -> dict[int, tuple[Record, Record]]:
    """(home record, away record) per team."""
    tally: dict[int, list[int]] = {}
    for game in games:
        home_won = bool(game.home_win)
        for team_id, is_home, won in (
            (game.home_team_id, True, home_won),
            (game.away_team_id, False, not home_won),
        ):
            row = tally.setdefault(team_id, [0, 0, 0, 0])  # hw, hl, aw, al
            base = 0 if is_home else 2
            row[base + (0 if won else 1)] += 1
    return {
        team_id: (Record(r[0], r[1]), Record(r[2], r[3])) for team_id, r in tally.items()
    }


def _streaks(
    games: list[Game], team_names: dict[int, str], keep: int = 12
) -> dict[int, Streak]:
    """Current unbroken run of wins or losses, most recent game last.

    ``keep`` bounds how many games are attached to the streak. A 20-game run is
    real but nobody reads twenty rows on a phone, and the length is reported
    separately, so truncation loses nothing.
    """
    per_team: dict[int, list[tuple[Game, bool]]] = {}
    for game in games:
        home_won = bool(game.home_win)
        per_team.setdefault(game.home_team_id, []).append((game, home_won))
        per_team.setdefault(game.away_team_id, []).append((game, not home_won))

    out: dict[int, Streak] = {}
    for team_id, history in per_team.items():
        if not history:
            continue
        latest_won = history[-1][1]
        run: list[tuple[Game, bool]] = []
        for game, won in reversed(history):
            if won != latest_won:
                break
            run.append((game, won))
        run.reverse()

        shown = run[-keep:]
        entries = [
            StreakGame(
                game_id=game.id,
                date=game.official_date.isoformat(),
                opponent_id=(
                    game.away_team_id if game.home_team_id == team_id else game.home_team_id
                ),
                opponent=team_names.get(
                    game.away_team_id if game.home_team_id == team_id else game.home_team_id,
                    "Unknown",
                ),
                is_home=game.home_team_id == team_id,
                runs_for=(
                    game.home_score if game.home_team_id == team_id else game.away_score
                )
                or 0,
                runs_against=(
                    game.away_score if game.home_team_id == team_id else game.home_score
                )
                or 0,
            )
            for game, _ in shown
        ]
        out[team_id] = Streak("W" if latest_won else "L", len(run), entries)
    return out


def _games_behind(leader: Record, team: Record) -> float:
    """Standard games-behind: half the sum of the win gap and the loss gap."""
    return round(((leader.wins - team.wins) + (team.losses - leader.losses)) / 2, 1)


def _standings(
    overall: dict[int, Record], teams: dict[int, Team]
) -> dict[int, Standing]:
    """Division rank, games behind, wild card position and elimination status.

    Elimination uses the tragic number: a team is eliminated once the leader has
    more wins than the team can still reach. `SCHEDULED_GAMES` is the season
    length, so a team that has played fewer games than that has the difference
    remaining — which is exactly right during the season and degrades to zero
    at the end of it.
    """
    by_division: dict[int, list[int]] = {}
    by_league: dict[int, list[int]] = {}
    for team_id, team in teams.items():
        if team_id not in overall:
            continue
        if team.division_id is not None:
            by_division.setdefault(team.division_id, []).append(team_id)
        if team.league_id is not None:
            by_league.setdefault(team.league_id, []).append(team_id)

    def rank_key(team_id: int) -> tuple[float, int]:
        rec = overall[team_id]
        # Win percentage, then wins, so a team that has played fewer games is
        # not punished for it.
        return (-(rec.win_pct or 0.0), -rec.wins)

    division_leader: dict[int, int] = {}
    division_order: dict[int, list[int]] = {}
    for division_id, members in by_division.items():
        ordered = sorted(members, key=rank_key)
        division_order[division_id] = ordered
        division_leader[division_id] = ordered[0]

    # Wild card: every non-leader in the league, ranked together.
    wildcard_order: dict[int, list[int]] = {}
    for league_id, members in by_league.items():
        leaders = {division_leader[d] for d in by_division if division_leader[d] in members}
        contenders = [t for t in members if t not in leaders]
        wildcard_order[league_id] = sorted(contenders, key=rank_key)

    out: dict[int, Standing] = {}
    for team_id, team in teams.items():
        if team_id not in overall:
            continue
        rec = overall[team_id]
        division_id, league_id = team.division_id, team.league_id

        div_rank = gb = None
        clinched = eliminated = False
        elimination_number = None
        if division_id in division_order:
            ordered = division_order[division_id]
            div_rank = ordered.index(team_id) + 1
            leader = overall[division_leader[division_id]]
            gb = _games_behind(leader, rec)

            remaining = max(SCHEDULED_GAMES - rec.games, 0)
            max_reachable = rec.wins + remaining
            if team_id == division_leader[division_id]:
                elimination_number = None
                runner_up = ordered[1] if len(ordered) > 1 else None
                if runner_up is not None:
                    challenger = overall[runner_up]
                    challenger_max = challenger.wins + max(
                        SCHEDULED_GAMES - challenger.games, 0
                    )
                    clinched = rec.wins > challenger_max
            else:
                # Tragic number: combined leader-wins and own-losses still
                # needed to put this team out. Equivalently the leader must
                # reach one more win than this team can possibly finish with,
                # so it is (this team's ceiling) - (leader's wins) + 1 — not
                # the other way round, which inflates it by the gap twice over.
                elimination_number = max(max_reachable - leader.wins + 1, 0)
                eliminated = leader.wins > max_reachable

        wc_rank = wc_gb = None
        in_position = False
        if league_id in wildcard_order and team_id in wildcard_order[league_id]:
            order = wildcard_order[league_id]
            wc_rank = order.index(team_id) + 1
            cutoff = order[WILDCARD_SPOTS - 1] if len(order) >= WILDCARD_SPOTS else None
            if cutoff is not None:
                wc_gb = _games_behind(overall[cutoff], rec)
            in_position = wc_rank <= WILDCARD_SPOTS
        elif division_id in division_order and team_id == division_leader[division_id]:
            in_position = True

        league_rank = None
        if league_id in by_league:
            league_rank = sorted(by_league[league_id], key=rank_key).index(team_id) + 1

        out[team_id] = Standing(
            division_name=team.division_name,
            division_rank=div_rank,
            games_behind=gb,
            league_name=team.league_name,
            league_rank=league_rank,
            wildcard_rank=wc_rank,
            wildcard_games_behind=wc_gb,
            in_playoff_position=in_position,
            elimination_number=elimination_number,
            clinched_division=clinched,
            eliminated=eliminated,
        )
    return out


class SeasonResults:
    """One season's completed games, loaded once and sliced in memory.

    A slate is a dozen games with a dozen distinct first pitches, and each wants
    its own as-of cut. Re-querying per game would be twelve scans of the season;
    this is one, and the per-game slice is a bisect over a sorted list.
    """

    def __init__(self, session: Session, season: int, horizon: datetime) -> None:
        self.season = season
        self._games = _completed_games(session, season, horizon)
        self._teams = {t.id: t for t in session.scalars(select(Team))}
        self._names = {t.id: t.team_name or t.name for t in self._teams.values()}
        # Sorted by game_date_utc already; keep the parallel key list for bisect.
        self._keys = [g.game_date_utc for g in self._games]

    def _through(self, as_of: datetime) -> list[Game]:
        from bisect import bisect_left

        cut = bisect_left(self._keys, as_of)
        # knowledge_time can trail game_date_utc, so re-apply it rather than
        # trusting the date cut alone.
        return [g for g in self._games[:cut] if g.knowledge_time <= as_of]

    def context_at(
        self, as_of: datetime, team_ids: set[int] | None = None
    ) -> dict[int, TeamContext]:
        """Records, streaks and standings as known at ``as_of``.

        Computed league-wide because standings are inherently relative — a
        team's division rank cannot be derived from its own games — then
        filtered to ``team_ids`` on the way out.
        """
        games = self._through(as_of)
        splits = _split_records(games)
        overall = {
            team_id: Record(home.wins + away.wins, home.losses + away.losses)
            for team_id, (home, away) in splits.items()
        }
        streaks = _streaks(games, self._names)
        standings = _standings(overall, self._teams)

        wanted = team_ids if team_ids is not None else set(splits)
        return {
            team_id: TeamContext(
                team_id=team_id,
                overall=overall[team_id],
                home=splits[team_id][0],
                away=splits[team_id][1],
                streak=streaks.get(team_id),
                standing=standings.get(team_id),
                as_of=as_of,
            )
            for team_id in wanted
            if team_id in splits
        }


def team_contexts(
    session: Session, season: int, as_of: datetime, team_ids: set[int] | None = None
) -> dict[int, TeamContext]:
    """Single-shot convenience wrapper over :class:`SeasonResults`."""
    return SeasonResults(session, season, as_of).context_at(as_of, team_ids)


__all__ = [
    "Record",
    "SeasonResults",
    "Standing",
    "Streak",
    "StreakGame",
    "TeamContext",
    "STREAK_IS_CONTEXT_ONLY",
    "team_contexts",
]
