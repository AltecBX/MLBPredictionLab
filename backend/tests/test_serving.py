"""What is served, and the guarantee that it is what was measured.

The blend won on two seasons in `simulate-check` before anything served it. The
risk that creates is specific: the served path and the measured path are two
different pieces of code that must produce the same number, and nothing but a
test stops them drifting apart. Most of what follows exists for that reason.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.features.context import GameContext
from app.modeling.runs import MAX_REPORTED_RUNS, TOP_SCORES, fit_dispersion, simulate_game
from app.modeling.serving import (
    SERVED_WEIGHT,
    blend_log_odds,
    dispersion_asof,
    serve_probability,
)
from app.modeling.simulation import PREREGISTERED_WEIGHT, _blend

# The fixture store carries 40 games — 80 team-lines — which is deliberately
# below the production floor, so tests that want a blend must ask for it.
FIXTURE_MIN_SAMPLE = 40


# --------------------------------------------------------------------------
# The served path must equal the measured path
# --------------------------------------------------------------------------


def test_served_blend_equals_the_backtest_blend():
    """The whole claim of promotion is that these two agree.

    `_blend` is what produced the two-season result; `blend_log_odds` is what
    goes on the screen. If they can disagree, the measurement stops describing
    the product.
    """
    rng = np.random.default_rng(7)
    logistic = rng.uniform(0.05, 0.95, 500)
    simulation = rng.uniform(0.05, 0.95, 500)

    expected = _blend(logistic, simulation, PREREGISTERED_WEIGHT)
    actual = [
        blend_log_odds(float(a), float(b), PREREGISTERED_WEIGHT)
        for a, b in zip(logistic, simulation, strict=True)
    ]
    assert actual == pytest.approx(list(expected), abs=1e-12)


def test_the_served_weight_is_the_preregistered_one():
    """Not the searched one. Serving the grid argmax would select on the test set."""
    assert SERVED_WEIGHT == PREREGISTERED_WEIGHT


def test_blend_is_not_a_probability_average():
    """Averaging probabilities shrinks toward .500; log-odds averaging does not.

    Two models agreeing on a confident call must stay confident. This is the
    property the arithmetic mean destroys, so it is pinned rather than assumed.
    """
    both_confident = blend_log_odds(0.90, 0.90, 0.5)
    assert both_confident == pytest.approx(0.90, abs=1e-9)
    assert both_confident > (0.90 + 0.90) / 2 - 1e-9


def test_blend_lands_between_its_inputs():
    for a, b in ((0.30, 0.70), (0.55, 0.51), (0.02, 0.40)):
        blended = blend_log_odds(a, b, 0.5)
        assert min(a, b) <= blended <= max(a, b)


def test_weight_zero_and_one_recover_each_component():
    assert blend_log_odds(0.62, 0.31, 0.0) == pytest.approx(0.62, abs=1e-9)
    assert blend_log_odds(0.62, 0.31, 1.0) == pytest.approx(0.31, abs=1e-9)


# --------------------------------------------------------------------------
# A missing simulation is not a 0.5 simulation
# --------------------------------------------------------------------------


def test_no_dispersion_falls_back_to_the_logistic_and_says_so(store, builder, target_game):
    served = serve_probability(
        store, builder, target_game, target_game.first_pitch_utc, 0.62, dispersion=None
    )
    assert served.probability == pytest.approx(0.62)
    assert served.simulation is None
    assert not served.is_blended
    assert served.weight == 0.0
    assert served.unavailable_reason
    assert served.game_simulation is None


def test_a_team_without_enough_games_falls_back_rather_than_guessing(
    store, builder, fixture_frames
):
    """The season's opening fortnight, which is a real state and not an error."""
    first = GameContext.from_row(store.games.iloc[0].to_dict())
    dispersion = fit_dispersion(np.array([3.0, 4.0, 5.0, 2.0, 6.0] * 40))
    served = serve_probability(
        store, builder, first, first.first_pitch_utc, 0.58, dispersion=dispersion
    )
    assert not served.is_blended
    assert served.unavailable_reason == "not enough games on record to project runs"
    assert served.probability == pytest.approx(0.58)


def test_fallback_probability_is_never_silently_a_half(store, builder, target_game):
    """0.5 is a coin flip; a missing model is an absence. They are not the same."""
    served = serve_probability(
        store, builder, target_game, target_game.first_pitch_utc, 0.73, dispersion=None
    )
    assert served.probability != 0.5
    assert served.probability == pytest.approx(0.73)


# --------------------------------------------------------------------------
# The blend, when it does run
# --------------------------------------------------------------------------


def _dispersion(store, game):
    return dispersion_asof(
        store, game.first_pitch_utc, min_sample=FIXTURE_MIN_SAMPLE
    )


def test_a_usable_game_is_blended_and_carries_its_simulation(store, builder, target_game):
    dispersion = _dispersion(store, target_game)
    assert dispersion is not None

    served = serve_probability(
        store, builder, target_game, target_game.first_pitch_utc, 0.55,
        dispersion=dispersion, simulations=4000,
    )
    assert served.is_blended
    assert served.game_simulation is not None
    assert served.weight == SERVED_WEIGHT
    assert served.unavailable_reason is None
    assert served.probability == pytest.approx(
        blend_log_odds(0.55, served.simulation, SERVED_WEIGHT)
    )


def test_the_served_simulation_is_the_backtests_simulation(store, builder, target_game):
    """Not just the same blend arithmetic — the same simulated probability.

    `simulate_slate` is what produced the two-season result; `serve_probability`
    is what runs on the slate. They reach the run means by different call paths,
    so nothing but this test guarantees the product simulates the game the way
    the measurement did.
    """
    import pandas as pd

    from app.modeling.run_inputs import BASE
    from app.modeling.simulation import simulate_slate

    dispersion = _dispersion(store, target_game)
    frame = pd.DataFrame(
        [{"game_id": target_game.game_id, "as_of": target_game.first_pitch_utc}]
    )
    slate = simulate_slate(
        store, builder, frame, dispersion.size, 4000, models=(BASE,), parks=None
    )
    served = serve_probability(
        store, builder, target_game, target_game.first_pitch_utc, 0.55,
        dispersion=dispersion, simulations=4000,
    )
    assert served.simulation == pytest.approx(float(slate.iloc[0]["sim_base"]), abs=1e-12)


def test_the_same_game_simulates_to_the_same_number_twice(store, builder, target_game):
    """A prediction that moves when nothing moved is not a prediction."""
    dispersion = _dispersion(store, target_game)
    kwargs = {"dispersion": dispersion, "simulations": 4000}
    first = serve_probability(
        store, builder, target_game, target_game.first_pitch_utc, 0.55, **kwargs
    )
    second = serve_probability(
        store, builder, target_game, target_game.first_pitch_utc, 0.55, **kwargs
    )
    assert first.probability == second.probability
    assert first.seed == second.seed


def test_two_games_do_not_share_a_draw_sequence(store, builder):
    a = GameContext.from_row(store.games.iloc[34].to_dict())
    b = GameContext.from_row(store.games.iloc[35].to_dict())
    dispersion = _dispersion(store, b)
    sa = serve_probability(store, builder, a, a.first_pitch_utc, 0.5,
                           dispersion=dispersion, simulations=2000)
    sb = serve_probability(store, builder, b, b.first_pitch_utc, 0.5,
                           dispersion=dispersion, simulations=2000)
    assert sa.seed != sb.seed


# --------------------------------------------------------------------------
# Dispersion is fitted as-of, like everything else
# --------------------------------------------------------------------------


def test_dispersion_refuses_a_sample_too_small_to_fit(store, target_game):
    """Early April is a real state. It gets no simulation rather than a bad one."""
    assert dispersion_asof(store, target_game.first_pitch_utc) is None


def test_dispersion_cannot_see_the_game_it_will_be_used_to_simulate(store):
    """The leakage rule, applied to a parameter rather than to a feature."""
    early = GameContext.from_row(store.games.iloc[10].to_dict())
    late = GameContext.from_row(store.games.iloc[35].to_dict())
    early_fit = dispersion_asof(store, early.first_pitch_utc, min_sample=1)
    late_fit = dispersion_asof(store, late.first_pitch_utc, min_sample=1)
    assert early_fit is not None and late_fit is not None
    assert early_fit.n < late_fit.n


# --------------------------------------------------------------------------
# The distributions that reach the screen
# --------------------------------------------------------------------------


def test_run_distributions_are_probabilities_that_sum_to_one():
    sim = simulate_game(4.6, 4.3, 3.4, simulations=20000, seed=11, distributions=True)
    for side in (sim.home_run_distribution, sim.away_run_distribution):
        assert len(side) == MAX_REPORTED_RUNS + 1
        assert sum(side) == pytest.approx(1.0, abs=1e-9)
        assert all(p >= 0 for p in side)


def test_the_top_bucket_pools_the_tail_rather_than_truncating_it():
    """A ten-run game is rare, not impossible, and the last bucket is "or more"."""
    sim = simulate_game(9.0, 9.0, 3.4, simulations=20000, seed=12, distributions=True)
    assert sim.home_run_distribution[MAX_REPORTED_RUNS] > 0.10
    assert sum(sim.home_run_distribution) == pytest.approx(1.0, abs=1e-9)


def test_score_distribution_is_ordered_and_reports_what_it_leaves_out():
    sim = simulate_game(4.6, 4.3, 3.4, simulations=20000, seed=13, distributions=True)
    payload = sim.score_distribution_dict()
    probs = [row["probability"] for row in payload["scores"]]
    assert len(probs) == TOP_SCORES
    assert probs == sorted(probs, reverse=True)
    # Baseball's score distribution is long-tailed; the top handful is a
    # minority of it, and saying so is the point of reporting `covered`.
    assert 0.0 < payload["covered"] < 0.5
    assert payload["covered"] == pytest.approx(sum(probs), abs=1e-4)


def test_score_distribution_is_deterministic_between_identical_runs():
    """Equal counts must not reshuffle; the display would look like a new opinion."""
    a = simulate_game(4.6, 4.3, 3.4, simulations=8000, seed=14, distributions=True)
    b = simulate_game(4.6, 4.3, 3.4, simulations=8000, seed=14, distributions=True)
    assert a.score_distribution == b.score_distribution


def test_distributions_are_off_by_default():
    """The ablation runs this four times a game and has no use for them."""
    sim = simulate_game(4.6, 4.3, 3.4, simulations=2000, seed=15)
    assert sim.home_run_distribution == ()
    assert sim.score_distribution == ()


def test_asking_for_distributions_does_not_change_the_probability():
    """They are read off the same draws, so the headline number must not move."""
    plain = simulate_game(4.6, 4.3, 3.4, simulations=8000, seed=16)
    detailed = simulate_game(4.6, 4.3, 3.4, simulations=8000, seed=16, distributions=True)
    assert plain.home_win_prob == detailed.home_win_prob
    assert plain.modal_score == detailed.modal_score
