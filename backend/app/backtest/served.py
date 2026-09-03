"""The served number, scored on the same walk-forward games as the logistic.

The backtest walked the logistic model forward and reported that. The product
serves something else: the calibrated logistic blended in log-odds with the run
simulation at the pre-registered weight, or the logistic alone for a game the
simulation cannot form (`app.modeling.serving`). The two do not calibrate the
same way — measured over 2024–26, the logistic's favourites above 65% won about
five points less often than stated, the simulation's about ten points *more*,
and the blend landed within two points of its word — so a reliability report on
the component was not a reliability report on the figure a reader acts on.

This scores the served figure on the same games, with the same leak-free
inputs the logistic's walk-forward already established:

* The simulation's dispersion is fitted the way serving fits it: from every
  team-game knowable at each slate's moment (`serving.dispersion_asof`), with
  the training-side fit — every game before the first game that is scored —
  as the fallback for a slate whose sample is still too small, which is when
  serving declines to simulate at all.
* Each game's run means are as-of the same moment the logistic's features
  were, through the same store and builder, so nothing the simulation reads is
  later than what the logistic read. The projected means come first and the
  season-to-date means second, as they do in `serve_probability`.
* A game the simulation cannot form is served as the logistic alone, which is
  what the product does, and is counted as such rather than blended against a
  made-up number.

The served slices are stored beside the component's under a prefixed slice
type, and the product's reliability readouts — the backtest page, the game
page's evidence band, the health screen — read the served rows when a run has
them and the component's when it does not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtest.metrics import Metrics, evaluate
from app.core.logging import get_logger
from app.db.models import BacktestResult
from app.features.asof import AsOfStore
from app.features.builder import FeatureBuilder
from app.modeling.dataset import Dataset
from app.modeling.run_inputs import SERVED
from app.modeling.runs import DEFAULT_SIMULATIONS, fit_dispersion
from app.modeling.serving import SERVED_WEIGHT
from app.modeling.simulation import _asof_sizes, _blend, _observed_runs, simulate_slate

log = get_logger(__name__)

#: Slice types for the served figure carry this prefix; the component's do not.
SERVED_SLICE_PREFIX = "served_"


@dataclass(frozen=True, slots=True)
class ServedEvaluation:
    """The served probability for every walk-forward game, and how it scored."""

    #: The walk-forward frame plus ``served_prob``, ``sim_prob`` (NaN where the
    #: simulation could not be formed) and ``served_blended``.
    frame: pd.DataFrame
    weight: float
    run_model: str
    simulations: int
    dispersion: dict[str, Any]
    metrics: Metrics

    @property
    def n_games(self) -> int:
        return int(len(self.frame))

    @property
    def n_blended(self) -> int:
        return int(self.frame["served_blended"].sum())

    @property
    def n_logistic_only(self) -> int:
        return self.n_games - self.n_blended

    def as_served(self) -> pd.DataFrame:
        """The frame with ``prob`` set to the served figure, for the slicers."""
        return self.frame.assign(prob=self.frame["served_prob"])

    def to_config(self) -> dict[str, Any]:
        return {
            "available": True,
            "blend_weight": self.weight,
            "run_model": self.run_model,
            "simulations": self.simulations,
            "dispersion": self.dispersion,
            "n_games": self.n_games,
            "n_blended": self.n_blended,
            "n_logistic_only": self.n_logistic_only,
        }

    def summary(self) -> dict[str, Any]:
        return {
            "n_games": self.n_games,
            "n_blended": self.n_blended,
            "n_logistic_only": self.n_logistic_only,
            "log_loss": self.metrics.log_loss,
            "brier_score": self.metrics.brier_score,
            "calibration_error": self.metrics.calibration_error,
            "max_calibration_error": self.metrics.max_calibration_error,
            "accuracy": self.metrics.accuracy,
            "roc_auc": self.metrics.roc_auc,
        }


def evaluate_served(
    store: AsOfStore,
    dataset: Dataset,
    frame: pd.DataFrame,
    *,
    simulations: int = DEFAULT_SIMULATIONS,
    weight: float = SERVED_WEIGHT,
    builder: FeatureBuilder | None = None,
) -> ServedEvaluation:
    """Score what the product would have served for every game in ``frame``.

    ``frame`` is the logistic's walk-forward output (`collect_predictions`);
    its ``prob`` is the calibrated logistic figure and its ``as_of`` the moment
    the features were read.
    """
    if frame.empty:
        raise ValueError("The walk-forward produced no games to serve.")

    labelled = dataset.labelled
    first_scored = pd.Timestamp(frame["official_date"].min()).date()
    train = labelled[labelled["official_date"] < first_scored]
    dispersion = fit_dispersion(_observed_runs(store, train["game_id"].tolist()))
    # Per slate, as serving fits it; the training-side value stands in where a
    # slate's sample is too small.
    size_by_game = _asof_sizes(store, frame, dispersion.size)
    sizes = np.array([v for v in size_by_game.values() if np.isfinite(v)], dtype=float)

    sims = simulate_slate(
        store, builder or FeatureBuilder(store), frame, dispersion.size, simulations,
        models=(SERVED,), size_by_game=size_by_game,
    )
    columns = ["game_id", "sim_prob"]
    if sims.empty:
        sims = pd.DataFrame(columns=columns)
    merged = frame.merge(sims[columns], on="game_id", how="left", validate="one_to_one")

    logistic = merged["prob"].to_numpy(dtype=float)
    simulated = pd.to_numeric(merged["sim_prob"], errors="coerce").to_numpy(dtype=float)
    blended = np.isfinite(simulated)
    served = logistic.copy()
    if blended.any():
        served[blended] = _blend(logistic[blended], simulated[blended], weight)

    merged["served_prob"] = served
    merged["sim_prob"] = simulated
    merged["served_blended"] = blended
    metrics = evaluate(merged["actual"].to_numpy(), served)

    evaluation = ServedEvaluation(
        frame=merged,
        weight=weight,
        run_model=SERVED.name,
        simulations=simulations,
        dispersion={
            "fit": "as-of per slate, training-side fallback",
            "training_side_nb_size": (
                None if not np.isfinite(dispersion.size) else round(dispersion.size, 2)
            ),
            "training_side_team_games": dispersion.n,
            "asof_nb_size_min": None if sizes.size == 0 else round(float(sizes.min()), 2),
            "asof_nb_size_max": None if sizes.size == 0 else round(float(sizes.max()), 2),
            "variance_over_mean": round(dispersion.ratio, 3),
        },
        metrics=metrics,
    )
    log.info(
        "backtest.served",
        n_games=evaluation.n_games,
        n_blended=evaluation.n_blended,
        n_logistic_only=evaluation.n_logistic_only,
        log_loss=metrics.log_loss,
        calibration_error=metrics.calibration_error,
        weight=weight,
        run_model=SERVED.name,
    )
    return evaluation


def reported_slices(
    session: Session, run_id: Any, slice_type: str
) -> list[BacktestResult]:
    """The rows a product readout should show for ``slice_type``.

    The served rows when the run scored the served figure, the component's
    rows when it did not — never a mixture, and never the component's rows
    presented as the served figure's.
    """
    rows = session.scalars(
        select(BacktestResult).where(
            BacktestResult.run_id == run_id,
            BacktestResult.slice_type.in_(
                [SERVED_SLICE_PREFIX + slice_type, slice_type]
            ),
        )
    ).all()
    served = [r for r in rows if r.slice_type == SERVED_SLICE_PREFIX + slice_type]
    return served or [r for r in rows if r.slice_type == slice_type]


__all__ = [
    "SERVED_SLICE_PREFIX",
    "ServedEvaluation",
    "evaluate_served",
    "reported_slices",
]
