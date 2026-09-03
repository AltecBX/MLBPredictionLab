"""Does the run model get better when it is told who is pitching and where?

MODELING_PLAN.md, on merging the simulation: *"The run model is deliberately
crude. No park factor, no starter, no bullpen, no weather. Every one of those is
a run-scoring input the feature layer already computes and the run model
currently ignores, which is the clearest remaining lead in this repository."*

This measures three of them. The fourth is not measurable here and the reason is
worth stating rather than omitting: the only weather in this database sits on the
`games` row, whose `knowledge_time` is first pitch plus three and a half hours.
Not one final game is knowable before it starts, so a weather feature at T−3h
would be reading the game it is predicting. The `weather` table is empty and the
registry marks every weather feature `available=False`. Weather stays UNAVAILABLE
until a forecast provider is enabled — which is the product rule working, not a
gap in this comparison.

**The comparison is an ablation, not a demonstration.** Each variant is scored
against the *base run model* on identical games with identical seeds, so the only
thing that varies is the model. A refinement that does not beat the crude version
it refines has not earned its place, however sensible it sounds — and three
feature groups in this repository have already failed exactly that test.

Everything is judged on a paired bootstrap interval, the same standard the
feature-set comparisons and the simulation itself were held to. Four variants are
measured, which is four chances for noise to look like signal, so the reading
says so and the standard for believing any of them is that it holds across both
seasons.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from app.backtest.feature_set_compare import PairedDelta, _paired_bootstrap, _per_game_log_loss
from app.backtest.metrics import evaluate
from app.backtest.walkforward import Step, collect_predictions, run_walk_forward
from app.core.logging import get_logger
from app.features.asof import AsOfStore
from app.features.builder import FeatureBuilder
from app.features.park import ParkFactors
from app.modeling.dataset import Dataset
from app.modeling.run_inputs import BASE, VARIANTS, RunModel
from app.modeling.runs import DEFAULT_SIMULATIONS, fit_dispersion
from app.modeling.simulation import (
    PREREGISTERED_WEIGHT,
    _blend,
    _headline,
    _observed_runs,
    simulate_slate,
)

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class VariantResult:
    """One run-model variant, scored two ways."""

    name: str
    #: The variant alone, as a win probability.
    metrics: dict[str, Any]
    #: Paired interval against the BASE run model. This is the ablation.
    vs_base: PairedDelta | None
    #: The variant blended with the logistic model at the pre-registered weight.
    blended: dict[str, Any]
    #: Paired interval for that blend against the logistic model alone.
    blend_vs_logistic: PairedDelta | None
    verdict: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "alone": self.metrics,
            "vs_base_run_model": None if self.vs_base is None else self.vs_base.to_dict(),
            "blended_at_0.5": self.blended,
            "blend_vs_logistic": (
                None if self.blend_vs_logistic is None else self.blend_vs_logistic.to_dict()
            ),
            "verdict": self.verdict,
        }


@dataclass(frozen=True, slots=True)
class RunModelComparison:
    n_games: int
    dispersion: dict[str, Any]
    coverage: dict[str, Any]
    logistic: dict[str, Any]
    variants: list[VariantResult]
    verdict: str
    reading: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_games": self.n_games,
            "dispersion": self.dispersion,
            "coverage": self.coverage,
            "logistic": self.logistic,
            "variants": [v.to_dict() for v in self.variants],
            "verdict": self.verdict,
            "reading": self.reading,
        }


def compare_run_models(
    store: AsOfStore,
    dataset: Dataset,
    steps: list[Step],
    C: float,
    simulations: int = DEFAULT_SIMULATIONS,
    min_train_rows: int | None = None,
    models: tuple[RunModel, ...] = VARIANTS,
) -> RunModelComparison | None:
    """Score every run-model variant on the same out-of-sample games."""
    predictions = collect_predictions(
        run_walk_forward(dataset, steps, C=C, min_train_rows=min_train_rows)
    )
    if predictions.empty:
        log.warning("run_model.no_predictions")
        return None

    # Dispersion is fitted on the training side only, exactly as the base
    # comparison does it: every game before the first game anyone is scored on.
    labelled = dataset.labelled
    first_scored = pd.Timestamp(predictions["official_date"].min()).date()
    train = labelled[labelled["official_date"] < first_scored]
    dispersion = fit_dispersion(_observed_runs(store, train["game_id"].tolist()))
    log.info(
        "run_model.dispersion",
        ratio=round(dispersion.ratio, 3),
        size=None if not np.isfinite(dispersion.size) else round(dispersion.size, 2),
        n=dispersion.n,
    )

    # Park factors are built from the same as-of team-game frame everything else
    # reads. The object holds all of history; the as-of cut happens per query.
    parks = ParkFactors(store.games, store.team_games)
    builder = FeatureBuilder(store)

    sims = simulate_slate(
        store, builder, predictions, dispersion.size, simulations,
        models=models, parks=parks,
    )
    merged = predictions.merge(sims, on="game_id", how="inner", validate="one_to_one")
    base_column = f"sim_{BASE.name}"
    merged = merged[merged[base_column].notna()]
    if merged.empty:
        log.warning("run_model.no_simulated_games")
        return None

    actual = merged["actual"].to_numpy()
    logistic = merged["prob"].to_numpy()
    base = merged[base_column].to_numpy(dtype=float)
    base_loss = _per_game_log_loss(actual, base)
    logistic_loss = _per_game_log_loss(actual, logistic)

    results: list[VariantResult] = []
    for model in models:
        probs = merged[f"sim_{model.name}"].to_numpy(dtype=float)
        vs_base = (
            None
            if model is BASE
            else _paired_bootstrap(base_loss - _per_game_log_loss(actual, probs))
        )
        blended = _blend(logistic, probs, PREREGISTERED_WEIGHT)
        results.append(
            VariantResult(
                name=model.name,
                metrics=_headline(evaluate(actual, probs)),
                vs_base=vs_base,
                blended=_headline(evaluate(actual, blended)),
                blend_vs_logistic=_paired_bootstrap(
                    logistic_loss - _per_game_log_loss(actual, blended)
                ),
                verdict=_variant_verdict(model, vs_base),
            )
        )

    coverage = {
        "park_measured": _share(merged, "park_measured"),
        "pitching_measured": _share(merged, "pitching_measured"),
        "projected_measured": _share(merged, "projected_measured"),
    }
    verdict, reading = _judge_variants(results, coverage)
    return RunModelComparison(
        n_games=int(len(merged)),
        dispersion={
            "variance_over_mean": round(dispersion.ratio, 3),
            "nb_size": None if not np.isfinite(dispersion.size) else round(dispersion.size, 2),
            "team_games": dispersion.n,
        },
        coverage=coverage,
        logistic=_headline(evaluate(actual, logistic)),
        variants=results,
        verdict=verdict,
        reading=reading,
    )


def _share(frame: pd.DataFrame, column: str) -> float | None:
    if column not in frame:
        return None
    return round(float(frame[column].fillna(False).astype(bool).mean()), 4)


def _variant_verdict(model: RunModel, vs_base: PairedDelta | None) -> str:
    if model is BASE:
        return "INCUMBENT"
    if vs_base is None:
        return "INCONCLUSIVE"
    if not vs_base.is_distinguishable_from_zero:
        return "NO_EFFECT"
    return "IMPROVES" if vs_base.favours_candidate else "REJECT"


def _judge_variants(
    results: list[VariantResult], coverage: dict[str, Any]
) -> tuple[str, str]:
    """One verdict over the whole ablation.

    Deliberately blunt about multiplicity. Every refinement tried is an interval
    drawn, and with k of them the chance that at least one clears a 95% bar by
    luck alone is 1 − 0.95^k — about one in seven for three, nearly one in five
    for four. The count is taken from the variants actually judged rather than
    written down, so adding a variant cannot quietly understate it. A single
    season saying yes is a lead; two seasons saying yes is the finding.
    """
    improved = [r for r in results if r.verdict == "IMPROVES"]
    rejected = [r for r in results if r.verdict == "REJECT"]
    tried = len([r for r in results if r.verdict != "INCUMBENT"])
    by_luck = 1.0 - 0.95 ** tried if tried else 0.0

    unmeasured = [
        name for name, share in coverage.items()
        if share is not None and share < 0.5
    ]
    caveat = (
        f" Coverage is thin for {', '.join(unmeasured)}, so that refinement was "
        f"inert on most games and the interval mostly measures the games it did "
        f"reach."
        if unmeasured
        else ""
    )

    if not improved and not rejected:
        return "NO_EFFECT", (
            "No refinement moved the run model out of its own interval. The park "
            "and the named starter are real effects on runs; this says they are "
            "already inside the team rates the base model uses, which is the same "
            "diagnosis three feature groups received against the win target."
            + caveat
        )
    if rejected and not improved:
        names = ", ".join(r.name for r in rejected)
        return "REJECT", (
            f"The refinements measurably hurt: {names}. The base run model stands."
            + caveat
        )
    names = ", ".join(r.name for r in improved)
    return "IMPROVES", (
        f"Beat the base run model on this season: {names}. {tried} refinements "
        f"were tried, so {tried} intervals were drawn and at least one clearing "
        f"a 95% bar by luck is a {by_luck:.0%} event. Re-measure on a second "
        f"season before believing it." + caveat
    )


__all__ = [
    "RunModelComparison",
    "VariantResult",
    "compare_run_models",
]
