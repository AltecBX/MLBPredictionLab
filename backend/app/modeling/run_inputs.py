"""Who is actually pitching, expressed as a deviation from the team's staff.

The run model's defensive term is a team's runs allowed per game. That number
averages over the whole staff, so it says the same thing about a game started by
an ace as about one started by the fifth starter — and roughly half a game's
innings belong to that one pitcher. The team rate is not wrong; it is answering a
question one level coarser than the one being asked.

This splits it. A team's defensive innings divide exactly into the starter's and
the bullpen's, so

    R_team = s · R_starter + (1 − s) · R_bullpen

is an **identity**, not a model, as long as all three come from the same window.
That is the reason everything here is season-to-date: the same window the team
rate already uses. Reaching for a longer window on the starter alone would buy
some signal and break the identity, and a decomposition that does not reconstruct
what it decomposes is a much worse trade than it looks.

What the model then supplies is the *named* starter in place of the average one:

    multiplier = (s · R_thisStarter + (1 − s) · R_bullpen) / R_team

Both components are shrunk toward the team rate by their own innings, so a
pitcher with no record contributes no deviation and the multiplier is exactly
1.0. That is the property that makes this a refinement of the run model rather
than a replacement for it: with no information it reproduces the existing model
exactly, and every departure from 1.0 is something measured.

**No starter named is not the same as an average starter.** It returns a
multiplier of 1.0 too, but `is_measured` is False, and the two are distinguished
everywhere they are reported. A missing probable pitcher is an absence of
knowledge; a 1.0 multiplier from a genuinely league-average ace is a finding.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

# Innings at which a rate is trusted halfway against the team it is shrunk
# toward. A starter reaches forty innings in about seven starts; a bullpen
# reaches eighty in about six weeks. Both are pre-registered — the ablation that
# judges this model does not search over them.
K_STARTER_INNINGS = 40.0
K_BULLPEN_INNINGS = 80.0

# Innings at which the starter's share of the game is trusted halfway.
K_SHARE_STARTS = 8.0

# A team needs a real sample before its own rate can anchor anything.
MIN_TEAM_INNINGS = 60.0
MIN_TEAM_GAMES = 10

# The multiplier is a ratio of two noisy rates and its tails are not meaningful.
# A starter three runs per nine better than his own bullpen over four starts is a
# small sample, not a different sport. The band is wide enough to leave every
# real effect intact and narrow enough that one freak line cannot hand a team a
# two-run edge.
MULTIPLIER_FLOOR = 0.70
MULTIPLIER_CEILING = 1.40


@dataclass(frozen=True, slots=True)
class PitchingSplit:
    """How much a named starter moves a team's expected runs allowed."""

    #: Multiply the opponent's expected runs by this. Exactly 1.0 when unmeasured.
    multiplier: float
    is_measured: bool
    reason: str | None = None
    starter_share: float | None = None
    starter_runs_per_9: float | None = None
    bullpen_runs_per_9: float | None = None
    team_runs_per_9: float | None = None
    starter_innings: float = 0.0

    def to_dict(self) -> dict[str, float | bool | str | None]:
        return {
            "multiplier": round(self.multiplier, 4),
            "is_measured": self.is_measured,
            "reason": self.reason,
            "starter_share": None if self.starter_share is None else round(self.starter_share, 4),
            "starter_runs_per_9": (
                None if self.starter_runs_per_9 is None else round(self.starter_runs_per_9, 3)
            ),
            "bullpen_runs_per_9": (
                None if self.bullpen_runs_per_9 is None else round(self.bullpen_runs_per_9, 3)
            ),
            "team_runs_per_9": (
                None if self.team_runs_per_9 is None else round(self.team_runs_per_9, 3)
            ),
            "starter_innings": round(self.starter_innings, 1),
        }


NO_SPLIT = PitchingSplit(1.0, False, "no starter named")


def _shrink(rate: float, weight: float, toward: float, k: float) -> float:
    """Regress ``rate`` toward ``toward``, by its own sample size."""
    if weight <= 0:
        return toward
    return (rate * weight + toward * k) / (weight + k)


def _innings(frame: pd.DataFrame) -> float:
    if frame.empty or "outs_pitched" not in frame:
        return 0.0
    return float(pd.to_numeric(frame["outs_pitched"], errors="coerce").fillna(0).sum()) / 3.0


def _runs(frame: pd.DataFrame) -> float:
    if frame.empty or "runs_allowed" not in frame:
        return 0.0
    return float(pd.to_numeric(frame["runs_allowed"], errors="coerce").fillna(0).sum())


def pitching_split(
    store,  # AsOfStore; untyped to avoid a circular import
    team_id: int,
    starter_id: int | None,
    as_of: datetime,
    season_start: datetime,
) -> PitchingSplit:
    """The named starter's deviation from his own team's staff, as-of.

    Everything is read through the as-of store, so no game at or after ``as_of``
    can reach this, and a starter's line from the game being predicted is not
    knowable at the moment it is predicted.
    """
    if starter_id is None:
        return NO_SPLIT

    staff = store.team_pitcher_games_asof(team_id, as_of, season_start)
    if staff.empty:
        return PitchingSplit(1.0, False, "no team pitching on record")

    team_innings = _innings(staff)
    team_games = int(staff["game_id"].nunique())
    if team_innings < MIN_TEAM_INNINGS or team_games < MIN_TEAM_GAMES:
        return PitchingSplit(1.0, False, "team sample too small to anchor a split")

    team_r9 = 9.0 * _runs(staff) / team_innings
    innings_per_game = team_innings / team_games
    if team_r9 <= 0 or innings_per_game <= 0:
        return PitchingSplit(1.0, False, "team allowed no runs in the window")

    is_starter = staff["is_starter"].fillna(False).astype(bool)
    relief = staff[~is_starter]
    relief_innings = _innings(relief)
    bullpen_r9 = (
        9.0 * _runs(relief) / relief_innings if relief_innings > 0 else team_r9
    )

    starts = store.pitcher_games_asof(
        int(starter_id), as_of, season_start, starters_only=True
    )
    starter_innings = _innings(starts)
    n_starts = int(len(starts))
    if starter_innings <= 0 or n_starts == 0:
        # He is on the card but has not started this season. The bullpen half is
        # still known, but with no starter rate the whole expression collapses
        # back to the team rate, which is what the base model already says.
        return PitchingSplit(
            1.0, False, "starter has no starts in the window",
            bullpen_runs_per_9=bullpen_r9, team_runs_per_9=team_r9,
        )

    starter_r9_raw = 9.0 * _runs(starts) / starter_innings

    # Both rates regress toward the team, so an unknown pitcher and an unknown
    # bullpen both contribute nothing and the multiplier lands on exactly 1.0.
    starter_r9 = _shrink(starter_r9_raw, starter_innings, team_r9, K_STARTER_INNINGS)
    bullpen_r9_shrunk = _shrink(bullpen_r9, relief_innings, team_r9, K_BULLPEN_INNINGS)

    # The starter's share of the innings, regressed toward his own team's split
    # so that a pitcher with two starts does not set the workload for the game.
    team_starter_innings = _innings(staff[is_starter])
    team_share = team_starter_innings / team_innings if team_innings > 0 else 0.5
    share_raw = (starter_innings / n_starts) / innings_per_game
    share = _shrink(share_raw, float(n_starts), team_share, K_SHARE_STARTS)
    share = min(max(share, 0.0), 1.0)

    blended = share * starter_r9 + (1.0 - share) * bullpen_r9_shrunk
    multiplier = blended / team_r9
    multiplier = min(max(multiplier, MULTIPLIER_FLOOR), MULTIPLIER_CEILING)

    return PitchingSplit(
        multiplier=multiplier,
        is_measured=True,
        starter_share=share,
        starter_runs_per_9=starter_r9,
        bullpen_runs_per_9=bullpen_r9_shrunk,
        team_runs_per_9=team_r9,
        starter_innings=starter_innings,
    )


@dataclass(frozen=True, slots=True)
class RunModel:
    """Which refinements a run model applies.

    Named so an ablation can report which one earned its place. `base` is the
    model already measured and merged — two teams' season rates and nothing else
    — and every variant has to beat it on the same games to be worth anything.
    """

    name: str
    park: bool = False
    pitching: bool = False
    #: Replace the season-to-date, league-shrunk team rates with the
    #: multi-season projections (features/projections.py). The base model's
    #: rates start every April at the league average; the projection starts
    #: where the previous seasons left off.
    projected: bool = False


BASE = RunModel("base")
PARK_ONLY = RunModel("park", park=True)
PITCHING_ONLY = RunModel("pitching", pitching=True)
PARK_AND_PITCHING = RunModel("park+pitching", park=True, pitching=True)
PROJECTED = RunModel("projected", projected=True)

#: The order they are reported in. Base first, because it is the incumbent
#: the ablation measures refinements against.
VARIANTS = (BASE, PARK_ONLY, PITCHING_ONLY, PARK_AND_PITCHING, PROJECTED)

#: The run model the product serves and the blend measurement scores.
#: Promoted from BASE on the walk-forward evidence in MODELING_PLAN.md
#: § Multi-season projections: the projected means beat the base run model
#: in every season and the served blend with them improves with the paired
#: interval excluding zero. A game whose projection cannot be formed falls
#: back to the base means, and says so.
SERVED = PROJECTED


@dataclass(frozen=True, slots=True)
class RunComponents:
    """Everything the four variants need, computed once per game.

    The variants differ only in which of these they use. Computing them once and
    combining arithmetically is not merely faster than four passes over the
    as-of store — it is the only way to be certain the variants differ in what
    they are supposed to differ in and nothing else.
    """

    #: The base model's expected runs, before any refinement.
    home: float
    away: float
    league: float
    home_games: int
    away_games: int
    #: The park the game is played in, and each side's accumulated exposure.
    park_factor: float = 1.0
    home_exposure: float = 1.0
    away_exposure: float = 1.0
    park_measured: bool = False
    #: Each team's own pitching, which acts on the OPPOSING side's runs.
    home_pitching: PitchingSplit = NO_SPLIT
    away_pitching: PitchingSplit = NO_SPLIT
    #: The same expected runs from the multi-season projections. None when a
    #: projection could not be formed, in which case the variant falls back to
    #: the base means and says so through `projected_measured`.
    home_projected: float | None = None
    away_projected: float | None = None

    @property
    def projected_measured(self) -> bool:
        return self.home_projected is not None and self.away_projected is not None

    @property
    def park_adjustment(self) -> float:
        """One scalar, applied to both sides — a park acts on total runs.

        Each side's rate is first divided by the park environment it was
        accumulated in, then the current park is applied once. Both exposures
        appear because the expected total is a product of one team's offence and
        the other's defence, and each carries its own park history.
        """
        denominator = self.home_exposure * self.away_exposure
        if denominator <= 0:
            return 1.0
        return self.park_factor / denominator

    def means(self, model: RunModel) -> tuple[float, float]:
        """(home, away) expected runs under ``model``."""
        home, away = self.home, self.away
        if model.projected and self.projected_measured:
            home, away = float(self.home_projected), float(self.away_projected)  # type: ignore[arg-type]
        if model.pitching:
            # The away team's pitching decides what the home team scores.
            home *= self.away_pitching.multiplier
            away *= self.home_pitching.multiplier
        if model.park:
            adjustment = self.park_adjustment
            home *= adjustment
            away *= adjustment
        return home, away


__all__ = [
    "BASE",
    "K_BULLPEN_INNINGS",
    "K_SHARE_STARTS",
    "K_STARTER_INNINGS",
    "MULTIPLIER_CEILING",
    "MULTIPLIER_FLOOR",
    "NO_SPLIT",
    "PARK_AND_PITCHING",
    "PARK_ONLY",
    "PITCHING_ONLY",
    "PROJECTED",
    "SERVED",
    "VARIANTS",
    "PitchingSplit",
    "RunComponents",
    "RunModel",
    "pitching_split",
]
