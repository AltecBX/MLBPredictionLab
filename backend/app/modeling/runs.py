"""Run scoring as a distribution, and the simulation that turns it into a game.

Every model in this repository so far predicts the winner directly. Three
feature groups have now been measured and rejected against that target, and the
diagnosis in MODELING_PLAN.md is the same each time: the signal is real but it is
already inside team strength by the time it reaches a binary outcome.

This takes the other route. Predict each side's **run distribution**, then ask
`P(home > away)`. The arithmetic is the same information arranged differently,
and the difference matters for two reasons:

* A distribution says things a probability cannot — the chance of a one-run
  game, of extra innings, of a blowout — which are the questions a reader
  actually asks after "who wins".
* A run model is scored on runs, which is a far denser target than a win. One
  game gives one bit of win information and about nine runs of scoring
  information, so the same season of games is a much larger sample.

**Negative binomial, not Poisson.** Baseball run scoring is overdispersed: the
variance of runs per team-game is roughly twice the mean, because runs arrive in
innings-shaped clumps rather than independently. Poisson assumes they are equal
and consequently understates both shutouts and blowouts — exactly the tails a
win probability is decided in. The dispersion is fitted from the data rather than
assumed, and `fit_dispersion` reports it so the assumption can be checked.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Baseball's extra-innings rule since 2020: a runner starts on second. That
# roughly doubles the per-half-inning scoring rate, and an extra-innings model
# that ignores it will systematically under-predict the chance a tie breaks
# quickly. Measured league-wide, a half inning with the runner on second scores
# about 0.55 runs against roughly 0.48 in a normal inning.
EXTRA_INNING_RUN_RATE = 0.55
NORMAL_INNING_SHARE = 1.0 / 9.0

# A game is nine innings, except the home half of the ninth is not played when
# the home side already leads. That asymmetry is small but real and it is the
# entire mechanism behind part of home-field advantage, so the simulation models
# it rather than folding it into a constant.
REGULATION_INNINGS = 9
MAX_EXTRA_INNINGS = 9

DEFAULT_SIMULATIONS = 20_000


@dataclass(frozen=True, slots=True)
class Dispersion:
    """Fitted overdispersion of runs per team-game."""

    mean: float
    variance: float
    #: Negative binomial size parameter `r`. Larger means closer to Poisson.
    size: float
    #: variance / mean. 1.0 is Poisson; baseball sits near 2.
    ratio: float
    n: int

    @property
    def is_overdispersed(self) -> bool:
        return self.ratio > 1.05


def fit_dispersion(runs: np.ndarray) -> Dispersion:
    """Method-of-moments fit of the negative binomial size parameter.

    For NB with mean m and size r, variance = m + m²/r, so r = m² / (v − m).
    When the sample is not overdispersed the fit is degenerate and `size` goes to
    infinity, which is Poisson — the right answer rather than an error.
    """
    values = np.asarray(runs, dtype=float)
    values = values[np.isfinite(values)]
    if values.size < 2:
        return Dispersion(0.0, 0.0, float("inf"), 1.0, int(values.size))

    mean = float(values.mean())
    variance = float(values.var(ddof=1))
    if variance <= mean or mean <= 0:
        return Dispersion(mean, variance, float("inf"), 1.0, int(values.size))

    size = mean * mean / (variance - mean)
    return Dispersion(mean, variance, float(size), variance / mean, int(values.size))


def sample_runs(
    rng: np.random.Generator, mean: np.ndarray | float, size: float, draws: int
) -> np.ndarray:
    """Draw runs from a negative binomial with a given mean and dispersion.

    numpy parameterises NB by (n successes, p), so the mean is set through
    `p = size / (size + mean)`. An infinite size is Poisson, which is what a
    non-overdispersed fit should produce.
    """
    mean_array = np.asarray(mean, dtype=float)
    if not np.isfinite(size):
        return rng.poisson(np.maximum(mean_array, 1e-9), size=draws)
    p = size / (size + np.maximum(mean_array, 1e-9))
    return rng.negative_binomial(size, p, size=draws)


def partial_size(size: float, part_mean: float, game_mean: float) -> float:
    """The size parameter for a *fraction* of a game.

    The negative binomial is closed under addition only when the two draws share
    the same `p`: NB(r₁, p) + NB(r₂, p) = NB(r₁ + r₂, p). Since `p` is
    `size / (size + mean)`, holding it fixed while taking a fraction f of the
    mean requires taking the same fraction f of the size.

    Reusing the game-level size for an inning-level draw quietly violates that,
    and the consequence is not subtle. The home side is drawn as eight innings
    plus a conditional ninth and the away side as one whole game; with the size
    reused, the home side came out with **11.7% less variance at the same mean**,
    purely from which dugout it occupied. Every simulated game was then decided
    partly by a distributional asymmetry that has nothing to do with baseball.
    """
    if not np.isfinite(size) or game_mean <= 0:
        return size
    return size * (part_mean / game_mean)


@dataclass(frozen=True, slots=True)
class GameSimulation:
    """What a simulated slate of one game says."""

    home_win_prob: float
    mean_home_runs: float
    mean_away_runs: float
    p_extra_innings: float
    p_one_run_game: float
    p_home_shutout: float
    p_away_shutout: float
    #: Most frequent final score, as (away, home).
    modal_score: tuple[int, int]
    simulations: int

    def to_dict(self) -> dict[str, float | int | list[int]]:
        return {
            "home_win_prob": round(self.home_win_prob, 6),
            "mean_home_runs": round(self.mean_home_runs, 3),
            "mean_away_runs": round(self.mean_away_runs, 3),
            "p_extra_innings": round(self.p_extra_innings, 4),
            "p_one_run_game": round(self.p_one_run_game, 4),
            "p_home_shutout": round(self.p_home_shutout, 4),
            "p_away_shutout": round(self.p_away_shutout, 4),
            "modal_score": list(self.modal_score),
            "simulations": self.simulations,
        }


def simulate_game(
    home_mean: float,
    away_mean: float,
    size: float,
    *,
    simulations: int = DEFAULT_SIMULATIONS,
    seed: int = 20240401,
) -> GameSimulation:
    """Simulate one game many times and report the distribution.

    Seeded, so the same inputs give the same answer — a prediction that changes
    when nothing changed is not a prediction. The seed is the caller's, and the
    prediction service derives it from the game id so two games never share a
    draw sequence.

    **Regulation is nine innings, and the home half of the ninth is conditional.**
    Runs are drawn per side for eight innings plus a conditional home ninth, so
    the walk-off structure is in the simulation rather than assumed away. A tie
    after regulation goes to extra innings under the runner-on-second rule, which
    scores at a materially higher rate than a normal inning.
    """
    rng = np.random.default_rng(seed)

    away_total = sample_runs(rng, away_mean, size, simulations)

    #
    # The home rate has to be corrected for the innings the home side does not
    # bat, and this is not a nicety.
    #
    # `home_mean` is a rate per *game*, measured from box scores in which the
    # home team already skipped the ninth whenever it was ahead. Feeding it
    # straight in and then skipping the ninth again applies that discount twice:
    # simulated home teams scored 4.36 against an input of 4.46, biasing home win
    # probability down by about a point across every game on the slate.
    #
    # The fix is exact rather than a fudge. Expected home runs are
    # `r · (8 + P(needs ninth))`, so the per-inning rate that actually produces
    # `home_mean` is `home_mean / (8 + P(needs ninth))`. P is not known in
    # advance, so it is measured on a first pass at the uncorrected rate and the
    # rate is then solved directly. One extra draw, and the input means what it
    # says.
    #
    naive = home_mean * NORMAL_INNING_SHARE
    probe = sample_runs(rng, naive * 8, partial_size(size, 8, 9), simulations)
    p_needs_ninth = float((probe <= away_total).mean())
    per_inning_home = home_mean / (8.0 + p_needs_ninth)

    # Split the size along with the mean, so the eight-inning block and the
    # ninth share the away side's `p` and sum to the same full-game
    # distribution. Without this the home side is quietly less variable than the
    # away side at an identical mean — see `partial_size`.
    home_eight = sample_runs(
        rng, per_inning_home * 8, partial_size(size, 8, 9), simulations
    )
    home_ninth = sample_runs(rng, per_inning_home, partial_size(size, 1, 9), simulations)

    # The home team bats in the ninth only when it is tied or behind.
    needs_ninth = home_eight <= away_total
    home_total = home_eight + np.where(needs_ninth, home_ninth, 0)

    tied = home_total == away_total
    n_tied = int(tied.sum())
    if n_tied:
        extra = _resolve_extra_innings(rng, n_tied, size)
        home_total = home_total.copy()
        away_total = away_total.copy()
        home_total[tied] += extra[0]
        away_total[tied] += extra[1]

    home_wins = home_total > away_total
    margin = np.abs(home_total.astype(int) - away_total.astype(int))

    return GameSimulation(
        home_win_prob=float(home_wins.mean()),
        mean_home_runs=float(home_total.mean()),
        mean_away_runs=float(away_total.mean()),
        p_extra_innings=n_tied / simulations,
        p_one_run_game=float((margin == 1).mean()),
        p_home_shutout=float((away_total == 0).mean()),
        p_away_shutout=float((home_total == 0).mean()),
        modal_score=_modal_score(away_total, home_total),
        simulations=simulations,
    )


def _resolve_extra_innings(
    rng: np.random.Generator, n: int, size: float
) -> tuple[np.ndarray, np.ndarray]:
    """Play extra innings until someone leads after a full inning.

    Both sides bat, the visitor first, at the runner-on-second rate. After
    `MAX_EXTRA_INNINGS` the remaining ties are broken by one more independent
    inning each — a game genuinely tied after eighteen innings is rarer than the
    numerical noise in a twenty-thousand-draw simulation, so an exact tail is not
    worth the loop.
    """
    # One inning is one ninth of a game, so it carries one ninth of the size for
    # the same reason the regulation split does.
    inning_size = partial_size(size, 1, 9)
    home = np.zeros(n, dtype=np.int64)
    away = np.zeros(n, dtype=np.int64)
    still_tied = np.ones(n, dtype=bool)

    for _ in range(MAX_EXTRA_INNINGS):
        live = int(still_tied.sum())
        if not live:
            break
        away_inning = sample_runs(rng, EXTRA_INNING_RUN_RATE, inning_size, live)
        home_inning = sample_runs(rng, EXTRA_INNING_RUN_RATE, inning_size, live)
        away[still_tied] += away_inning
        home[still_tied] += home_inning
        decided = away_inning != home_inning
        idx = np.flatnonzero(still_tied)
        still_tied[idx[decided]] = False

    if still_tied.any():
        live = int(still_tied.sum())
        away[still_tied] += sample_runs(rng, EXTRA_INNING_RUN_RATE, inning_size, live)
        home[still_tied] += (
            sample_runs(rng, EXTRA_INNING_RUN_RATE, inning_size, live) + 1
        )

    return home, away


def _modal_score(away: np.ndarray, home: np.ndarray) -> tuple[int, int]:
    """The most frequent final score. Capped so one freak blowout cannot win."""
    a = np.clip(away.astype(int), 0, 20)
    h = np.clip(home.astype(int), 0, 20)
    counts = np.bincount(a * 21 + h, minlength=21 * 21)
    best = int(counts.argmax())
    return best // 21, best % 21


__all__ = [
    "DEFAULT_SIMULATIONS",
    "EXTRA_INNING_RUN_RATE",
    "Dispersion",
    "GameSimulation",
    "fit_dispersion",
    "partial_size",
    "sample_runs",
    "simulate_game",
]
