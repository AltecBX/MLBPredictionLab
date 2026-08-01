"""Win probability by simulating runs, and the comparison that judges it.

Three feature groups have been measured against the binary win target and
rejected, each for the same reason: the signal is real but already inside team
strength by the time it reaches one bit of outcome. This changes the target
rather than the inputs.

Each side's expected runs come from the classic multiplicative combination — a
team's own scoring rate, the opponent's rate of allowing, and the league rate
that both are measured against:

    expected = league × (offense / league) × (opponent defence / league)

It has **no fitted parameters**, deliberately. Every quantity in it is an as-of
rate the store already computes, shrunk toward the league by the same rules
everything else obeys, so there is nothing here to overfit and nothing to select
on. If a parameter-free run model beats a forty-two-feature logistic regression,
that is a finding about the target; if it loses, the comparison cost one command
and the reason is legible.

The comparison follows `ensemble.compare_walk_forward` exactly: same walk-forward
steps, same games, and a blend weight searched over a grid that includes zero so
the null hypothesis can win.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from app.backtest.feature_set_compare import PairedDelta, _paired_bootstrap, _per_game_log_loss
from app.backtest.metrics import Metrics, evaluate
from app.backtest.walkforward import Step, collect_predictions, run_walk_forward
from app.core.logging import get_logger
from app.features.aggregates import team_aggregate
from app.features.asof import AsOfStore, season_start_utc
from app.features.builder import FeatureBuilder
from app.features.context import GameContext
from app.modeling.dataset import Dataset
from app.modeling.runs import DEFAULT_SIMULATIONS, fit_dispersion, simulate_game

log = get_logger(__name__)

# Shrinkage for a team's scoring and run-prevention rates, in games. The same
# constant FEATURE_DICTIONARY.md §1 uses for runs per game.
K_TEAM_RUNS = 25
MIN_GAMES = 10

# Blend weights searched. Zero is in the grid on purpose: the incumbent has to be
# beatable, which means it also has to be able to win.
WEIGHT_GRID = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.7, 1.0)

# The weight the headline number is reported at.
#
# The grid search picks whichever weight scores best on the games it is scored
# on, which is a real optimistic bias — the same one `select_hyperparameters`
# carries, and tolerable when the thing being selected is a nuisance parameter,
# but not when it IS the result. So the headline is a weight fixed in advance:
# an even split between the two models, chosen because it is the obvious a
# priori answer and not because it won anything. The searched weight is reported
# beside it, and the gap between them is how much the selection was worth.
PREREGISTERED_WEIGHT = 0.5

BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20240401


def _shrunk(rate: float | None, games: int, prior: float | None) -> float | None:
    if prior is None:
        return rate
    if rate is None or games <= 0:
        return prior
    return (rate * games + prior * K_TEAM_RUNS) / (games + K_TEAM_RUNS)


@dataclass(frozen=True, slots=True)
class RunMeans:
    home: float
    away: float
    league: float
    home_games: int
    away_games: int

    @property
    def is_usable(self) -> bool:
        return (
            self.home > 0
            and self.away > 0
            and min(self.home_games, self.away_games) >= MIN_GAMES
        )


def expected_runs(
    store: AsOfStore, builder: FeatureBuilder, ctx: GameContext, as_of: datetime
) -> RunMeans | None:
    """Each side's expected runs, from as-of rates only.

    Home-field advantage is left out of this function on purpose. It is already
    in the simulation, structurally: the home side bats in the ninth only when it
    needs to, which is where part of the advantage physically comes from. Adding
    a multiplier here as well would count it twice.
    """
    season_start = season_start_utc(ctx.season)
    league = builder.league_baseline(ctx.season, as_of)
    if league.runs_per_game is None or league.runs_per_game <= 0:
        return None

    home = team_aggregate(store.team_games_asof(ctx.home_team_id, as_of, season_start))
    away = team_aggregate(store.team_games_asof(ctx.away_team_id, as_of, season_start))
    if home.empty or away.empty:
        return None

    lg = float(league.runs_per_game)
    home_off = _shrunk(home.runs_per_game, home.games, lg)
    away_off = _shrunk(away.runs_per_game, away.games, lg)
    home_def = _shrunk(home.runs_allowed_per_game, home.games, lg)
    away_def = _shrunk(away.runs_allowed_per_game, away.games, lg)
    if None in (home_off, away_off, home_def, away_def):
        return None

    return RunMeans(
        home=lg * (home_off / lg) * (away_def / lg),
        away=lg * (away_off / lg) * (home_def / lg),
        league=lg,
        home_games=home.games,
        away_games=away.games,
    )


def simulate_slate(
    store: AsOfStore,
    builder: FeatureBuilder,
    frame: pd.DataFrame,
    size: float,
    simulations: int = DEFAULT_SIMULATIONS,
) -> pd.DataFrame:
    """Simulated home win probability for every game in ``frame``.

    Games whose run means cannot be established are returned with a null
    probability rather than a guess, and the caller drops them from the
    comparison — a model that silently emits 0.5 for a game it cannot model
    would be scored as if it had an opinion.
    """
    rows: list[dict[str, Any]] = []
    for record in frame.itertuples():
        as_of = pd.Timestamp(record.as_of).to_pydatetime()
        game_row = store.games[store.games["id"] == record.game_id]
        if game_row.empty:
            continue
        ctx = GameContext.from_row(game_row.iloc[0].to_dict())
        means = expected_runs(store, builder, ctx, as_of)
        if means is None or not means.is_usable:
            rows.append({"game_id": record.game_id, "sim_prob": None})
            continue
        # Seeded from the game id, so a rerun reproduces exactly and two games
        # never share a draw sequence.
        sim = simulate_game(
            means.home, means.away, size,
            simulations=simulations, seed=int(record.game_id) % (2**31),
        )
        rows.append({"game_id": record.game_id, "sim_prob": sim.home_win_prob})
    return pd.DataFrame(rows)


@dataclass(frozen=True, slots=True)
class SimulationComparison:
    n_games: int
    dispersion: dict[str, float]
    logistic: dict[str, Any]
    simulation: dict[str, Any]
    blended: dict[str, Any]
    blended_preregistered: dict[str, Any]
    best_weight: float
    weight_grid: dict[float, float]
    #: Paired 95% interval for the PRE-REGISTERED blend against the logistic.
    log_loss_interval: PairedDelta
    brier_interval: PairedDelta
    verdict: str
    reading: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_games": self.n_games,
            "dispersion": self.dispersion,
            "logistic": self.logistic,
            "simulation": self.simulation,
            "blended_at_searched_weight": self.blended,
            "blended_at_preregistered_weight": self.blended_preregistered,
            "preregistered_weight": PREREGISTERED_WEIGHT,
            "best_weight": self.best_weight,
            "paired_95_ci_preregistered_vs_logistic": {
                "log_loss": self.log_loss_interval.to_dict(),
                "brier": self.brier_interval.to_dict(),
            },
            "weight_grid": {str(k): v for k, v in self.weight_grid.items()},
            "verdict": self.verdict,
            "reading": self.reading,
        }


def _headline(metrics: Metrics) -> dict[str, Any]:
    d = metrics.to_dict()
    return {
        k: d.get(k)
        for k in ("n", "log_loss", "brier_score", "calibration_error", "accuracy", "roc_auc")
    }


def _blend(logistic: np.ndarray, other: np.ndarray, weight: float) -> np.ndarray:
    """Blend in log-odds, never in probability.

    Averaging probabilities pulls every prediction toward .500 — a shrinkage
    operator wearing an ensemble's hat — and it damages the tails, which is
    exactly where a win probability is decided. MODELING_PLAN.md §4.
    """
    eps = 1e-6
    a = np.clip(logistic, eps, 1 - eps)
    b = np.clip(other, eps, 1 - eps)
    logit = (1 - weight) * np.log(a / (1 - a)) + weight * np.log(b / (1 - b))
    return 1.0 / (1.0 + np.exp(-logit))


def compare_walk_forward(
    store: AsOfStore,
    dataset: Dataset,
    steps: list[Step],
    C: float,
    simulations: int = DEFAULT_SIMULATIONS,
    min_train_rows: int | None = None,
) -> SimulationComparison | None:
    """Logistic vs simulation vs blend, on identical out-of-sample games."""
    predictions = collect_predictions(
        run_walk_forward(dataset, steps, C=C, min_train_rows=min_train_rows)
    )
    if predictions.empty:
        log.warning("simulation.no_predictions")
        return None

    # Dispersion is fitted on the TRAINING side only — every game before the
    # first test window — so the simulation never sees a run total it is about
    # to be scored on.
    labelled = dataset.labelled
    first_test = min(step.test_start for step in steps)
    train = labelled[labelled["official_date"] < first_test]
    observed = _observed_runs(store, train["game_id"].tolist())
    dispersion = fit_dispersion(observed)
    log.info(
        "simulation.dispersion",
        mean=round(dispersion.mean, 3),
        ratio=round(dispersion.ratio, 3),
        size=None if not np.isfinite(dispersion.size) else round(dispersion.size, 2),
        n=dispersion.n,
    )

    builder = FeatureBuilder(store)
    sims = simulate_slate(store, builder, predictions, dispersion.size, simulations)
    merged = predictions.merge(sims, on="game_id", how="inner", validate="one_to_one")
    merged = merged[merged["sim_prob"].notna()]
    if merged.empty:
        log.warning("simulation.no_simulated_games")
        return None

    actual = merged["actual"].to_numpy()
    logistic = merged["prob"].to_numpy()
    sim = merged["sim_prob"].to_numpy(dtype=float)

    grid = {
        weight: evaluate(actual, _blend(logistic, sim, weight)).log_loss
        for weight in WEIGHT_GRID
    }
    best_weight = min(grid, key=lambda w: grid[w] if grid[w] is not None else float("inf"))

    logistic_metrics = evaluate(actual, logistic)
    sim_metrics = evaluate(actual, sim)
    blend_metrics = evaluate(actual, _blend(logistic, sim, best_weight))

    fixed = _blend(logistic, sim, PREREGISTERED_WEIGHT)
    fixed_metrics = evaluate(actual, fixed)
    ll_interval = _paired_bootstrap(
        _per_game_log_loss(actual, logistic) - _per_game_log_loss(actual, fixed)
    )
    brier_interval = _paired_bootstrap(
        (logistic - actual) ** 2 - (fixed - actual) ** 2
    )

    verdict, reading = _judge(
        logistic_metrics, sim_metrics, fixed_metrics, best_weight, ll_interval
    )
    return SimulationComparison(
        n_games=int(len(merged)),
        dispersion={
            "mean_runs": round(dispersion.mean, 3),
            "variance": round(dispersion.variance, 3),
            "variance_over_mean": round(dispersion.ratio, 3),
            "nb_size": None if not np.isfinite(dispersion.size) else round(dispersion.size, 2),
            "team_games": dispersion.n,
        },
        logistic=_headline(logistic_metrics),
        simulation=_headline(sim_metrics),
        blended=_headline(blend_metrics),
        blended_preregistered=_headline(fixed_metrics),
        log_loss_interval=ll_interval,
        brier_interval=brier_interval,
        best_weight=best_weight,
        weight_grid={w: round(v, 6) for w, v in grid.items() if v is not None},
        verdict=verdict,
        reading=reading,
    )


def _observed_runs(store: AsOfStore, game_ids: list[int]) -> np.ndarray:
    """Runs scored per team-game, for the dispersion fit."""
    if store.team_games.empty or not game_ids:
        return np.array([])
    rows = store.team_games[store.team_games["game_id"].isin(set(game_ids))]
    return rows["runs"].dropna().to_numpy(dtype=float)


def _judge(
    logistic: Metrics,
    simulation: Metrics,
    blended_fixed: Metrics,
    searched_weight: float,
    interval: PairedDelta,
) -> tuple[str, str]:
    """Judged on the PRE-REGISTERED weight and a paired interval.

    Not on the searched weight: that one was chosen on the games it is scored
    on, and a verdict taken from it would be reporting the selection as if it
    were the finding.
    """
    if logistic.log_loss is None or simulation.log_loss is None:
        return "INCONCLUSIVE", "One side produced no scorable predictions."
    if searched_weight == 0.0:
        return "REJECT", (
            "The blend weight search chose zero. The simulation carries nothing "
            "the logistic model does not already have, and the grid contained "
            "zero precisely so it could say so."
        )
    if not interval.is_distinguishable_from_zero:
        return "NO_EFFECT", (
            "At the pre-registered weight the paired interval spans zero. The "
            "searched weight scored better, but selecting it on the games it is "
            "scored on is what that number would be measuring."
        )
    if not interval.favours_candidate:
        return "REJECT", "The blend is measurably worse than the logistic model."
    return "IMPROVES", (
        f"At the pre-registered weight of {PREREGISTERED_WEIGHT} the blend lowers "
        f"out-of-sample log loss and the paired interval excludes zero. The "
        f"searched weight of {searched_weight} scores better still, and the gap "
        f"between the two is what the selection was worth. Re-measure on a second "
        f"season before serving it."
    )


__all__ = [
    "K_TEAM_RUNS",
    "WEIGHT_GRID",
    "RunMeans",
    "SimulationComparison",
    "compare_walk_forward",
    "expected_runs",
    "simulate_slate",
]
