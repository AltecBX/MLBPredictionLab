"""First-order expected-runs projection.

Phase 1 does not have the Poisson/negative-binomial run model (Phase 3) or the
Monte Carlo simulation (Phase 3). What it does have is real, as-of team scoring
and run-prevention rates, from which a standard odds-ratio expectation can be
derived:

    E[runs] = (team runs/game x opponent runs allowed/game) / league runs/game

The interval is a Poisson approximation around that mean. Both the point
estimate and the interval are returned with ``is_estimated=True`` and an
explicit ``method`` string, and the UI labels them as derived rather than
simulated. Nothing here is a placeholder — every input is an observed as-of
rate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.features.builder import FeatureVector, LeagueBaseline

METHOD = "odds_ratio_runs_v1"
INTERVAL_MASS = 0.60  # central interval reported as the projected score range


@dataclass(frozen=True, slots=True)
class RunProjection:
    home_runs: float | None
    away_runs: float | None
    home_low: int | None
    home_high: int | None
    away_low: int | None
    away_high: int | None
    is_estimated: bool
    method: str
    detail: str | None = None

    @classmethod
    def unavailable(cls, detail: str) -> RunProjection:
        return cls(None, None, None, None, None, None, True, METHOD, detail)


def _poisson_interval(mean: float, mass: float = INTERVAL_MASS) -> tuple[int, int]:
    """Narrowest central-ish interval covering ``mass`` of a Poisson(mean)."""
    if mean <= 0:
        return (0, 0)
    probabilities: list[float] = []
    term = math.exp(-mean)
    probabilities.append(term)
    for k in range(1, 25):
        term *= mean / k
        probabilities.append(term)

    best = (0, len(probabilities) - 1)
    best_width = len(probabilities)
    for lo in range(len(probabilities)):
        cumulative = 0.0
        for hi in range(lo, len(probabilities)):
            cumulative += probabilities[hi]
            if cumulative >= mass:
                if hi - lo < best_width:
                    best, best_width = (lo, hi), hi - lo
                break
    return best


def project_runs(vector: FeatureVector, baseline: LeagueBaseline) -> RunProjection:
    league = baseline.runs_per_game
    if not league or league <= 0:
        return RunProjection.unavailable("League scoring baseline is not yet computable.")

    home_offense = vector.home.get("off_runs_per_game_season").value
    away_offense = vector.away.get("off_runs_per_game_season").value
    home_prevention = vector.home.get("team_opp_adj_pitching").value
    away_prevention = vector.away.get("team_opp_adj_pitching").value

    if None in (home_offense, away_offense, home_prevention, away_prevention):
        return RunProjection.unavailable(
            "Not enough as-of scoring history to project runs for this matchup."
        )

    home_mean = max((home_offense * away_prevention) / league, 0.5)
    away_mean = max((away_offense * home_prevention) / league, 0.5)

    home_low, home_high = _poisson_interval(home_mean)
    away_low, away_high = _poisson_interval(away_mean)

    return RunProjection(
        home_runs=round(home_mean, 2),
        away_runs=round(away_mean, 2),
        home_low=home_low,
        home_high=home_high,
        away_low=away_low,
        away_high=away_high,
        is_estimated=True,
        method=METHOD,
        detail=(
            "Odds-ratio expectation from as-of team scoring and opponent-adjusted run "
            "prevention, with a Poisson central interval. The negative-binomial run "
            "model and Monte Carlo simulation arrive in Phase 3."
        ),
    )


def fair_moneyline(probability: float) -> int | None:
    """American odds implied by a probability, with no margin applied."""
    p = min(max(probability, 1e-6), 1 - 1e-6)
    if p >= 0.5:
        return int(round(-100.0 * p / (1.0 - p)))
    return int(round(100.0 * (1.0 - p) / p))
