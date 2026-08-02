"""What the served probability actually is.

Until now the served probability was the calibrated logistic model's alone. The
simulation was measured, it won on both seasons by an order of magnitude more
than any feature group managed, and it was still not served — MODELING_PLAN.md
recorded that gap rather than closing it, because promoting a model is a change
with its own before-and-after and does not come free with the measurement.

This closes it. The served probability is the **blend** of the calibrated
logistic model and the run simulation, combined in log-odds at the weight fixed
in advance:

    logit(p) = (1 − w)·logit(logistic) + w·logit(simulation),  w = 0.5

Three properties of that line are deliberate.

**The weight is pre-registered, not searched.** `simulate_check` reports a
searched weight beside the fixed one, and the two disagreed between seasons —
0.5 is not the argmax on either. Serving the argmax of a two-season grid search
would be selecting on the test set with extra steps, which is the failure this
repository's comparison protocol exists to prevent.

**The blend is in log-odds.** Averaging probabilities pulls every prediction
toward .500; it is shrinkage wearing an ensemble's hat, and it damages the tails
where a win probability is decided.

**A missing simulation is not a 0.5 simulation.** When a game cannot be
simulated — a team without enough games on record, usually in the season's first
fortnight — the served probability falls back to the logistic model alone and
says so in `component_probs`. It does not blend against a made-up number, and a
reader can tell a fallback from a blend by looking. That is the same rule that
governs UNAVAILABLE everywhere else in this repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np

from app.core.logging import get_logger
from app.features.asof import AsOfStore
from app.features.builder import FeatureBuilder
from app.features.context import GameContext
from app.modeling.runs import (
    DEFAULT_SIMULATIONS,
    Dispersion,
    GameSimulation,
    fit_dispersion,
    simulate_game,
)
from app.modeling.simulation import expected_runs

log = get_logger(__name__)

#: The weight on the simulation. Fixed in advance; see the module docstring.
SERVED_WEIGHT = 0.5

#: Below this many team-games the dispersion fit is not trustworthy and the
#: simulation is withheld rather than run on a guess. Early April on a fresh
#: database is the case this exists for.
MIN_DISPERSION_SAMPLE = 200

EPS = 1e-6


def blend_log_odds(logistic: float, simulation: float, weight: float) -> float:
    """Combine two probabilities in log-odds space.

    Mirrors `simulation._blend`, which operates on arrays for the backtest. The
    two are pinned to each other by a test: the served path and the measured
    path must not be able to drift apart, because the whole claim of this change
    is that what is served is what was measured.
    """
    a = min(max(logistic, EPS), 1 - EPS)
    b = min(max(simulation, EPS), 1 - EPS)
    logit = (1 - weight) * np.log(a / (1 - a)) + weight * np.log(b / (1 - b))
    return float(1.0 / (1.0 + np.exp(-logit)))


@dataclass(frozen=True, slots=True)
class ServedProbability:
    """The number that goes on the screen, and how it was arrived at."""

    probability: float
    #: The calibrated logistic model alone, always present.
    logistic: float
    #: The simulation alone. None when the game could not be simulated.
    simulation: float | None
    #: 0.0 when the simulation is missing, so the field always describes what
    #: was actually done rather than what would have been done.
    weight: float
    #: Populated only when the simulation ran.
    game_simulation: GameSimulation | None = None
    #: The draw seed, stored so a persisted simulation can be reproduced exactly.
    seed: int | None = None
    #: Why the simulation is missing, for the diagnostics screen. None on success.
    unavailable_reason: str | None = None

    @property
    def is_blended(self) -> bool:
        return self.simulation is not None


def dispersion_asof(
    store: AsOfStore,
    as_of: datetime,
    *,
    min_sample: int = MIN_DISPERSION_SAMPLE,
) -> Dispersion | None:
    """Fit run overdispersion from every game knowable at ``as_of``.

    The backtest fits this once on the training side. Serving cannot: there is no
    training side, only a moment. So it is fitted from every team-game the store
    will admit at ``as_of`` — the same as-of cut every feature obeys, so a game
    cannot contribute its own runs to the dispersion used to simulate it.

    **All history, not the current season.** Restricting the fit to the season in
    progress sounds more responsive and is worse on both counts. It buys nothing:
    the ratio measured on this repository's data is 2.201 in 2024 and 2.179 in
    2025, a parameter that plainly does not move. And it costs the opening
    fortnight of every season, when a hundred games have not yet been played and
    the simulation would be withheld from every card — which is precisely when a
    reader has least other information. This is a shape parameter, not a rate.
    """
    rows = store.league_team_games_asof(as_of)
    if rows.empty or "runs" not in rows:
        return None
    observed = rows["runs"].dropna().to_numpy(dtype=float)
    if observed.size < min_sample:
        return None
    return fit_dispersion(observed)


def serve_probability(
    store: AsOfStore,
    builder: FeatureBuilder,
    ctx: GameContext,
    as_of: datetime,
    logistic_probability: float,
    *,
    dispersion: Dispersion | None,
    simulations: int = DEFAULT_SIMULATIONS,
    weight: float = SERVED_WEIGHT,
) -> ServedProbability:
    """Blend the logistic model with a simulation of this game.

    ``dispersion`` is passed in rather than fitted here because it is a property
    of the slate, not of the game: fitting it once per game would repeat an
    identical scan for every game on the card.
    """
    # An infinite size is not a failure — it is the Poisson case, which
    # `sample_runs` and `partial_size` both handle explicitly. Only a genuinely
    # absent fit withholds the simulation.
    if dispersion is None:
        return ServedProbability(
            probability=logistic_probability,
            logistic=logistic_probability,
            simulation=None,
            weight=0.0,
            unavailable_reason="run dispersion not yet estimable this season",
        )

    means = expected_runs(store, builder, ctx, as_of)
    if means is None or not means.is_usable:
        return ServedProbability(
            probability=logistic_probability,
            logistic=logistic_probability,
            simulation=None,
            weight=0.0,
            unavailable_reason="not enough games on record to project runs",
        )

    # Seeded from the game id, exactly as the backtest seeds it. Re-running the
    # slate reproduces the same prediction, and two games never share a draw
    # sequence — a prediction that moves when nothing moved is not a prediction.
    seed = int(ctx.game_id) % (2**31)
    simulation = simulate_game(
        means.home,
        means.away,
        dispersion.size,
        simulations=simulations,
        seed=seed,
        distributions=True,
    )
    return ServedProbability(
        probability=blend_log_odds(logistic_probability, simulation.home_win_prob, weight),
        logistic=logistic_probability,
        simulation=simulation.home_win_prob,
        weight=weight,
        game_simulation=simulation,
        seed=seed,
    )


__all__ = [
    "MIN_DISPERSION_SAMPLE",
    "SERVED_WEIGHT",
    "ServedProbability",
    "blend_log_odds",
    "dispersion_asof",
    "serve_probability",
]
