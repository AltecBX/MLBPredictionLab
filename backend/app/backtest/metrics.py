"""Evaluation metrics.

Priority order is log loss, Brier score and calibration error; accuracy is
reported but never optimized (MODELING_PLAN.md §2).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np

EPS = 1e-15
ALWAYS_FIFTY_LOG_LOSS = math.log(2)  # 0.6931…

# Bins used for calibration reporting (BACKTEST_PLAN.md §5).
CALIBRATION_BINS = 10
MIN_BIN_FOR_MCE = 30
MIN_SLICE_N = 30


@dataclass(frozen=True, slots=True)
class CalibrationBin:
    lower: float
    upper: float
    n: int
    mean_predicted: float | None
    observed_frequency: float | None
    wilson_low: float | None
    wilson_high: float | None


@dataclass(slots=True)
class Metrics:
    n: int
    accuracy: float | None = None
    log_loss: float | None = None
    brier_score: float | None = None
    calibration_error: float | None = None
    max_calibration_error: float | None = None
    roc_auc: float | None = None
    baseline_log_loss: float = ALWAYS_FIFTY_LOG_LOSS
    log_loss_improvement: float | None = None
    mean_predicted: float | None = None
    observed_rate: float | None = None
    bins: list[CalibrationBin] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["bins"] = [asdict(b) for b in self.bins]
        return data


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval — honest about small bins."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def log_loss(y: np.ndarray, p: np.ndarray) -> float:
    p = np.clip(p, EPS, 1 - EPS)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def brier_score(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def accuracy(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((p > 0.5).astype(int) == y))


def roc_auc(y: np.ndarray, p: np.ndarray) -> float | None:
    """Rank-based AUC. Returns None when only one class is present."""
    positives, negatives = int(np.sum(y == 1)), int(np.sum(y == 0))
    if positives == 0 or negatives == 0:
        return None
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p), dtype=float)
    sorted_p = p[order]
    i = 0
    while i < len(sorted_p):
        j = i
        while j + 1 < len(sorted_p) and sorted_p[j + 1] == sorted_p[i]:
            j += 1
        average_rank = (i + j) / 2.0 + 1.0
        ranks[order[i : j + 1]] = average_rank
        i = j + 1
    rank_sum = float(np.sum(ranks[y == 1]))
    return (rank_sum - positives * (positives + 1) / 2.0) / (positives * negatives)


def calibration_bins(
    y: np.ndarray, p: np.ndarray, n_bins: int = CALIBRATION_BINS
) -> list[CalibrationBin]:
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    out: list[CalibrationBin] = []
    for i in range(n_bins):
        lower, upper = float(edges[i]), float(edges[i + 1])
        mask = (p >= lower) & (p < upper) if i < n_bins - 1 else (p >= lower) & (p <= upper)
        count = int(np.sum(mask))
        if count == 0:
            out.append(CalibrationBin(lower, upper, 0, None, None, None, None))
            continue
        successes = int(np.sum(y[mask]))
        low, high = wilson_interval(successes, count)
        out.append(
            CalibrationBin(
                lower=lower,
                upper=upper,
                n=count,
                mean_predicted=float(np.mean(p[mask])),
                observed_frequency=successes / count,
                wilson_low=low,
                wilson_high=high,
            )
        )
    return out


def expected_calibration_error(bins: list[CalibrationBin], total: int) -> float | None:
    if total == 0:
        return None
    error = 0.0
    for b in bins:
        if b.n == 0 or b.mean_predicted is None or b.observed_frequency is None:
            continue
        error += (b.n / total) * abs(b.observed_frequency - b.mean_predicted)
    return error


def max_calibration_error(bins: list[CalibrationBin]) -> float | None:
    values = [
        abs(b.observed_frequency - b.mean_predicted)
        for b in bins
        if b.n >= MIN_BIN_FOR_MCE and b.mean_predicted is not None
        and b.observed_frequency is not None
    ]
    return max(values) if values else None


def evaluate(y_true, y_prob, *, n_bins: int = CALIBRATION_BINS) -> Metrics:
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(y_prob, dtype=float)
    n = int(len(y))
    if n == 0:
        return Metrics(n=0)

    # A slice too small to say anything about reports its count only. A
    # calibration error computed on a dozen games is noise, not evidence.
    if n < MIN_SLICE_N:
        return Metrics(
            n=n,
            mean_predicted=float(np.mean(p)),
            observed_rate=float(np.mean(y)),
        )

    bins = calibration_bins(y, p, n_bins)
    ll = log_loss(y, p)
    return Metrics(
        n=n,
        accuracy=accuracy(y, p),
        log_loss=ll,
        brier_score=brier_score(y, p),
        calibration_error=expected_calibration_error(bins, n),
        max_calibration_error=max_calibration_error(bins),
        roc_auc=roc_auc(y, p),
        log_loss_improvement=ALWAYS_FIFTY_LOG_LOSS - ll,
        mean_predicted=float(np.mean(p)),
        observed_rate=float(np.mean(y)),
        bins=bins,
    )
