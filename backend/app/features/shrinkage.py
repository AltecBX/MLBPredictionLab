"""Shrinkage toward stabilized baselines (FEATURE_DICTIONARY.md §1).

    shrunk = (observed_events + prior_rate * k) / (observed_denominator + k)

``k`` is the denominator at which the observed rate carries equal weight with
the prior. A value computed with a denominator below its minimum sample is not
dropped — it is shrunk harder and flagged as estimated, and its sample size
travels with it to the UI.
"""

from __future__ import annotations

from dataclasses import dataclass

# Stabilization constants (FEATURE_DICTIONARY.md §1).
K_BATTER_K_PCT = 60
K_BATTER_BB_PCT = 120
K_BATTER_WOBA = 300
K_BATTER_ISO = 320
K_PITCHER_K_PCT = 70
K_PITCHER_BB_PCT = 170
K_PITCHER_GB_PCT = 70
K_PITCHER_HR_FB = 320
K_TEAM_RUNS_PER_GAME = 25
K_H2H = 40

# Minimum samples below which a value is flagged estimated.
MIN_STARTS = 5
MIN_TEAM_GAMES = 10
MIN_RELIEF_APPEARANCES = 20


@dataclass(frozen=True, slots=True)
class FeatureValue:
    """Every feature returns this, never a bare float."""

    value: float | None
    sample_size: int
    is_estimated: bool
    detail: str | None = None

    @classmethod
    def missing(cls, detail: str | None = None) -> FeatureValue:
        return cls(value=None, sample_size=0, is_estimated=True, detail=detail)


def shrink(
    events: float | None,
    denominator: float | None,
    prior_rate: float | None,
    k: float,
    *,
    min_sample: float = 0.0,
) -> FeatureValue:
    """Regress an observed rate toward a prior."""
    if prior_rate is None:
        if not denominator:
            return FeatureValue.missing("no observations and no prior available")
        return FeatureValue(
            value=float(events or 0.0) / float(denominator),
            sample_size=int(denominator),
            is_estimated=denominator < min_sample,
        )
    events = float(events or 0.0)
    denominator = float(denominator or 0.0)
    value = (events + prior_rate * k) / (denominator + k)
    return FeatureValue(
        value=value,
        sample_size=int(denominator),
        is_estimated=denominator < min_sample,
    )


def shrink_mean(
    observed_mean: float | None,
    n: float,
    prior_mean: float | None,
    k: float,
    *,
    min_sample: float = 0.0,
) -> FeatureValue:
    """Regress an observed per-unit mean (e.g. runs per game) toward a prior."""
    if observed_mean is None or n <= 0:
        if prior_mean is None:
            return FeatureValue.missing("no observations and no prior available")
        return FeatureValue(value=prior_mean, sample_size=0, is_estimated=True,
                            detail="league prior only")
    if prior_mean is None:
        return FeatureValue(
            value=float(observed_mean), sample_size=int(n), is_estimated=n < min_sample
        )
    value = (observed_mean * n + prior_mean * k) / (n + k)
    return FeatureValue(value=value, sample_size=int(n), is_estimated=n < min_sample)


def safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return float(numerator) / float(denominator)


def difference(home: FeatureValue, away: FeatureValue) -> FeatureValue:
    """Home-minus-away difference. Missing on either side propagates."""
    if home.value is None or away.value is None:
        return FeatureValue.missing(
            home.detail or away.detail or "one side unavailable"
        )
    return FeatureValue(
        value=home.value - away.value,
        sample_size=min(home.sample_size, away.sample_size),
        is_estimated=home.is_estimated or away.is_estimated,
    )
