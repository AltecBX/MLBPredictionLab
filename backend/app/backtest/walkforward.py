"""Walk-forward splitting and out-of-sample prediction generation.

Shared by training (for hyperparameter selection) and by the backtest engine,
so both obey exactly the same chronological protocol (BACKTEST_PLAN.md §1).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.core.config import settings
from app.core.logging import get_logger
from app.modeling.calibration import select_method
from app.modeling.dataset import LABEL_COLUMN, Dataset
from app.modeling.logistic import LogisticWinModel

log = get_logger(__name__)

# The fewest validation games a calibrator may be fitted on. A Platt fit has
# two parameters and a validation slice is the last forty-five days of the
# training window; for the first step of a season that slice is the opening
# week, and on 55 games in which the home side happened to win 38% it fitted
# slope 1.93, intercept −0.80, and pulled every prediction for the following
# month toward the visitor — April 2024 walked forward at 0.724 log loss
# against 0.678 uncalibrated. Three hundred games is roughly three weeks of a
# season: enough that the intercept is measured rather than guessed, and
# reached by every step except the first of each season, which is served
# uncalibrated rather than mis-calibrated. Pre-registered.
MIN_CALIBRATION_ROWS = 300


@dataclass(frozen=True, slots=True)
class Step:
    train_start: date
    train_end: date          # inclusive
    validation_start: date   # inclusive, inside the training window
    test_start: date
    test_end: date           # inclusive


def make_steps(
    frame: pd.DataFrame,
    start: date | None = None,
    end: date | None = None,
    step_days: int = 30,
    validation_days: int | None = None,
) -> list[Step]:
    """Expanding-window steps. Test windows are contiguous and never overlap train."""
    if frame.empty:
        return []
    validation_days = validation_days or settings.backtest_validation_days
    dates = pd.Series(sorted(frame["official_date"].unique()))
    first, last = dates.iloc[0], dates.iloc[-1]
    start = start or first
    end = end or last

    steps: list[Step] = []
    cursor = start
    while cursor <= end:
        test_start = cursor
        test_end = min(cursor + timedelta(days=step_days - 1), end)
        train_end = test_start - timedelta(days=1)
        if train_end >= first:
            steps.append(
                Step(
                    train_start=first,
                    train_end=train_end,
                    validation_start=max(first, train_end - timedelta(days=validation_days - 1)),
                    test_start=test_start,
                    test_end=test_end,
                )
            )
        cursor = test_end + timedelta(days=1)
    return steps


@dataclass(slots=True)
class StepResult:
    step: Step
    n_train: int
    n_test: int
    predictions: pd.DataFrame  # game_id, official_date, prob, actual, train_end, n_train
    coefficients: dict[str, float]
    skipped: bool = False
    reason: str | None = None


def run_walk_forward(
    dataset: Dataset,
    steps: list[Step],
    C: float = 0.1,
    min_train_rows: int | None = None,
    feature_names: list[str] | None = None,
    calibration_method: str | None = None,
) -> list[StepResult]:
    """Fit-on-past, predict-forward. Nothing here can produce an in-sample row."""
    min_train_rows = min_train_rows or settings.min_train_rows
    names = feature_names or dataset.feature_names
    frame = dataset.labelled
    results: list[StepResult] = []

    for step in steps:
        train = frame[frame["official_date"] <= step.train_end]
        test = frame[
            (frame["official_date"] >= step.test_start)
            & (frame["official_date"] <= step.test_end)
        ]
        if len(train) < min_train_rows or test.empty:
            results.append(
                StepResult(
                    step=step, n_train=len(train), n_test=len(test),
                    predictions=pd.DataFrame(), coefficients={}, skipped=True,
                    reason=(
                        f"training window has {len(train)} rows, below the "
                        f"{min_train_rows}-row minimum"
                        if len(train) < min_train_rows
                        else "no test games in window"
                    ),
                )
            )
            continue

        # The calibrator is fit on the tail of the training window, which the
        # classifier has seen — so the classifier is refit without it first.
        validation = train[train["official_date"] >= step.validation_start]
        core = train[train["official_date"] < step.validation_start]
        if len(core) < min_train_rows // 2 or len(validation) < MIN_CALIBRATION_ROWS:
            core, validation = train, train.iloc[:0]

        model = LogisticWinModel(feature_names=list(names), C=C)
        model.fit(core, LABEL_COLUMN)
        if not validation.empty:
            method = select_method(len(validation), calibration_method)
            model.fit_calibration(validation, LABEL_COLUMN, method=method)
            # Refit the classifier on the full training window now that the
            # calibration mapping is fixed, so no training data is wasted.
            fitted_calibrator = model.calibrator
            model = LogisticWinModel(feature_names=list(names), C=C)
            model.fit(train, LABEL_COLUMN)
            model.calibrator = fitted_calibrator

        probabilities = model.predict(test)
        results.append(
            StepResult(
                step=step,
                n_train=len(train),
                n_test=len(test),
                predictions=pd.DataFrame(
                    {
                        "game_id": test["game_id"].to_numpy(),
                        "official_date": test["official_date"].to_numpy(),
                        "season": test["season"].to_numpy(),
                        "month": test["month"].to_numpy(),
                        "as_of": test["as_of"].to_numpy(),
                        "prob": probabilities,
                        "actual": test[LABEL_COLUMN].astype(int).to_numpy(),
                        "train_end": step.train_end,
                        "n_train": len(train),
                        "completeness": test["completeness"].to_numpy(),
                        "home_starter_known": test["home_starter_known"].to_numpy(),
                        "away_starter_known": test["away_starter_known"].to_numpy(),
                        "lineup_confirmed": test["lineup_confirmed"].to_numpy(),
                        "starter_quality_index": test["starter_quality_index"].to_numpy(),
                    }
                ),
                coefficients=model.coefficients,
            )
        )
    return results


def collect_predictions(results: list[StepResult]) -> pd.DataFrame:
    frames = [r.predictions for r in results if not r.skipped and not r.predictions.empty]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def importance_stability(results: list[StepResult]) -> dict[str, dict[str, float]]:
    """Rank mean/std of each feature's absolute coefficient across steps."""
    usable = [r for r in results if r.coefficients]
    if len(usable) < 2:
        return {}
    ranks: dict[str, list[float]] = {}
    for result in usable:
        ordered = sorted(
            result.coefficients.items(), key=lambda kv: abs(kv[1]), reverse=True
        )
        for position, (name, _) in enumerate(ordered, start=1):
            ranks.setdefault(name, []).append(float(position))
    return {
        name: {
            "mean_rank": float(np.mean(values)),
            "std_rank": float(np.std(values)),
            "n_steps": len(values),
        }
        for name, values in ranks.items()
    }
