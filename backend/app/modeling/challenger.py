"""XGBoost and LightGBM challengers, per-model calibration selection, and a
stacked meta-model — measured walk-forward against the served logistic model.

This is the Phase 2A comparison protocol made runnable. The earlier
`ensemble-check` measured one boosted model (sklearn HistGradientBoosting) at
one fixed configuration and rejected the blend; this measurement is broader in
exactly the ways that rejection left open:

  * **XGBoost and LightGBM**, each with strong L1 and L2 regularization, row
    and column subsampling, shallow trees, a conservative learning rate, early
    stopping on a chronological validation tail, and a *small* hyperparameter
    search run inside each training fold only. No random cross-validation
    exists anywhere in this file — every split is by date.
  * **Per-model calibration selection.** Platt and isotonic are both fitted
    and the choice is made per model on data strictly before each test
    period, prequentially: the method used for step *s* is whichever scored
    better pooled over steps before *s*.
  * **A stacked ensemble.** A regularized logistic meta-model over the base
    models' out-of-fold probabilities, trained for step *s* only on OOF rows
    from steps strictly before *s* (MODELING_PLAN.md § Stacking, not voting).
    No majority voting, no hand-set weights.

Leakage guarantees, stated once and enforced structurally below:

  * every base model at step *s* is fitted on games dated ≤ train_end < test;
  * every hyperparameter choice at step *s* is scored on the validation tail
    of that step's own training window, never on test;
  * every calibrator applied at step *s* is fitted on OOF rows of steps < *s*;
  * the meta-model at step *s* is fitted on OOF rows of steps < *s*;
  * nothing in this module ever reads `retrieved_at`, only the dataset the
    production feature builder produced under its as-of policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from app.backtest.walkforward import Step
from app.core.config import settings
from app.core.logging import get_logger
from app.features.elo import DEFAULT_HOME_ADVANTAGE
from app.modeling.calibration import fit_calibrator, select_method
from app.modeling.dataset import LABEL_COLUMN, Dataset
from app.modeling.logistic import LogisticWinModel

log = get_logger(__name__)

EPS = 1e-6

# The market's forty-two-year maximum implied favourite probability is ~73.7%
# including vig (MODELING_PLAN.md § The market baseline). A prediction past it
# is claiming knowledge the closing line has never claimed; the rate of such
# predictions is reported as the overconfidence rate.
MARKET_MAX_PROB = 0.737

# Calibration on fewer prior out-of-fold rows than this is passthrough: a
# mapping fitted on a few hundred games corrects less than it distorts.
MIN_CALIBRATION_ROWS = 400
# The meta-model needs enough prior OOF rows to estimate five parameters
# without chasing one month's noise.
MIN_META_ROWS = 800

# --- XGBoost -----------------------------------------------------------------
# Fixed parts are deliberately conservative (shallow, slow, subsampled); the
# searched part is small and searched inside each training fold only.
XGB_FIXED: dict[str, Any] = {
    "objective": "binary:logistic",
    "eval_metric": "logloss",
    "eta": 0.03,
    "subsample": 0.8,
    "colsample_bytree": 0.7,
    "min_child_weight": 40.0,
    "tree_method": "hist",
    "max_bin": 128,
    "nthread": 4,
}
XGB_GRID: list[dict[str, Any]] = [
    {"max_depth": depth, "alpha": l1, "lambda": l2}
    for depth in (2, 3)
    for l1 in (0.5, 2.0)
    for l2 in (2.0, 6.0)
]
XGB_MAX_ROUNDS = 1500
XGB_EARLY_STOP = 50

# --- LightGBM ----------------------------------------------------------------
LGBM_FIXED: dict[str, Any] = {
    "objective": "binary",
    "metric": "binary_logloss",
    "learning_rate": 0.03,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "feature_fraction": 0.7,
    "min_data_in_leaf": 60,
    "max_bin": 127,
    "verbosity": -1,
    "num_threads": 4,
}
LGBM_GRID: list[dict[str, Any]] = [
    {"num_leaves": leaves, "lambda_l1": l1, "lambda_l2": l2}
    for leaves in (4, 8)
    for l1 in (0.5, 2.0)
    for l2 in (2.0, 6.0)
]
LGBM_MAX_ROUNDS = 1500
LGBM_EARLY_STOP = 50


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)
    return np.log(p / (1 - p))


def _expit(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def elo_probability(elo_diff: np.ndarray) -> np.ndarray:
    """The existing Elo model's win probability, from the dataset's own column.

    `elo_diff` is the pre-game rating difference with no home advantage in it
    (features/registry.py); the engine adds the home-advantage constant at
    probability time, so the same constant is added here. Rows where the
    feature is missing get 0.5 — Elo saying nothing, not Elo saying even.
    """
    diff = np.asarray(elo_diff, dtype=float)
    prob = 1.0 / (1.0 + np.power(10.0, -(diff + DEFAULT_HOME_ADVANTAGE) / 400.0))
    return np.where(np.isnan(diff), 0.5, prob)


# --- per-fold challenger fits ------------------------------------------------


@dataclass(slots=True)
class FoldFit:
    """What one challenger produced on one walk-forward step."""

    config: dict[str, Any]
    best_rounds: int
    validation_log_loss: float
    test_raw: np.ndarray


def _fit_xgb_fold(
    core: pd.DataFrame,
    validation: pd.DataFrame,
    train: pd.DataFrame,
    test: pd.DataFrame,
    names: list[str],
) -> FoldFit:
    """Small grid on the fold's own validation tail, then refit on full train.

    The search and the early stopping share the validation tail — both are
    strictly earlier than test. The winning configuration is refit on the full
    training window at its early-stopped round count, so no training data is
    wasted once the choice is made (same shape as the logistic refit protocol).
    """
    import xgboost as xgb

    dcore = xgb.DMatrix(core[names].astype(float), label=core[LABEL_COLUMN].astype(int))
    dval = xgb.DMatrix(
        validation[names].astype(float), label=validation[LABEL_COLUMN].astype(int)
    )
    best: tuple[float, dict[str, Any], int] | None = None
    for config in XGB_GRID:
        params = {**XGB_FIXED, **config, "seed": settings.random_seed}
        booster = xgb.train(
            params,
            dcore,
            num_boost_round=XGB_MAX_ROUNDS,
            evals=[(dval, "val")],
            early_stopping_rounds=XGB_EARLY_STOP,
            verbose_eval=False,
        )
        score = float(booster.best_score)
        rounds = int(booster.best_iteration) + 1
        if best is None or score < best[0]:
            best = (score, config, rounds)

    score, config, rounds = best  # type: ignore[misc]
    params = {**XGB_FIXED, **config, "seed": settings.random_seed}
    dtrain = xgb.DMatrix(train[names].astype(float), label=train[LABEL_COLUMN].astype(int))
    final = xgb.train(params, dtrain, num_boost_round=max(rounds, 10))
    raw = final.predict(xgb.DMatrix(test[names].astype(float)))
    return FoldFit(config=config, best_rounds=rounds, validation_log_loss=score, test_raw=raw)


def _fit_lgbm_fold(
    core: pd.DataFrame,
    validation: pd.DataFrame,
    train: pd.DataFrame,
    test: pd.DataFrame,
    names: list[str],
) -> FoldFit:
    """LightGBM under the identical per-fold protocol as XGBoost."""
    import lightgbm as lgb

    core_set = lgb.Dataset(core[names].astype(float), label=core[LABEL_COLUMN].astype(int))
    val_set = lgb.Dataset(
        validation[names].astype(float),
        label=validation[LABEL_COLUMN].astype(int),
        reference=core_set,
    )
    best: tuple[float, dict[str, Any], int] | None = None
    for config in LGBM_GRID:
        params = {**LGBM_FIXED, **config, "seed": settings.random_seed}
        booster = lgb.train(
            params,
            core_set,
            num_boost_round=LGBM_MAX_ROUNDS,
            valid_sets=[val_set],
            callbacks=[lgb.early_stopping(LGBM_EARLY_STOP, verbose=False)],
        )
        score = float(booster.best_score["valid_0"]["binary_logloss"])
        rounds = int(booster.best_iteration)
        if best is None or score < best[0]:
            best = (score, config, rounds)

    score, config, rounds = best  # type: ignore[misc]
    params = {**LGBM_FIXED, **config, "seed": settings.random_seed}
    train_set = lgb.Dataset(train[names].astype(float), label=train[LABEL_COLUMN].astype(int))
    final = lgb.train(params, train_set, num_boost_round=max(rounds, 10))
    raw = np.asarray(final.predict(test[names].astype(float)), dtype=float)
    return FoldFit(config=config, best_rounds=rounds, validation_log_loss=score, test_raw=raw)


# --- the walk-forward --------------------------------------------------------

RAW_COLUMNS = ("raw_logistic", "raw_xgb", "raw_lgbm", "raw_elo")


def collect_oof(
    dataset: Dataset,
    steps: list[Step],
    C: float,
    min_train_rows: int | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """One pass over the walk-forward, every model on identical games.

    Emits, per test game: the production-protocol calibrated logistic
    probability (the baseline, produced exactly as the shipped walk-forward
    produces it), the *raw* probabilities of all four base models, and the
    metadata the report slices on. Nothing here can emit an in-sample row.
    """
    min_train_rows = min_train_rows or settings.min_train_rows
    names = list(dataset.feature_names)
    frame = dataset.labelled
    rows: list[pd.DataFrame] = []
    fold_info: list[dict[str, Any]] = []

    step_index = 0
    for step in steps:
        train = frame[frame["official_date"] <= step.train_end]
        test = frame[
            (frame["official_date"] >= step.test_start)
            & (frame["official_date"] <= step.test_end)
        ]
        if len(train) < min_train_rows or test.empty:
            continue

        validation = train[train["official_date"] >= step.validation_start]
        core = train[train["official_date"] < step.validation_start]
        if len(core) < min_train_rows // 2 or len(validation) < 50:
            # The tiny-window fallback: hold out the chronological tail of the
            # training window instead, so the challengers always stop early on
            # data that is strictly pre-test rather than skipping the guard.
            cut = max(1, int(len(train) * 0.85))
            core, validation = train.iloc[:cut], train.iloc[cut:]

        # Baseline logistic, produced by the production protocol: fit on core,
        # calibrate on validation, refit on the full window with the mapping
        # fixed (identical to app.backtest.walkforward.run_walk_forward).
        linear = LogisticWinModel(feature_names=names, C=C)
        linear.fit(core, LABEL_COLUMN)
        method = select_method(len(validation))
        linear.fit_calibration(validation, LABEL_COLUMN, method=method)
        kept = linear.calibrator
        linear = LogisticWinModel(feature_names=names, C=C)
        linear.fit(train, LABEL_COLUMN)
        linear.calibrator = kept

        xgb_fit = _fit_xgb_fold(core, validation, train, test, names)
        lgbm_fit = _fit_lgbm_fold(core, validation, train, test, names)

        rows.append(
            pd.DataFrame(
                {
                    "game_id": test["game_id"].to_numpy(),
                    "official_date": test["official_date"].to_numpy(),
                    "season": test["season"].to_numpy(),
                    "month": test["month"].to_numpy(),
                    "step": step_index,
                    "actual": test[LABEL_COLUMN].astype(int).to_numpy(),
                    "p_logistic": linear.predict(test),
                    "raw_logistic": linear.predict_raw(test),
                    "raw_xgb": xgb_fit.test_raw,
                    "raw_lgbm": lgbm_fit.test_raw,
                    "raw_elo": elo_probability(test["elo_diff"].to_numpy()),
                    "completeness": test["completeness"].to_numpy(),
                    "home_starter_known": test["home_starter_known"].to_numpy(),
                    "away_starter_known": test["away_starter_known"].to_numpy(),
                    "lineup_confirmed": test["lineup_confirmed"].to_numpy(),
                    "starter_quality_index": test["starter_quality_index"].to_numpy(),
                }
            )
        )
        fold_info.append(
            {
                "train_end": str(step.train_end),
                "n_train": len(train),
                "n_test": len(test),
                "xgb": {**xgb_fit.config, "rounds": xgb_fit.best_rounds},
                "lgbm": {**lgbm_fit.config, "rounds": lgbm_fit.best_rounds},
            }
        )
        log.info(
            "challenger.step",
            train_end=str(step.train_end),
            n_train=len(train),
            n_test=len(test),
            xgb=xgb_fit.config,
            xgb_rounds=xgb_fit.best_rounds,
            lgbm=lgbm_fit.config,
            lgbm_rounds=lgbm_fit.best_rounds,
        )
        step_index += 1

    oof = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    return oof, {"folds": fold_info}


# --- causal calibration ------------------------------------------------------


@dataclass(slots=True)
class CalibratedStream:
    """A model's OOF probabilities after prequential calibration."""

    calibrated: np.ndarray
    method_by_step: list[str]
    selected_method: str
    pooled: dict[str, float | None]  # log loss of each candidate stream, pooled


def causal_calibrate(oof: pd.DataFrame, raw_column: str) -> CalibratedStream:
    """Calibrate one model's OOF stream using only strictly-earlier OOF rows.

    For step *s*, both a Platt and an isotonic calibrator are fitted on the raw
    OOF probabilities and outcomes of steps < *s*. The method *used* at step
    *s* is whichever candidate stream scored the better pooled log loss over
    those same earlier steps (Brier as the tiebreak) — a selection that at
    every moment has seen only the past. Before enough history exists, the raw
    probability passes through unchanged and is labelled as such.
    """
    y_all = oof["actual"].to_numpy()
    raw_all = oof[raw_column].to_numpy(dtype=float)
    steps = np.sort(oof["step"].unique())

    sigmoid_stream = raw_all.copy()
    isotonic_stream = raw_all.copy()
    chosen = raw_all.copy()
    method_by_step: list[str] = []

    for s in steps:
        current = (oof["step"] == s).to_numpy()
        prior = (oof["step"] < s).to_numpy()
        n_prior = int(prior.sum())
        if n_prior < MIN_CALIBRATION_ROWS:
            method_by_step.append("raw")
            continue

        sig = fit_calibrator(raw_all[prior], y_all[prior], method="sigmoid")
        iso = fit_calibrator(raw_all[prior], y_all[prior], method="isotonic")
        sigmoid_stream[current] = sig.transform(raw_all[current])
        isotonic_stream[current] = iso.transform(raw_all[current])

        sig_ll = _pooled_log_loss(y_all[prior], sigmoid_stream[prior])
        iso_ll = _pooled_log_loss(y_all[prior], isotonic_stream[prior])
        if iso_ll + 1e-9 < sig_ll:
            method_by_step.append("isotonic")
            chosen[current] = isotonic_stream[current]
        else:
            # Ties go to Platt: fewer parameters (calibration.py's own rule).
            method_by_step.append("sigmoid")
            chosen[current] = sigmoid_stream[current]

    pooled = {
        "raw": _pooled_log_loss(y_all, raw_all),
        "sigmoid": _pooled_log_loss(y_all, sigmoid_stream),
        "isotonic": _pooled_log_loss(y_all, isotonic_stream),
        "raw_brier": float(np.mean((raw_all - y_all) ** 2)),
        "sigmoid_brier": float(np.mean((sigmoid_stream - y_all) ** 2)),
        "isotonic_brier": float(np.mean((isotonic_stream - y_all) ** 2)),
    }
    real = [m for m in method_by_step if m != "raw"]
    selected = real[-1] if real else "raw"
    return CalibratedStream(
        calibrated=chosen,
        method_by_step=method_by_step,
        selected_method=selected,
        pooled=pooled,
    )


def _pooled_log_loss(y: np.ndarray, p: np.ndarray) -> float:
    clipped = np.clip(p, EPS, 1 - EPS)
    return float(-np.mean(y * np.log(clipped) + (1 - y) * np.log(1 - clipped)))


# --- the stack ---------------------------------------------------------------


def stacked_oof(
    oof: pd.DataFrame,
    input_columns: tuple[str, ...],
    C: float = 1.0,
) -> tuple[np.ndarray, np.ndarray, list[dict[str, float]]]:
    """Regularized logistic meta-model over prior-step OOF probabilities.

    For step *s* the meta-model is fitted on OOF rows of steps < *s* only;
    its inputs are the base models' calibrated probabilities in log-odds.
    Returns the meta probabilities (NaN where too little prior history
    existed), a mask of rows where the stack produced a prediction, and the
    per-step coefficient trajectory — the evidence for whether the stack
    leans on one unstable component.
    """
    from sklearn.linear_model import LogisticRegression

    y_all = oof["actual"].to_numpy()
    X_all = np.column_stack([_logit(oof[c].to_numpy(dtype=float)) for c in input_columns])
    steps = np.sort(oof["step"].unique())

    out = np.full(len(oof), np.nan)
    weights: list[dict[str, float]] = []
    for s in steps:
        current = (oof["step"] == s).to_numpy()
        prior = (oof["step"] < s).to_numpy()
        if int(prior.sum()) < MIN_META_ROWS:
            continue
        meta = LogisticRegression(
            C=C, penalty="l2", solver="lbfgs", max_iter=2000,
            random_state=settings.random_seed,
        )
        meta.fit(X_all[prior], y_all[prior])
        out[current] = meta.predict_proba(X_all[current])[:, 1]
        weights.append(
            {
                "step": int(s),
                **{
                    name: float(coef)
                    for name, coef in zip(input_columns, meta.coef_[0], strict=True)
                },
                "intercept": float(meta.intercept_[0]),
            }
        )
    mask = ~np.isnan(out)
    return out, mask, weights


__all__ = [
    "LGBM_GRID",
    "MARKET_MAX_PROB",
    "RAW_COLUMNS",
    "XGB_GRID",
    "CalibratedStream",
    "causal_calibrate",
    "collect_oof",
    "elo_probability",
    "stacked_oof",
]
