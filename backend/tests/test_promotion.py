"""The gate between training a model and serving it.

Registering and activating were one action, which meant a nightly refit could
make a worse configuration the product without anyone noticing. These tests pin
the four outcomes and, more importantly, pin that the two non-activating ones
really do not activate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from app.backtest.walkforward import make_steps
from app.features.registry import feature_keys
from app.modeling.dataset import Dataset
from app.modeling.promotion import (
    HOLD,
    NO_INCUMBENT,
    PROMOTE,
    REFRESH,
    REJECT,
    decide_promotion,
    incumbent_columns,
)
from app.modeling.train import incumbent_matrix


@dataclass
class _Version:
    """A ModelVersion, reduced to what the gate reads."""

    version: str = "v3"
    feature_set_version: str = "fs_v1"
    hyperparameters: dict[str, Any] | None = None
    feature_names: list[str] | None = None


@dataclass
class _Dataset:
    feature_set_version: str = "fs_v1"
    feature_names: list[str] = field(default_factory=lambda: ["a", "b"])
    frame: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["a", "b"]))


def _incumbent(
    C: float | None = 0.01,
    feature_set: str = "fs_v1",
    feature_names: list[str] | None = None,
) -> _Version:
    return _Version(
        feature_set_version=feature_set,
        hyperparameters=None if C is None else {"C": C},
        feature_names=feature_names,
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
        _Dataset(feature_set_version="fs_v5", feature_names=["a", "b", "c"]),
        [_step()],
        candidate_C=0.01,
        incumbent=_incumbent(0.01, feature_set="fs_v1", feature_names=["a", "b"]),
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

    calls: dict[str, Any] = {"n": 0, "runs": []}

    def fake_run(dataset, steps, C, min_train_rows=None, feature_names=None):
        calls["runs"].append(
            {"dataset": dataset, "C": C, "feature_names": feature_names}
        )
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
    return calls


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


def test_a_tie_on_C_alone_refreshes_at_the_incumbents_C(monkeypatch):
    """The regularisation choice is immaterial; the newer games are not.

    Holding here would freeze the model at the last day the grid happened to
    agree with the active C. Production did exactly that on the first retrain
    after a data correction: grid 0.001, active 0.003, a tie, HOLD, and the
    model fitted on the old features stayed served.
    """
    _stub_walk_forward(monkeypatch, base=0.10, cand=0.10)
    decision = decide_promotion(
        _Dataset(), [_step()], candidate_C=0.05, incumbent=_incumbent(0.01)
    )
    assert decision.verdict == REFRESH
    assert decision.should_activate is True
    assert decision.fit_C == 0.01
    assert not decision.delta.is_distinguishable_from_zero
    assert decision.to_dict()["fit_C"] == 0.01


def test_a_tie_between_feature_sets_holds_the_incumbent(monkeypatch):
    """A tie is not a reason to swap the served model's columns."""
    _stub_walk_forward(monkeypatch, base=0.10, cand=0.10)
    decision = decide_promotion(
        _Dataset(feature_set_version="fs_v9", feature_names=["a", "b", "c"],
                 frame=pd.DataFrame(columns=["a", "b", "c"])),
        [_step()],
        candidate_C=0.01,
        incumbent=_incumbent(0.01, feature_set="fs_v1", feature_names=["a", "b"]),
    )
    assert decision.verdict == HOLD
    assert decision.should_activate is False
    assert decision.fit_C is None


def test_a_promotion_or_rejection_fits_the_candidates_C(monkeypatch):
    _stub_walk_forward(monkeypatch, base=0.02, cand=0.20)
    promoted = decide_promotion(
        _Dataset(), [_step()], candidate_C=0.05, incumbent=_incumbent(0.01)
    )
    assert promoted.verdict == PROMOTE and promoted.fit_C is None


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


def test_train_refits_at_the_incumbents_C_on_a_tie(monkeypatch):
    """`train_model` fits and registers the C the decision says, not the grid's."""
    import app.modeling.train as train_module
    from app.modeling.promotion import PromotionDecision

    fitted: dict[str, Any] = {}
    fake_dataset = SimpleNamespace(
        frame=pd.DataFrame({"x": [1.0]}), feature_set_version="fs_v9",
        labelled=pd.DataFrame({"official_date": [date(2025, 6, 1)], "x": [1.0]}),
        feature_names=["x"], as_of_policy="T_MINUS_3H",
    )
    monkeypatch.setattr(train_module.AsOfStore, "load", staticmethod(lambda session, seasons: None))
    monkeypatch.setattr(train_module, "build_dataset", lambda *a, **k: fake_dataset)
    monkeypatch.setattr(train_module, "select_hyperparameters", lambda *a, **k: (0.001, {}))
    monkeypatch.setattr(train_module, "make_steps", lambda *a, **k: [_step()])
    monkeypatch.setattr(train_module, "run_walk_forward", lambda *a, **k: [])
    monkeypatch.setattr(train_module, "collect_predictions", lambda results: pd.DataFrame())
    monkeypatch.setattr(train_module, "get_active_version", lambda *a, **k: _incumbent(0.003, "fs_v9"))
    monkeypatch.setattr(
        train_module, "decide_promotion",
        lambda *a, **k: PromotionDecision(verdict=REFRESH, should_activate=True, reason="tie", fit_C=0.003),
    )
    monkeypatch.setattr(train_module, "incumbent_matrix", lambda *a, **k: None)

    def fake_fit(dataset, C):
        fitted["C"] = C
        return SimpleNamespace(train_rows=1, calibrator=None, feature_names=["x"])

    monkeypatch.setattr(train_module, "fit_final_model", fake_fit)
    monkeypatch.setattr(train_module, "next_version", lambda *a, **k: "v9")

    def fake_register(session, model, **kwargs):
        fitted["registered_C"] = kwargs["hyperparameters"]["C"]
        fitted["selected_C"] = kwargs["hyperparameters"]["selected_C"]
        fitted["notes"] = kwargs["notes"]
        fitted["activate"] = kwargs["activate"]
        return SimpleNamespace(id=1)

    monkeypatch.setattr(train_module, "register_model", fake_register)

    class _Run:
        rows_written = 0

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(train_module, "job_run", lambda *a, **k: _Run())
    summary = train_module.train_model(object(), activate=True)
    assert fitted["C"] == 0.003
    assert fitted["registered_C"] == 0.003
    # The grid's own pick is kept beside the fitted C, and the note says both.
    assert fitted["selected_C"] == 0.001
    assert "selected C=0.001" in fitted["notes"] and "fitted at the active model's C=0.003" in fitted["notes"]
    assert fitted["activate"] is True
    assert summary["C"] == 0.003
    assert summary["selected_C"] == 0.001
    assert summary["activated"] is True


@pytest.mark.parametrize("verdict_case", [(0.02, 0.20, True), (0.20, 0.02, False)])
def test_should_activate_always_matches_the_verdict(monkeypatch, verdict_case):
    base, cand, expected = verdict_case
    _stub_walk_forward(monkeypatch, base=base, cand=cand)
    decision = decide_promotion(
        _Dataset(), [_step()], candidate_C=0.05, incumbent=_incumbent(0.01)
    )
    assert decision.should_activate is expected


# --------------------------------------------------------------------------
# A changed feature set: the incumbent is scored on its own columns
# --------------------------------------------------------------------------
#
# The gate used to run both arms on the candidate's matrix. When only the
# feature set changed and the grid picked the same C for both, that compared a
# model with itself: a delta of exactly zero, a HOLD, and a feature set that
# could never reach the product. These pin the repair.


def test_a_changed_feature_set_scores_the_incumbent_on_its_registered_columns(monkeypatch):
    calls = _stub_walk_forward(monkeypatch, base=0.05, cand=0.15)
    candidate = _Dataset(
        feature_set_version="fs_v9",
        feature_names=["a", "b", "c"],
        frame=pd.DataFrame(columns=["a", "b", "c"]),
    )
    decide_promotion(
        candidate, [_step()], candidate_C=0.01,
        incumbent=_incumbent(0.01, feature_set="fs_v1", feature_names=["a", "b"]),
    )
    candidate_run, incumbent_run = calls["runs"]
    assert candidate_run["feature_names"] is None  # the matrix's own columns
    assert candidate_run["dataset"] is candidate
    assert incumbent_run["feature_names"] == ["a", "b"]
    assert incumbent_run["dataset"] is candidate  # same rows: no second build
    assert incumbent_run["C"] == 0.01


def test_a_same_set_comparison_does_not_restrict_columns(monkeypatch):
    calls = _stub_walk_forward(monkeypatch, base=0.05, cand=0.15)
    decide_promotion(
        _Dataset(), [_step()], candidate_C=0.05,
        incumbent=_incumbent(0.01, feature_names=["a", "b"]),
    )
    assert [run["feature_names"] for run in calls["runs"]] == [None, None]


def _synthetic(n: int = 2400, seed: int = 11) -> Dataset:
    """Games whose outcome the column `c` explains and `a` barely does.

    The incumbent set is `a` alone; the candidate adds `c`. With a real
    walk-forward this is the case the old gate could not see.
    """
    rng = np.random.default_rng(seed)
    a = rng.normal(size=n)
    c = rng.normal(size=n)
    logit = 0.15 * a + 1.4 * c
    home_win = (rng.uniform(size=n) < 1.0 / (1.0 + np.exp(-logit))).astype(float)
    first = date(2025, 4, 1)
    days = np.sort(rng.integers(0, 360, size=n))
    dates = [first + timedelta(days=int(d)) for d in days]
    frame = pd.DataFrame(
        {
            "game_id": np.arange(n),
            "as_of": pd.to_datetime(dates, utc=True),
            "official_date": dates,
            "season": [d.year for d in dates],
            "month": [d.month for d in dates],
            "home_team_id": rng.integers(100, 130, size=n),
            "away_team_id": rng.integers(100, 130, size=n),
            "completeness": 1.0,
            "n_missing": 0,
            "home_starter_known": True,
            "away_starter_known": True,
            "lineup_confirmed": False,
            "starter_quality_index": None,
            "home_win": home_win,
            "a": a,
            "c": c,
        }
    )
    return Dataset(frame, ["a", "c"], "fs_v9", "T_MINUS_3H")


def test_a_feature_set_change_at_the_same_C_is_measured_not_tied():
    """Before the repair this returned HOLD with a delta of exactly zero."""
    dataset = _synthetic()
    steps = make_steps(dataset.labelled, step_days=30, validation_days=20)
    decision = decide_promotion(
        dataset,
        steps,
        candidate_C=0.01,
        incumbent=_incumbent(0.01, feature_set="fs_v1", feature_names=["a"]),
        min_train_rows=200,
    )
    assert decision.verdict == PROMOTE
    assert decision.should_activate is True
    assert decision.incumbent_n_features == 1
    assert decision.candidate_n_features == 2
    assert decision.candidate_log_loss < decision.incumbent_log_loss
    assert decision.delta.ci_low > 0
    # The incumbent arm, scored on `a` alone, is the near-coin-flip it should
    # be; scored on the candidate's full matrix it would have matched the
    # candidate to the digit.
    assert decision.incumbent_log_loss > 0.66


def test_an_incumbent_column_the_candidate_lacks_holds_and_names_it(monkeypatch):
    _stub_walk_forward(monkeypatch, base=0.05, cand=0.15)
    decision = decide_promotion(
        _Dataset(
            feature_set_version="fs_v9",
            feature_names=["a", "b", "c"],
            frame=pd.DataFrame(columns=["a", "b", "c"]),
        ),
        [_step()],
        candidate_C=0.05,
        incumbent=_incumbent(0.01, feature_set="fs_v1", feature_names=["a", "z"]),
    )
    assert decision.verdict == HOLD
    assert decision.should_activate is False
    assert "z" in decision.reason
    assert decision.n_common_games == 0


def test_the_incumbents_own_matrix_is_used_when_supplied(monkeypatch):
    calls = _stub_walk_forward(monkeypatch, base=0.05, cand=0.15)
    incumbent_matrix_ = _Dataset(
        feature_set_version="fs_v1",
        feature_names=["a", "z"],
        frame=pd.DataFrame(columns=["a", "z"]),
    )
    decision = decide_promotion(
        _Dataset(
            feature_set_version="fs_v9",
            feature_names=["a", "b", "c"],
            frame=pd.DataFrame(columns=["a", "b", "c"]),
        ),
        [_step()],
        candidate_C=0.05,
        incumbent=_incumbent(0.01, feature_set="fs_v1", feature_names=["a", "z"]),
        incumbent_dataset=incumbent_matrix_,
    )
    assert decision.verdict == PROMOTE
    assert calls["runs"][1]["dataset"] is incumbent_matrix_
    assert calls["runs"][1]["feature_names"] == ["a", "z"]


def test_an_incumbent_without_a_feature_list_falls_back_to_its_registered_set():
    assert incumbent_columns(_incumbent(0.01, feature_set="fs_v1")) == feature_keys("fs_v1")
    assert incumbent_columns(_incumbent(0.01, feature_names=["a"])) == ["a"]
    assert incumbent_columns(_incumbent(0.01, feature_set="fs_v0_retired")) is None


def test_a_retired_feature_set_with_no_feature_list_holds(monkeypatch):
    _stub_walk_forward(monkeypatch, base=0.05, cand=0.15)
    decision = decide_promotion(
        _Dataset(feature_set_version="fs_v9"),
        [_step()],
        candidate_C=0.05,
        incumbent=_incumbent(0.01, feature_set="fs_v0_retired"),
    )
    assert decision.verdict == HOLD
    assert "fs_v0_retired" in decision.reason


# --------------------------------------------------------------------------
# Training builds the incumbent's matrix only when the gate cannot do without
# --------------------------------------------------------------------------


def _fake_build(calls: list[dict[str, Any]]):
    def build(session, seasons=None, store=None, feature_set_version=None, **_):
        calls.append({"seasons": seasons, "store": store, "feature_set_version": feature_set_version})
        return _Dataset(feature_set_version=feature_set_version or "fs_v1")

    return build


def test_no_incumbent_or_same_set_needs_no_second_matrix(monkeypatch):
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr("app.modeling.train.build_dataset", _fake_build(calls))
    candidate = _Dataset(feature_set_version="fs_v9", feature_names=["a", "b", "c"])
    assert incumbent_matrix(None, candidate, None, None, None) is None
    assert incumbent_matrix(None, candidate, _incumbent(0.01, "fs_v9"), None, None) is None
    assert calls == []


def test_a_subset_incumbent_needs_no_second_matrix(monkeypatch):
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr("app.modeling.train.build_dataset", _fake_build(calls))
    candidate = _Dataset(
        feature_set_version="fs_v9",
        feature_names=["a", "b", "c"],
        frame=pd.DataFrame(columns=["a", "b", "c"]),
    )
    incumbent = _incumbent(0.01, feature_set="fs_v1", feature_names=["a", "b"])
    assert incumbent_matrix(None, candidate, incumbent, [2025], "store") is None
    assert calls == []


def test_a_missing_incumbent_column_builds_its_matrix_on_the_same_store(monkeypatch):
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr("app.modeling.train.build_dataset", _fake_build(calls))
    candidate = _Dataset(
        feature_set_version="fs_v9",
        feature_names=["a", "b", "c"],
        frame=pd.DataFrame(columns=["a", "b", "c"]),
    )
    incumbent = _incumbent(0.01, feature_set="fs_v1", feature_names=["a", "z"])
    built = incumbent_matrix(None, candidate, incumbent, [2025, 2026], "the-store")
    assert built is not None and built.feature_set_version == "fs_v1"
    assert calls == [
        {"seasons": [2025, 2026], "store": "the-store", "feature_set_version": "fs_v1"}
    ]


def test_a_retired_incumbent_set_is_left_to_the_gate(monkeypatch):
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr("app.modeling.train.build_dataset", _fake_build(calls))
    candidate = _Dataset(feature_set_version="fs_v9", feature_names=["a", "b", "c"])
    incumbent = _incumbent(0.01, feature_set="fs_v0_retired", feature_names=["a", "z"])
    assert incumbent_matrix(None, candidate, incumbent, None, None) is None
    assert calls == []
