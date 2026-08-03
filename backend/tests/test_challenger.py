"""The challenger comparison's leakage guarantees, pinned as tests.

Every guarantee here is structural in `app.modeling.challenger` — these tests
exist so a refactor that silently breaks one fails loudly. The two that matter
most: a calibrator applied at step *s* may depend only on steps before *s*,
and the stacked meta-model's prediction for step *s* may depend only on steps
before *s*. Both are asserted by perturbing the future and requiring the
present not to move.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.modeling.challenger import (
    MIN_CALIBRATION_ROWS,
    MIN_META_ROWS,
    causal_calibrate,
    elo_probability,
    stacked_oof,
)


def _synthetic_oof(n_steps: int, rows_per_step: int, seed: int = 7) -> pd.DataFrame:
    """OOF rows with a deliberately miscalibrated raw probability.

    True probability is 0.5 + 0.3 * (x - 0.5); the raw model reports x itself,
    so it is overconfident by construction and a calibrator has real work to do.
    """
    rng = np.random.default_rng(seed)
    n = n_steps * rows_per_step
    x = rng.uniform(0.05, 0.95, size=n)
    true = 0.5 + 0.3 * (x - 0.5)
    return pd.DataFrame(
        {
            "step": np.repeat(np.arange(n_steps), rows_per_step),
            "raw": x,
            "actual": (rng.uniform(size=n) < true).astype(int),
            "season": 2024,
        }
    )


class TestCausalCalibration:
    def test_passthrough_until_enough_prior_rows(self):
        rows = MIN_CALIBRATION_ROWS // 2  # two steps of prior data needed
        frame = _synthetic_oof(n_steps=4, rows_per_step=rows)
        stream = causal_calibrate(frame, "raw")
        # Steps 0 and 1 have 0 and rows prior rows — both below the floor.
        assert stream.method_by_step[0] == "raw"
        assert stream.method_by_step[1] == "raw"
        assert stream.method_by_step[2] in ("sigmoid", "isotonic")
        raw = frame["raw"].to_numpy()
        early = frame["step"] < 2
        assert np.array_equal(stream.calibrated[early], raw[early])
        late = (frame["step"] == 2).to_numpy()
        assert not np.array_equal(stream.calibrated[late], raw[late])

    def test_future_cannot_change_the_present(self):
        frame = _synthetic_oof(n_steps=4, rows_per_step=MIN_CALIBRATION_ROWS)
        stream = causal_calibrate(frame, "raw")

        tampered = frame.copy()
        last = (tampered["step"] == 3).to_numpy()
        tampered.loc[last, "actual"] = 1 - tampered.loc[last, "actual"]
        tampered.loc[last, "raw"] = 1 - tampered.loc[last, "raw"].to_numpy()
        tampered_stream = causal_calibrate(tampered, "raw")

        earlier = (frame["step"] < 3).to_numpy()
        assert np.array_equal(stream.calibrated[earlier], tampered_stream.calibrated[earlier])
        assert stream.method_by_step[:3] == tampered_stream.method_by_step[:3]

    def test_calibration_actually_helps_a_miscalibrated_stream(self):
        frame = _synthetic_oof(n_steps=6, rows_per_step=MIN_CALIBRATION_ROWS)
        stream = causal_calibrate(frame, "raw")
        assert stream.pooled["sigmoid"] < stream.pooled["raw"]


class TestStackedOof:
    def test_no_prediction_before_enough_history(self):
        rows = MIN_META_ROWS // 2
        frame = _synthetic_oof(n_steps=4, rows_per_step=rows)
        frame["cal_a"] = frame["raw"]
        frame["cal_b"] = np.clip(frame["raw"] * 0.9 + 0.05, 0.01, 0.99)
        out, mask, weights = stacked_oof(frame, ("cal_a", "cal_b"))
        # Steps 0 and 1 have 0 and rows prior rows — below MIN_META_ROWS.
        assert not mask[(frame["step"] < 2).to_numpy()].any()
        assert mask[(frame["step"] >= 2).to_numpy()].all()
        assert len(weights) == 2

    def test_future_cannot_change_the_present(self):
        frame = _synthetic_oof(n_steps=4, rows_per_step=MIN_META_ROWS)
        frame["cal_a"] = frame["raw"]
        frame["cal_b"] = np.clip(1 - frame["raw"], 0.01, 0.99)
        out, mask, _ = stacked_oof(frame, ("cal_a", "cal_b"))

        tampered = frame.copy()
        last = (tampered["step"] == 3).to_numpy()
        tampered.loc[last, "actual"] = 1 - tampered.loc[last, "actual"]
        t_out, t_mask, _ = stacked_oof(tampered, ("cal_a", "cal_b"))

        earlier = (frame["step"] < 3).to_numpy() & mask
        assert np.array_equal(out[earlier], t_out[earlier])
        # And the tampered step's own predictions are identical too — the meta
        # model for step 3 was fitted before step 3's labels existed.
        assert np.array_equal(out[last & mask], t_out[last & mask])


class TestEloProbability:
    def test_home_advantage_is_applied(self):
        p = elo_probability(np.array([0.0]))
        assert 0.53 < p[0] < 0.54  # +24 Elo at even ratings

    def test_missing_diff_is_a_shrug_not_a_verdict(self):
        p = elo_probability(np.array([np.nan]))
        assert p[0] == 0.5

    def test_monotone_in_the_rating_gap(self):
        p = elo_probability(np.array([-200.0, 0.0, 200.0]))
        assert p[0] < p[1] < p[2]


class TestChallengerFolds:
    def test_xgb_and_lgbm_fold_protocol(self):
        """Both challengers: search on validation, refit on train, score test.

        The boosters live in the optional ``[ml]`` extra — CI installs only
        ``[dev]`` — so this test runs where the research stack is installed
        and skips honestly where it is not.
        """
        pytest.importorskip("xgboost")
        pytest.importorskip("lightgbm")
        from app.modeling.challenger import _fit_lgbm_fold, _fit_xgb_fold

        rng = np.random.default_rng(11)
        n = 1200
        names = [f"f{i}" for i in range(6)]
        X = rng.normal(size=(n, 6))
        logits = 0.8 * X[:, 0] - 0.5 * X[:, 1]
        y = (rng.uniform(size=n) < 1 / (1 + np.exp(-logits))).astype(int)
        frame = pd.DataFrame(X, columns=names)
        frame["home_win"] = y

        core, validation, test = frame.iloc[:800], frame.iloc[800:1000], frame.iloc[1000:]
        train = frame.iloc[:1000]

        for fit_fold in (_fit_xgb_fold, _fit_lgbm_fold):
            result = fit_fold(core, validation, train, test, names)
            assert len(result.test_raw) == len(test)
            assert np.all((result.test_raw > 0) & (result.test_raw < 1))
            assert result.best_rounds >= 1
            # The same call is deterministic: fixed seed, fixed data.
            again = fit_fold(core, validation, train, test, names)
            assert np.allclose(result.test_raw, again.test_raw)
            assert result.config == again.config
