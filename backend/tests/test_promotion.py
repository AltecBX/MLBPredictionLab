"""The gate between training a model and serving it.

Registering and activating were one action, which meant a nightly refit could
make a worse configuration the product without anyone noticing. These tests pin
the four outcomes and, more importantly, pin that the two non-activating ones
really do not activate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import pytest

from app.modeling.promotion import (
    HOLD,
    NO_INCUMBENT,
    PROMOTE,
    REFRESH,
    REJECT,
    decide_promotion,
)


@dataclass
class _Version:
    """A ModelVersion, reduced to what the gate reads."""

    version: str = "v3"
    feature_set_version: str = "fs_v1"
    hyperparameters: dict[str, Any] | None = None


@dataclass
class _Dataset:
    feature_set_version: str = "fs_v1"


def _incumbent(C: float | None = 0.01, feature_set: str = "fs_v1") -> _Version:
    return _Version(
        feature_set_version=feature_set,
        hyperparameters=None if C is None else {"C": C},
    )


# --------------------------------------------------------------------------
# The cases that need no comparison
# --------------------------------------------------------------------------


def test_the_first_model_is_served_without_a_comparison():
    decision = decide_promotion(_Dataset(), [], candidate_C=0.01, incumbent=None)
    assert decision.verdict == NO_INCUMBENT
    assert decision.should_activate is True


def test_an_identical_configuration_is_a_refresh_and_activates():
    """Withholding this would freeze the model at the day it was first trained."""
    decision = decide_promotion(
        _Dataset(), [], candidate_C=0.01, incumbent=_incumbent(0.01)
    )
    assert decision.verdict == REFRESH
    assert decision.should_activate is True
    assert "refitted on newer games" in decision.reason


def test_a_refresh_does_not_run_a_walk_forward():
    """It passes no steps, so a comparison would raise rather than pass quietly."""
    decision = decide_promotion(
        _Dataset(), [], candidate_C=0.05, incumbent=_incumbent(0.05)
    )
    assert decision.verdict == REFRESH
    assert decision.n_common_games == 0


def test_a_changed_feature_set_is_not_a_refresh_even_at_the_same_C(monkeypatch):
    """Same C, different columns, is a different model."""
    _stub_walk_forward(monkeypatch, base=0.5, cand=0.5)
    decision = decide_promotion(
        _Dataset(feature_set_version="fs_v5"), [_step()], candidate_C=0.01,
        incumbent=_incumbent(0.01, feature_set="fs_v1"),
    )
    assert decision.verdict != REFRESH


def test_an_incumbent_without_a_recorded_C_holds_rather_than_guessing():
    decision = decide_promotion(
        _Dataset(), [], candidate_C=0.01, incumbent=_incumbent(None)
    )
    assert decision.verdict == HOLD
    assert decision.should_activate is False


# --------------------------------------------------------------------------
# The comparison itself
# --------------------------------------------------------------------------


def _step():
    return object()  # never dereferenced; the walk-forward is stubbed


def _stub_walk_forward(monkeypatch, base: float, cand: float, n: int = 900):
    """Two configurations that predict at fixed, different accuracies."""
    rng = np.random.default_rng(5)
    actual = rng.integers(0, 2, n)

    def probs(strength: float) -> np.ndarray:
        # A probability that leans the right way by `strength`.
        return np.where(actual == 1, 0.5 + strength, 0.5 - strength)

    calls = {"n": 0}

    def fake_run(dataset, steps, C, min_train_rows=None):
        return C

    def fake_collect(C):
        # `decide_promotion` collects the candidate first, then the incumbent.
        calls["n"] += 1
        strength = cand if calls["n"] % 2 == 1 else base
        return pd.DataFrame(
            {
                "game_id": np.arange(n),
                "actual": actual,
                "prob": probs(strength),
            }
        )

    monkeypatch.setattr("app.modeling.promotion.run_walk_forward", fake_run)
    monkeypatch.setattr("app.modeling.promotion.collect_predictions", fake_collect)


def test_a_clearly_better_configuration_is_promoted(monkeypatch):
    _stub_walk_forward(monkeypatch, base=0.02, cand=0.20)
    decision = decide_promotion(
        _Dataset(), [_step()], candidate_C=0.05, incumbent=_incumbent(0.01)
    )
    assert decision.verdict == PROMOTE
    assert decision.should_activate is True
    assert decision.candidate_log_loss < decision.incumbent_log_loss
    assert decision.delta.ci_low > 0


def test_a_clearly_worse_configuration_is_registered_but_not_served(monkeypatch):
    """The failure this gate exists to prevent."""
    _stub_walk_forward(monkeypatch, base=0.20, cand=0.02)
    decision = decide_promotion(
        _Dataset(), [_step()], candidate_C=0.05, incumbent=_incumbent(0.01)
    )
    assert decision.verdict == REJECT
    assert decision.should_activate is False
    assert decision.delta.ci_high < 0


def test_a_tie_holds_the_incumbent(monkeypatch):
    """A tie is not a reason to swap the served model."""
    _stub_walk_forward(monkeypatch, base=0.10, cand=0.10)
    decision = decide_promotion(
        _Dataset(), [_step()], candidate_C=0.05, incumbent=_incumbent(0.01)
    )
    assert decision.verdict == HOLD
    assert decision.should_activate is False
    assert not decision.delta.is_distinguishable_from_zero


def test_the_comparison_pairs_on_the_same_games(monkeypatch):
    _stub_walk_forward(monkeypatch, base=0.05, cand=0.15, n=700)
    decision = decide_promotion(
        _Dataset(), [_step()], candidate_C=0.05, incumbent=_incumbent(0.01)
    )
    assert decision.n_common_games == 700


def test_no_comparable_games_holds_rather_than_activating(monkeypatch):
    monkeypatch.setattr(
        "app.modeling.promotion.run_walk_forward", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "app.modeling.promotion.collect_predictions", lambda _: pd.DataFrame()
    )
    decision = decide_promotion(
        _Dataset(), [_step()], candidate_C=0.05, incumbent=_incumbent(0.01)
    )
    assert decision.verdict == HOLD
    assert decision.should_activate is False


# --------------------------------------------------------------------------
# The record
# --------------------------------------------------------------------------


def test_every_decision_serialises_with_its_evidence(monkeypatch):
    _stub_walk_forward(monkeypatch, base=0.02, cand=0.20)
    payload = decide_promotion(
        _Dataset(), [_step()], candidate_C=0.05, incumbent=_incumbent(0.01)
    ).to_dict()
    assert payload["verdict"] == PROMOTE
    assert payload["incumbent"]["C"] == 0.01
    assert payload["candidate"]["C"] == 0.05
    assert payload["paired_95_ci"]["ci_low"] > 0
    assert payload["n_common_games"] > 0


def test_a_refresh_serialises_without_an_interval():
    payload = decide_promotion(
        _Dataset(), [], candidate_C=0.01, incumbent=_incumbent(0.01)
    ).to_dict()
    assert payload["paired_95_ci"] is None
    assert payload["should_activate"] is True


@pytest.mark.parametrize("verdict_case", [(0.02, 0.20, True), (0.20, 0.02, False)])
def test_should_activate_always_matches_the_verdict(monkeypatch, verdict_case):
    base, cand, expected = verdict_case
    _stub_walk_forward(monkeypatch, base=base, cand=cand)
    decision = decide_promotion(
        _Dataset(), [_step()], candidate_C=0.05, incumbent=_incumbent(0.01)
    )
    assert decision.should_activate is expected
