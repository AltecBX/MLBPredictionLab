"""The run distribution, the game simulation, and the blend that judges them."""

from __future__ import annotations

import numpy as np
import pytest

from app.modeling.runs import (
    EXTRA_INNING_RUN_RATE,
    fit_dispersion,
    sample_runs,
    simulate_game,
)
from app.modeling.simulation import WEIGHT_GRID, _blend, _judge

# Measured on this repository's own data: 16,314 nine-inning regular-season
# team-games, mean 4.463 runs, variance 10.592.
LEAGUE_MEAN = 4.463
LEAGUE_SIZE = 3.25


# --------------------------------------------------------------------------
# Dispersion
# --------------------------------------------------------------------------


def test_poisson_data_is_not_mistaken_for_overdispersed():
    """The fit must not manufacture a dispersion that is not there.

    Sampling noise leaves the variance a hair above the mean, so the fitted size
    is enormous rather than literally infinite — and a negative binomial with a
    size in the thousands is Poisson for every purpose here. What matters is that
    the ratio is recognised as one and the draws come out Poisson-shaped.
    """
    rng = np.random.default_rng(1)
    fitted = fit_dispersion(rng.poisson(4.5, 40_000))
    assert not fitted.is_overdispersed
    assert fitted.ratio == pytest.approx(1.0, abs=0.02)

    draws = sample_runs(rng, 4.5, fitted.size, 40_000)
    assert draws.var() == pytest.approx(draws.mean(), rel=0.05)


def test_variance_at_or_below_the_mean_falls_back_to_poisson_exactly():
    """The degenerate case: no NB solution exists, and Poisson is the answer."""
    assert not np.isfinite(fit_dispersion(np.array([3.0, 3.0, 3.0, 3.0])).size)


def test_overdispersed_data_is_recognised_as_such():
    rng = np.random.default_rng(1)
    truth = 3.0
    draws = rng.negative_binomial(truth, truth / (truth + 4.5), 60_000)
    fitted = fit_dispersion(draws)
    assert fitted.is_overdispersed
    assert fitted.size == pytest.approx(truth, rel=0.15)


def test_a_sample_too_small_to_fit_reports_poisson_rather_than_failing():
    assert not np.isfinite(fit_dispersion(np.array([3.0])).size)
    assert not np.isfinite(fit_dispersion(np.array([])).size)


def test_sampling_reproduces_the_requested_mean():
    rng = np.random.default_rng(3)
    draws = sample_runs(rng, LEAGUE_MEAN, LEAGUE_SIZE, 80_000)
    assert draws.mean() == pytest.approx(LEAGUE_MEAN, rel=0.02)
    # And is genuinely overdispersed, which is the whole reason for using NB.
    assert draws.var() > draws.mean() * 1.8


def test_an_infinite_size_samples_poisson():
    rng = np.random.default_rng(3)
    draws = sample_runs(rng, LEAGUE_MEAN, float("inf"), 80_000)
    assert draws.mean() == pytest.approx(LEAGUE_MEAN, rel=0.02)
    assert draws.var() == pytest.approx(draws.mean(), rel=0.05)


# --------------------------------------------------------------------------
# The simulation
# --------------------------------------------------------------------------


def test_the_same_inputs_give_the_same_answer():
    """A prediction that changes when nothing changed is not a prediction."""
    a = simulate_game(4.6, 4.4, LEAGUE_SIZE, simulations=8000, seed=42)
    b = simulate_game(4.6, 4.4, LEAGUE_SIZE, simulations=8000, seed=42)
    assert a.to_dict() == b.to_dict()


def test_the_better_side_is_favoured_and_monotonically_so():
    probs = [
        simulate_game(mean, 4.4, LEAGUE_SIZE, simulations=20_000, seed=5).home_win_prob
        for mean in (3.6, 4.0, 4.4, 4.8, 5.2)
    ]
    assert probs == sorted(probs)
    assert probs[0] < 0.45 < 0.55 < probs[-1]


def test_the_conditional_ninth_does_not_cost_the_home_team_runs():
    """The correction that stops a per-game rate being discounted twice.

    `home_mean` is measured from box scores in which the home side already
    skipped the ninth when ahead. Skipping it again in the simulation applied
    that discount a second time and biased home win probability down by about a
    point on every game. The per-inning rate is solved so the input means what it
    says.
    """
    sim = simulate_game(LEAGUE_MEAN, LEAGUE_MEAN, LEAGUE_SIZE, simulations=40_000, seed=9)
    # Both sides land near the input. Home sits fractionally below away because
    # it still forgoes some ninth innings; the gap is a fraction of a run, not
    # the fifth of a run the uncorrected version produced.
    assert sim.mean_away_runs == pytest.approx(LEAGUE_MEAN, abs=0.15)
    assert sim.mean_home_runs == pytest.approx(LEAGUE_MEAN, abs=0.15)
    assert abs(sim.mean_home_runs - sim.mean_away_runs) < 0.1


def test_equal_teams_still_leave_the_home_side_slightly_ahead():
    """The conditional ninth is a real, structural piece of home advantage."""
    sim = simulate_game(LEAGUE_MEAN, LEAGUE_MEAN, LEAGUE_SIZE, simulations=40_000, seed=9)
    assert 0.50 < sim.home_win_prob < 0.55


def test_the_simulation_reproduces_league_rates_it_was_not_fitted_to():
    """Extra innings, one-run games and shutouts are outputs, not inputs.

    Measured on this repository's own games: extra innings 8.6%, one-run games
    28.1%, a shutout by either side 13.0% — so about 6.5% per side. Nothing in
    the simulation was tuned to hit these; they fall out of the run distribution
    and the innings structure, which is what makes them worth asserting.
    """
    sim = simulate_game(LEAGUE_MEAN, LEAGUE_MEAN, LEAGUE_SIZE, simulations=60_000, seed=11)
    assert 0.06 < sim.p_extra_innings < 0.13
    assert 0.24 < sim.p_one_run_game < 0.34
    assert 0.03 < sim.p_home_shutout < 0.10


def test_a_tie_is_always_resolved():
    """No simulated game may end level. The extra-innings loop has to terminate."""
    sim = simulate_game(0.2, 0.2, LEAGUE_SIZE, simulations=20_000, seed=13)
    # Very low-scoring teams tie constantly in regulation, which stresses the
    # loop hardest. Every draw must still produce a winner.
    assert sim.p_extra_innings > 0.3
    assert 0.0 < sim.home_win_prob < 1.0


def test_extra_innings_score_faster_than_regulation():
    """The runner on second is why, and ignoring it mis-times every tiebreak."""
    assert EXTRA_INNING_RUN_RATE > LEAGUE_MEAN / 9


def test_a_blowout_cannot_become_the_modal_score():
    sim = simulate_game(4.5, 4.5, LEAGUE_SIZE, simulations=40_000, seed=17)
    away, home = sim.modal_score
    assert 0 <= away <= 8 and 0 <= home <= 8


# --------------------------------------------------------------------------
# Blending and judgement
# --------------------------------------------------------------------------


def test_blending_happens_in_log_odds_not_probability():
    """Averaging probabilities is shrinkage toward .500 wearing an ensemble's hat.

    Two models that agree a game is a 90% home win must still say 90% after
    blending. A probability average of 0.9 and 0.9 also gives 0.9 — so the case
    that separates the two is disagreement in the tail, where the log-odds mean
    stays confident and the arithmetic mean collapses toward the middle.
    """
    a = np.array([0.90, 0.80])
    b = np.array([0.95, 0.60])
    blended = _blend(a, b, 0.5)
    assert blended[0] == pytest.approx(0.9282, abs=0.001)
    # Agreement is preserved exactly.
    assert _blend(a, a, 0.5) == pytest.approx(a)


def test_a_blend_weight_of_zero_returns_the_incumbent():
    a = np.array([0.7, 0.3, 0.55])
    assert _blend(a, np.array([0.1, 0.9, 0.5]), 0.0) == pytest.approx(a)


def test_the_weight_grid_contains_zero_so_the_null_can_win():
    assert 0.0 in WEIGHT_GRID


def _metrics(log_loss: float):
    from app.backtest.metrics import Metrics

    return Metrics(n=1000, log_loss=log_loss, brier_score=0.24, accuracy=0.55)


def test_a_chosen_weight_of_zero_is_a_rejection():
    verdict, reading = _judge(_metrics(0.686), _metrics(0.700), _metrics(0.686), 0.0)
    assert verdict == "REJECT"
    assert "grid contained" in reading


def test_a_blend_that_lowers_log_loss_improves():
    verdict, _ = _judge(_metrics(0.6868), _metrics(0.6900), _metrics(0.6860), 0.2)
    assert verdict == "IMPROVES"


def test_a_blend_that_does_not_lower_log_loss_is_no_effect():
    verdict, reading = _judge(_metrics(0.6868), _metrics(0.6900), _metrics(0.6869), 0.2)
    assert verdict == "NO_EFFECT"
    assert "measured no" in reading
