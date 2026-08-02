# MODELING_PLAN — Jerry MLB Prediction Lab

The target is a **calibrated probability**, not a pick. Everything below is
organized around that: model choice, selection criteria, ensembling, calibration,
confidence and explanation.

---

## 1. Prediction target

```
y = 1  if the home team won
y = 0  if the away team won
```

Scope: regular-season and postseason games with a final result. Ties are not
possible in MLB. Suspended/postponed games are excluded from training.

The primary output is `P(home win | information available at as_of)`. The away
probability is `1 − P(home)` by construction, validated at the DB and API layers.

---

## 2. Loss and selection criteria, in priority order

| Rank | Metric | Why |
|---|---|---|
| 1 | **Log loss** | Proper scoring rule; punishes confident errors, which is exactly the failure mode that matters. |
| 2 | **Brier score** | Second proper rule; less sensitive to extreme predictions, catches different pathologies. |
| 3 | **Calibration error (ECE / MCE)** | A 60% prediction must win ~60% of the time or the number is not usable. |
| 4 | ROC AUC | Ranking quality, independent of calibration. |
| 5 | Accuracy | Reported, never optimized. In MLB the ceiling is roughly 60% and accuracy is nearly uninformative between two models with the same discrimination. |

A model is promoted only if it improves **walk-forward** log loss *and* does not
worsen calibration error. In-sample performance is never a selection input.

**Reference points.** MLB is a high-variance sport. A well-built model lands
roughly in the 0.66–0.68 log-loss range and 55–58% accuracy over a full season;
the market's closing line is the practical ceiling at roughly 0.65. A backtest
claiming materially better than that is evidence of leakage, and
[BACKTEST_PLAN](BACKTEST_PLAN.md) §8 treats it as a failing condition rather
than a success.

---

## 3. The five models

### Model 1 — Regularized logistic regression *(Phase 1, shipped)*

* L2-penalized logistic regression on the standardized `fs_v1` difference
  features.
* `C` selected by walk-forward validation over a fixed grid, never by in-sample
  fit or by a random-shuffle CV that would mix future into past.
* Median imputation for missing values, with the imputer **fit only on the
  training fold**, plus an explicit missing-indicator column for gated features.
* Chosen as the Phase 1 baseline because it is monotone, inspectable, hard to
  overfit on ~2,400 games per season, and produces contribution values that
  translate directly into "this factor is worth +X probability points" without
  needing SHAP.

**Contribution math.** For a standardized feature vector `z` and coefficients
`β`, the log-odds is `η = β₀ + Σ βᵢzᵢ`. The contribution of feature *i* in
probability points is computed as a leave-one-out effect:

```
contribution_pp_i = 100 × [ σ(η) − σ(η − βᵢzᵢ) ]
```

This is exact, additive in log-odds, and reported in the units a reader
understands. Contributions are grouped into categories (starting pitching,
offense, bullpen, defense, schedule/rest, environment, team strength) for the
top-5 for / top-5 against display.

### Model 2 — Gradient-boosted trees *(Phase 2)*

* LightGBM (fallback XGBoost), binary logloss objective.
* Optuna tuning with a **time-series split that respects chronology**, a bounded
  trial budget, and a fixed seed. Tuning runs on the training window only.
* Early stopping on a validation fold that is strictly later than train and
  strictly earlier than test.
* Explained with SHAP, then translated into the same probability-point language
  as Model 1 so the UI has one explanation vocabulary.

### Model 3 — Elo with starting-pitcher and home-field adjustment *(Phase 1 reference → Phase 2 ensemble member)*

```
elo_home_adj = elo_home + HFA + sp_adjust(home_starter) − sp_adjust(away_starter)
P(home) = 1 / (1 + 10^(−(elo_home_adj − elo_away)/400))
```

* `K` and the home-field constant are fit on historical data by minimizing
  walk-forward log loss, not set by convention.
* Between seasons the rating regresses toward 1500 by a fitted fraction.
* `sp_adjust` maps a starter's stabilized quality to an Elo delta, bounded so a
  single starter cannot swing the rating implausibly.
* Phase 1 ships Elo in two roles: the pre-game *rating* is a feature inside
  Model 1, and the Elo *win probability* is served alongside the calibrated
  probability as a **reference model**. It carries no ensemble weight — the
  served probability is Model 1's alone — but the spread between the two is a
  genuine disagreement signal and feeds confidence (§6). Phase 2 promotes Elo
  to a weighted ensemble member with the starting-pitcher adjustment fitted.

### Model 4 — Expected runs *(Phase 3)*

* Two run-scoring models, one per side, predicting the run distribution rather
  than a point estimate.
* Poisson as the baseline; negative binomial when the fitted dispersion is
  significantly greater than 1, which it usually is for baseball run scoring.
* Inputs: lineup strength, opposing starter quality and expected innings,
  bullpen quality weighted by expected relief innings, park factor, weather.
* Win probability follows from `P(home runs > away runs)` with an explicit
  extra-innings tiebreak model.

### Model 5 — Monte Carlo game simulation *(Phase 3)*

* ≥ 10,000 simulations per game, seeded and reproducible.
* Simulates: starter innings drawn from the starter's own workload
  distribution, transition to bullpen with availability-weighted reliever
  quality, per-inning run scoring from the Model 4 distribution, and the extra
  innings rule.
* Produces the win distribution, run distributions, most common scores,
  extra-innings probability, one-run-game probability and upset probability.
* Re-runs on any material change (starter change, lineup confirmation, scratch,
  weather move beyond threshold, bullpen availability change).

---

## 4. Ensemble

The ensemble is fit **only on out-of-sample predictions**:

1. Run the walk-forward backtest. Each model produces a prediction for every
   game using a model trained strictly on prior games.
2. Collect those out-of-sample probabilities into a stacking matrix.
3. Fit non-negative weights that sum to 1 by minimizing log loss on those
   out-of-sample predictions, with a time-blocked split so the weight fit itself
   is validated on a later period than it was fit on.
4. Shrink weights toward the equal-weight vector to avoid a weight solution that
   chases noise in one season.

The ensemble may not be fit on in-sample predictions under any circumstance.
This is enforced structurally: the stacking matrix is produced only by the
backtest engine, which cannot emit an in-sample row.

**Model agreement** — the standard deviation of the component probabilities — is
recorded on every prediction and feeds confidence.

---

## 5. Calibration

Raw classifier outputs are not directly usable as probabilities.

* **Method selection.** Both isotonic regression and Platt scaling are fit on a
  validation window that is later than train and earlier than test. Whichever
  gives lower log loss *on the test window* across the walk-forward is selected
  and recorded in `model_versions.calibration_method`.
* **Guidance.** Isotonic needs a few thousand validation samples to avoid
  overfitting; below that, Platt is the default. With one MLB season ≈ 2,430
  games, Phase 1 uses Platt (sigmoid) unless the validation window spans
  multiple seasons.
* **Never refit on test.** The calibrator is part of the model artifact and is
  applied, not re-fit, at prediction time.
* Both the pre- and post-calibration probability are stored on every prediction
  so the effect of calibration is auditable.

---

## 6. Confidence

Confidence is **not** a restatement of the probability. It is a weighted score
over five signals, each in `[0,1]`:

| Signal | Weight | Definition |
|---|---|---|
| Model agreement | 0.25 | `1 − std(component_probs) / 0.10`, clamped to `[0,1]`. Phase 1 compares the calibrated model against the Elo reference. When fewer than two components are available the signal is `NULL` and its weight is redistributed over the remaining signals. |
| Data completeness | 0.25 | The completeness score from [DATA_SOURCES](DATA_SOURCES.md) §6. |
| Input confirmation | 0.20 | Starter confirmed (0.6) + lineup confirmed (0.4). |
| Historical calibration for similar predictions | 0.20 | `1 − |observed − predicted|` in this prediction's probability band, from the most recent backtest. |
| Prediction stability | 0.10 | `1 − normalized` spread of the probability under perturbation of uncertain inputs (missing starter → replacement prior; form features → ± their standard error). |

Distance from 50% enters **only** as a small tie-breaking term (≤ 0.10 of the
final score) and never as the primary driver, because a 70% prediction built on
missing inputs deserves lower confidence than a 55% prediction built on
confirmed ones.

Labels: `HIGH ≥ 0.75`, `MODERATE ≥ 0.55`, `LOW ≥ 0.35`, `VERY_LOW` below,
`INSUFFICIENT_DATA` when completeness < 0.5.

---

## 7. Recommendation label

Derived from the *edge over a neutral baseline*, gated by confidence:

| Label | Condition |
|---|---|
| `INSUFFICIENT_DATA` | completeness < 0.5, or either starter unknown and completeness < 0.65 |
| `STRONG_LEAN` | `\|p − 0.5\| ≥ 0.10` and confidence ≥ 0.65 |
| `MODERATE_LEAN` | `\|p − 0.5\| ≥ 0.06` and confidence ≥ 0.50 |
| `SMALL_LEAN` | `\|p − 0.5\| ≥ 0.03` |
| `NO_MEANINGFUL_ADVANTAGE` | otherwise |

The words "lock", "guaranteed" and "sure thing" do not appear anywhere in the
codebase or UI. A lint test asserts this.

---

## 8. Fair moneyline and market comparison

Fair moneyline is a direct transform of the model probability:

```
p ≥ 0.5 → −100 × p / (1 − p)
p < 0.5 → +100 × (1 − p) / p
```

Market comparison requires a configured licensed odds provider. When present,
the market's two-sided prices are de-vigged (multiplicative method, with the
shin method available) before comparison, and `market_edge = model_prob −
novig_market_prob`. When no provider is configured the market fields are `NULL`
and the UI hides the market comparison entirely rather than showing zeros.

---

## 9. Training protocol

1. **Data cut.** Regular-season games with a final result, both starters
   resolvable, and both teams having at least `min_games` prior games in the
   season or the previous season.
2. **Feature build.** As-of features computed at the prediction timestamp
   policy — `T − 3h` for the standard training row. Every training row's
   features are produced by the exact same code path that produces a live
   prediction. There is no separate "training feature" implementation.
3. **Split.** Walk-forward by date. No shuffling. No random CV.
4. **Fit.** Scaler, imputer and model fit on train only. Calibrator fit on the
   validation slice only.
5. **Register.** Persist artifact + SHA-256, hyperparameters, feature-set
   version, train window, row count, and out-of-sample metrics into
   `model_versions`. Activation is a separate, explicit step.
6. **Reproducibility.** Fixed seeds; `tests/test_model_reproducibility.py`
   asserts that retraining on the same window yields identical coefficients and
   identical predictions.

---

## 10. Retraining and drift *(Phase 4)*

* Nightly walk-forward refit. A new version is registered every run, but
  activated only if out-of-sample log loss improves by a margin exceeding the
  run-to-run noise band.
* **Feature drift** — population stability index per feature versus the training
  distribution; alert above 0.2.
* **Calibration drift** — rolling 30-day ECE; alert when it exceeds the backtest
  value by a fixed multiple.
* **Importance stability** — coefficient/importance rank correlation across
  consecutive retrains; a feature with unstable importance is a candidate for
  removal in the ablation review.

---

## 11. What the model deliberately does not do

* It does not use closing odds as a feature for predictions made before close.
* It does not use any postgame statistic, including the game's own weather
  observation, box score, or attendance.
* It does not use season-to-date aggregates pulled from a stats endpoint; every
  aggregate is rebuilt from dated game logs.
* It does not let a 6-game head-to-head record outweigh 300 games of team
  quality.
* It does not report a probability without an accompanying completeness and
  freshness state.

---

## Ensemble: measured, and rejected on the evidence

A gradient-boosted component was built, blended with the logistic model, and
evaluated walk-forward. It does not earn its place, so the logistic model is
served unchanged.

Reproduce with `python -m app.cli ensemble-check`. Measured over **8,339
out-of-sample games**, 27 walk-forward steps, both components sharing the same
training window, validation slice and test window at every step:

| | Log loss | Brier | Calibration error | Accuracy | AUC |
|---|---|---|---|---|---|
| **Logistic (served)** | **0.6842** | **0.2455** | **0.83%** | **55.9%** | **0.572** |
| GBDT alone | 0.6895 | 0.2481 | 1.38% | 53.7% | 0.548 |
| Best blend | 0.6842 | 0.2455 | 0.83% | 55.9% | 0.572 |

The blend weight is searched on out-of-sample predictions only, over a grid
that includes 0.0 so the null hypothesis can win. It did:

| GBDT weight | 0.0 | 0.1 | 0.2 | 0.3 | 0.4 | 0.5 |
|---|---|---|---|---|---|---|
| Log loss | **0.6842** | 0.6842 | 0.6844 | 0.6847 | 0.6851 | 0.6855 |

Monotonically worse as the boosted component gains weight. That is a clean
negative result, not an inconclusive one.

**Why this is the expected outcome.** The dataset is ~9,000 rows and 42
strongly correlated engineered features, most of them already differences of
rates that have been shrunk toward a baseline. There is very little interaction
structure left for trees to find that the linear model has not already been
handed directly, and boosting on a small wide matrix spends its capacity on
noise. The ablation result points the same way: no single feature carries more
than 8.8% of the model's weight, which is the signature of a broad, flat,
additive signal.

**Two things this does not mean.** It does not mean trees are wrong for this
problem — it means they are wrong for *this feature set*. Once Statcast
provides genuinely non-linear inputs (contact quality, velocity trends,
platoon splits at the plate-appearance level), the comparison is worth
re-running; `ensemble-check` exists so it costs one command. And it does not
mean the ensemble machinery is dead code: it is the measurement that keeps the
question answerable with evidence instead of assumption.

**Blending is in log-odds, not probability.** Averaging probabilities pulls
every prediction toward .500 — a shrinkage operator wearing an ensemble's hat,
and it damages the tails, which is exactly where this model is already
overconfident. A test asserts the log-odds behaviour.

---

## Starting-pitcher Statcast: measured, and rejected on the evidence

The second negative result kept on purpose. Nine features — expected wOBA,
barrel rate, hard-hit rate and exit velocity allowed; whiff, chase and
called-strike-plus-whiff rates; four-seam velocity; and the 30-day velocity
delta — were built as feature set `fs_v2` and evaluated against `fs_v1`. They do
not earn their place, so the active feature set is unchanged.

Reproduce with `python -m app.cli compare-feature-sets --seasons 2024`.

**Head to head**, both sets refit walk-forward, regularisation selected for each
by the same rule, scored on the same games. Run twice — once over 2024, once
testing on 2025 with 2024 available as the prior season, which is the setup
where these features have their best chance:

**2024 — 1,741 scored games**

| | Log loss | Brier | Calibration error | Accuracy | AUC |
|---|---|---|---|---|---|
| **fs_v1 (served), 42 features** | **0.68383** | **0.24545** | **0.50%** | 56.17% | **0.5658** |
| fs_v2, 51 features | 0.68423 | 0.24560 | 1.59% | **56.69%** | 0.5657 |

**2025 — 2,363 scored games, trained from 2024**

| | Log loss | Brier | Calibration error | Accuracy | AUC |
|---|---|---|---|---|---|
| fs_v1 (served), 42 features | 0.68682 | 0.24677 | 2.11% | 55.56% | 0.5562 |
| fs_v2, 51 features | **0.68645** | **0.24660** | **2.02%** | **55.73%** | **0.5580** |

| Paired 95% interval | Δ log loss | Δ Brier | Δ calibration error |
|---|---|---|---|
| 2024 | −0.0004 [−0.0032, +0.0026] | −0.0001 [−0.0015, +0.0013] | −0.0037 [−0.0220, +0.0129] |
| 2025 | +0.0004 [−0.0003, +0.0010] | +0.0002 [−0.0002, +0.0005] | +0.0009 [−0.0045, +0.0072] |

**Six intervals, all six spanning zero — and the sign of the log-loss difference
flips between the two seasons.** That is a stronger statement than either season
alone could make. A small real effect would show the same sign twice and shrink
its interval as the sample grew; this one wanders around zero. The 2025 interval
is four times tighter than 2024's, because the training window is larger, and
zero is still comfortably inside it. This is the season-stability gate in
BACKTEST_PLAN.md § Phase 2A doing its job.

Accuracy rose in both seasons, by half a point and then by a fifth of one. That
is exactly the trade this system does not make: §2 ranks log loss and calibration
above accuracy, and a model that is right slightly more often while stating no
better probabilities has not improved.

**Leave-one-out and group-alone**, inside `fs_v2`:

| Group | Δ log loss on removal | Group alone, vs a coin flip |
|---|---|---|
| team_strength | −0.0029 | +0.0063 |
| offense | −0.0024 | +0.0047 |
| **starting_pitcher_statcast** | **−0.0041** | **+0.00001** |
| starting_pitcher (box score) | +0.0011 | +0.0026 |

Removing the group *improves* log loss more than removing any other group, and
on its own it beats a coin flip by one hundred-thousandth of a nat. Both views
agree, which is the point of running both.

**Why, in the univariate numbers rather than in the fit.** Every one of the nine
correlates with the outcome more weakly than the box-score starter features
already in the model, and the strongest of them are largely the same variable:

| Feature | sd | r with home win | Largest \|r\| with an existing feature |
|---|---|---|---|
| `sc_sp_xwoba_allowed_diff` | 0.020 | +0.069 | 0.693 `sp_k_minus_bb_pct_diff` |
| `sc_sp_csw_pct_diff` | 0.024 | +0.067 | 0.670 `sp_k_pct_season_diff` |
| `sc_sp_whiff_pct_diff` | 0.042 | +0.056 | 0.738 `sp_k_pct_season_diff` |
| `sc_sp_fastball_velocity_diff` | 1.69 | +0.050 | 0.463 `sp_k_pct_season_diff` |
| `sc_sp_chase_pct_diff` | 0.030 | +0.046 | 0.436 `sp_k_minus_bb_pct_diff` |
| `sc_sp_hard_hit_pct_allowed_diff` | 0.050 | +0.036 | 0.229 `sp_fip_season_diff` |
| `sc_sp_barrel_pct_allowed_diff` | 0.020 | +0.017 | 0.521 `sp_hr_per_9_diff` |
| `sc_sp_avg_exit_velocity_allowed_diff` | 1.74 | +0.015 | 0.232 `sp_fip_season_diff` |
| `sc_sp_velocity_delta_30d_diff` | 0.52 | +0.005 | 0.075 `sp_bb_pct_season_diff` |
| *for comparison:* `sp_k_minus_bb_pct_diff` | | **+0.082** | |
| *for comparison:* `elo_diff` | | **+0.130** | |

Read down that table and the result stops being surprising. The features that
correlate most with winning are the ones that correlate most with the
strikeout-rate feature the model already has — they are a second, noisier
measurement of the same thing. And the part that is genuinely new, contact
quality allowed and the velocity trend, is the weakest of all.

**This is not over-shrinkage.** The spreads are healthy: five percentage points
of hard-hit rate and 1.7 mph of fastball velocity between two starting pitchers.
The features are well formed; they are measuring something real about the
pitcher that turns out not to move a single game's outcome.

**What it does not mean.** It does not mean Statcast is worthless here. It means
*starter-level season aggregates* are worthless here, on top of box-score
starter features that already exist. The three hypotheses it leaves open, in
descending order of how much they would change:

1. **The matchup, not the pitcher.** A starter's arsenal against *this* lineup's
   weaknesses is a different quantity from his arsenal in general, and the
   general version is what was measured. That is the pitch-arsenal matchup
   engine, and it needs batter-side Statcast that is now ingested.
2. **The batters, not the pitcher.** A starter is a minority of a team's run
   prevention and none of its scoring. Nine lineup slots of contact quality is
   a larger surface than one pitcher's.
3. **Non-linearity.** The ensemble section above notes that trees had nothing to
   find in 42 shrunk, correlated rate differences. That argument does not change
   with nine more of the same shape — which is itself evidence that the next
   feature group should not be more of this shape.

Both seasons are now measured and both say the same thing. What would still
change the answer is a different *shape* of feature, not more data on this one —
which is what the three hypotheses above are for.

Reproduce either run:

```
python -m app.cli compare-feature-sets --seasons 2024
python -m app.cli compare-feature-sets --seasons 2024,2025 --start 2025-04-01
```

---

## Projected lineups and the arsenal matchup: measured, and rejected

The third negative result, and the one that says the most about where the
ceiling is. Seven features testing the two hypotheses the starting-pitcher
rejection left open — the matchup, and the batters. Neither earns a place; the
active feature set is unchanged.

Reproduce with:

```
python -m app.cli compare-feature-sets --baseline fs_v1 --candidate fs_v3 \
    --seasons 2024,2025 --start 2025-04-01
python -m app.cli compare-feature-sets --baseline fs_v1 --candidate fs_v4 \
    --seasons 2024,2025 --start 2025-04-01
```

**Measured together (fs_v3), 2,363 games:**

| | Log loss | Brier | Calibration error | Accuracy |
|---|---|---|---|---|
| **fs_v1 (served), 42 features** | **0.68682** | **0.24677** | **2.11%** | 55.56% |
| fs_v3, 49 features | 0.68726 | 0.24698 | 2.13% | **55.69%** |

Δ log loss −0.000444, paired 95% CI [−0.00102, +0.00014]. Every interval spans
zero.

**Then the ablation showed the average was the wrong thing to look at.** The two
halves of the group behaved nothing alike:

| Group | Δ log loss on removal | Group alone, vs a coin flip |
|---|---|---|
| offense (6 features) | +0.00047 | +0.00493 |
| **arsenal_matchup (2)** | +0.00009 | **+0.00380** |
| bullpen (4) | +0.00078 | +0.00262 |
| starting_pitcher (13) | +0.00012 | +0.00050 |
| **projected_lineup (5)** | −0.00053 | **−0.00609** |

Two features carrying +0.0038 on their own is the best per-feature standalone
signal in this model — team strength manages +0.0060 with eight. Five features
coming in *worse* than a coin flip is the opposite.

**So the arsenal pair was re-measured alone (fs_v4), 2,363 games:**

| | Log loss | Brier | Calibration error | Accuracy |
|---|---|---|---|---|
| fs_v1 (served), 42 features | 0.686816 | 0.246772 | 2.109% | 55.56% |
| fs_v4, 44 features | **0.686733** | **0.246741** | **2.034%** | **55.82%** |

Δ log loss **+0.000084**, paired 95% CI [−0.00028, +0.00046].

Dropping the lineup half moved the delta by +0.000528. The ablation had
independently estimated that half was worth −0.00053. **Two different methods
agreeing to two parts in a million** is the strongest available evidence that
this comparison machinery measures what it claims to.

It still does not clear the bar. An interval of [−0.00028, +0.00046] is a group
that cannot be told apart from nothing, and NO_EFFECT is not adoption.

### What three rejections in a row actually say

Not "these were bad ideas". Look at where the model's edge comes from. Against
the always-50% baseline of 0.6931, fs_v1 scores 0.6868 — an improvement of
**0.0063**. Team strength alone is worth **+0.0060** of that. Offense adds
+0.0049 standing alone but almost nothing on top. Starting pitching, thirteen
features of it, is worth +0.0005 alone.

Every group measured in Phase 2 has been redundant with team strength, including
the two that carry real standalone signal. That is not a feature-engineering
problem, and a fourth group of the same shape will not fix it. A single baseball
game is close to a coin flip, and this model is already extracting most of what
team-level information can say about one.

The hypotheses that remain are the ones that change the *shape* of the question,
not the number of columns:

1. **Predict runs, not the winner.** A negative-binomial run model with an
   innings allocation and a Monte Carlo over it produces a distribution rather
   than a point, and the tails are where a matchup feature would show up if it
   shows up anywhere. `arsenal_xwoba_edge` predicts contact quality; win
   probability is three inferential steps downstream of that, and each step
   costs signal. This is step 8 in IMPLEMENTATION_PLAN.md and it is now the
   highest-value one.
2. **A market baseline.** Nothing here has ever been compared against a de-vigged
   closing line. Without one there is no way to know whether 0.6868 is close to
   the achievable floor or a long way above it — and that is the single most
   useful unknown left. It needs a licensed odds provider.
3. **Confirmed lineups at a later snapshot.** Every lineup feature here is
   projected because no posted lineup is knowable at T−3h. A pregame poller plus
   the prediction timeline would let the same features be scored at T−60m, where
   they would be facts rather than guesses.

---

## Simulating runs: measured, and it works

The first thing in this repository to beat the served model. Three feature
groups were measured against the binary win target and rejected, each with the
same diagnosis — the signal is real but already inside team strength by the time
it reaches one bit of outcome. This changed the target instead of the inputs, and
that is what moved.

Reproduce. Two commands, because one cannot produce both rows — `--start` is the
lower bound on the *test* windows, so the 2025 invocation scores no 2024 game:

```
# 2025, trained from 2024
python -m app.cli simulate-check --seasons 2024,2025 --start 2025-04-01
# 2024, trained from its own opening weeks
python -m app.cli simulate-check --seasons 2024
```

**2025 — 2,363 scored games**

| | Log loss | Brier | Calibration error | Accuracy | AUC |
|---|---|---|---|---|---|
| Logistic (served), 42 features | 0.68682 | 0.24677 | 2.11% | **55.56%** | 0.5562 |
| Simulation alone, **0 fitted parameters** | 0.68348 | 0.24530 | 2.98% | 54.55% | 0.5637 |
| **Blend at the pre-registered weight 0.5** | **0.68210** | **0.24456** | **1.28%** | 55.06% | 0.5654 |
| Blend at the searched weight 0.7 | 0.68192 | 0.24449 | 1.15% | 55.69% | 0.5659 |

**2024 — 1,741 scored games**

| | Log loss | Brier | Calibration error | Accuracy | AUC |
|---|---|---|---|---|---|
| Logistic (served), 42 features | 0.68882 | 0.24743 | 3.27% | 56.58% | 0.5682 |
| Simulation alone, **0 fitted parameters** | **0.68081** | **0.24394** | **1.35%** | 55.49% | **0.5803** |
| Blend at the pre-registered weight 0.5 | 0.68239 | 0.24465 | 1.52% | **56.86%** | 0.5762 |

**Both seasons, same sign, both intervals excluding zero:**

| Season | n | Δ log loss @ 0.5 | Paired 95% CI | Δ Brier | Searched weight |
|---|---|---|---|---|---|
| 2024 | 1,741 | **+0.00643** | [+0.00307, +0.00985] | +0.00278 | 1.0 |
| 2025 | 2,363 | **+0.00472** | [+0.00156, +0.00784] | +0.00221 | 0.7 |

For scale, the three rejected feature groups moved log loss by −0.0004, +0.0004
and −0.0004, with every interval spanning zero and the sign flipping between
seasons. This is an order of magnitude larger, holds its sign across both
seasons, and both intervals exclude zero — which is exactly the standard the
rejections were held to and failed.

**The searched weight is unstable and the pre-registered one is not.** 2024
wanted 1.0 — the simulation alone, no logistic at all — and 2025 wanted 0.7. A
weight chosen on one season would have been wrong on the other. The even split
improves on both, which is the better reason to use it than the one it was
chosen for.

**In 2024 the simulation alone beat everything**, including the blend, at a
calibration error of 1.35% against the logistic model's 3.27% and an AUC of
0.5803 against 0.5682. In 2025 it did not — the blend won there. The two
seasons disagree about whether the logistic model is worth any weight at all,
and that disagreement is the reason for a fixed weight rather than a fitted one.

**The weight is pre-registered, not searched.** The grid picks whichever weight
scores best on the games it is scored on, and reporting that number would be
reporting the selection. The headline is an even split, chosen in advance because
it is the obvious a priori answer. The searched weight of 0.7 is worth
0.00018 more — which is to say the selection bought almost nothing, and the
result is not a knife edge. Every weight from 0.1 to 1.0 improves on the
logistic model alone.

**What it does to the model's edge.** A win probability model is only worth the
distance between it and a coin flip, which scores 0.69315. Measured that way:

| | Logistic edge | Blend edge | Multiple |
|---|---|---|---|
| 2024 | 0.00433 | 0.01076 | 2.5× |
| 2025 | 0.00633 | 0.01105 | 1.7× |

That is not a refinement of what the model knows. In 2025 it is close to
doubling it, and in 2024 rather more than doubling it.

**Calibration is the one place the blend beats the simulation in both seasons.**
Expected calibration error against the logistic model:

| | Logistic | Simulation alone | Blend @ 0.5 |
|---|---|---|---|
| 2024 | 3.27% | 1.35% | 1.52% |
| 2025 | 2.11% | **2.98%** | 1.28% |

The blend is better calibrated than the logistic model in both seasons. The
simulation alone is not: in 2025 it is *worse* calibrated while scoring better on
log loss and AUC, which is to say it is sharper rather than better behaved.

That asymmetry is the argument for blending rather than replacing, and it is a
stronger one than "the blend happens to score best". Two models that are wrong in
the same places gain nothing from being averaged. These two are wrong in
different places — the logistic model is over-confident in 2024 and the
simulation is over-confident in 2025 — and a log-odds average cancels part of
each. BACKTEST_PLAN.md § Phase 2A says reliability wins when metrics disagree;
they disagree for the simulation alone and agree for the blend, which is what
makes the blend the result rather than the simulation, despite 2024 preferring
the simulation on log loss.

### Why a parameter-free model beat a fitted one

The simulation has nothing to fit. Each side's expected runs are the classic
multiplicative combination of own scoring rate, opponent's rate of allowing and
the league rate, all as-of and shrunk by existing rules. What it adds is not
information — it is **structure**:

* **Runs are overdispersed and the model now says so.** Measured over 16,314
  nine-inning team-games: mean 4.463, variance 10.592. Refitted inside each
  backtest it comes out at 2.18 in the 2025 window and 2.20 in the 2024 one, so
  it is a property of the sport rather than of a sample. A logistic regression on
  rate differences has no way to express that the same run differential means
  different things at different scoring levels. A negative binomial does.
* **The scoring floor is a hard zero.** Nothing can score −1 runs, and the
  asymmetry that creates near the tails is exactly where win probability is
  decided. A linear model in log-odds space cannot represent it.
* **The innings structure is real.** The home side bats in the ninth only when it
  needs to. That is a genuine, mechanical piece of home advantage which the
  simulation gets for free and a coefficient can only approximate.

The two are complementary rather than competing, which is why the blend beats
both: the logistic model knows about starters, bullpens, rest and travel, and the
simulation knows the shape of a baseball game.

### Rates the simulation was never fitted to

A run distribution and an innings structure imply a great deal more than a win
probability, and none of it was tuned. Two evenly matched teams at the league
scoring rate, 200,000 draws, against 8,934 regular-season games in this
database:

| | Simulated | Observed | |
|---|---|---|---|
| Extra innings | 9.8% | 8.6% | over |
| One-run games | 26.7% | 28.1% | under |
| Home team shut out | 5.3% | 5.9% | under |
| Away team shut out | 5.8% | 7.1% | under |
| Home win rate | 51.6% | 52.7% | under |

Read this for order of magnitude and direction, not for agreement to the decimal.
The simulated column is two *identical* teams; the observed column is a league
with real home advantage and a real spread of team strength, so the two are not
strictly like for like — the observed away-shutout rate is higher than the
home-shutout rate precisely because of an asymmetry the equal-teams simulation
does not contain.

What the table does establish is that nothing is wildly wrong. Every rate lands
within about a point and a half of a quantity no part of the model was pointed
at, and the residual gap in home win rate — 51.6% against 52.7% — is genuine home
advantage that this model's inputs do not carry. There are tests on the first
three.

The stronger version of this check aggregates the simulated rates over the actual
slate rather than a synthetic even matchup. That needs a full walk-forward to
produce and is a follow-up, not a blocker.

### Two bugs the structure created, and what caught them

Modelling the innings rather than the game buys the realism above at the price of
two failure modes a single whole-game draw cannot have. Both were live, both
biased every game on every slate, and neither would have shown up in a win-rate
sanity check.

**The home rate was discounted twice.** `home_mean` is a per-game rate measured
from box scores in which the home side had *already* skipped the ninth whenever
it led. Feeding that in and then skipping the ninth again applied the discount a
second time: simulated home teams scored 4.36 against an input of 4.46. The fix
is exact rather than a fudge — expected home runs are `r·(8 + P(needs ninth))`,
so P is measured on a first pass and `r` follows. Caught by asserting that the
simulation reproduces its own input, which is a cheap test to write and was not
there until it failed.

**The innings split gave the home team less variance.** A negative binomial adds
only when both draws share `p`, and `p = size/(size + mean)`. Splitting the mean
across eight innings plus a conditional ninth while reusing the game-level size
therefore does not reconstruct the game-level distribution. The home side came
out with **11.7% less variance at an identical mean** — a distributional edge
handed out by which dugout a team occupied, worth more on close games than most
features are worth at all. Scaling the size with the mean holds `p` fixed and
closes the gap to 0.21%. Caught by code review, not by a test; the test exists
now, and so does one asserting the unscaled version really would have been wrong,
so a silent reversion cannot pass.

The second is the more interesting failure. The first shifted a mean, which is
the kind of thing a summary statistic catches. The second left every mean correct
and changed only the shape, which no mean-based check anywhere in this repository
would ever have noticed.

### What is not yet established

* **Two seasons, not five.** Both agree, but the optimal weight moved between
  them, and a third season would say whether 0.5 is genuinely the stable choice
  or merely between the two answers so far.
* ~~**The run model is deliberately crude.**~~ Followed, and it closed: the park
  and the named starter were both built and both measured. Neither improves the
  win prediction in either season — see *The run model's park and starting
  pitcher* below. The run model stays crude because the refined versions are not
  better, which is a different statement from never having tried.
* ~~**Nothing is served yet.**~~ Closed: the blend is what the product now
  shows. See *Serving the blend* below for the before-and-after that promotion
  required, and for the one place the served path genuinely differs from the
  path measured here.

---

## The run model's park and starting pitcher: measured, and rejected

The fourth negative result, and the first one aimed at the *run* model rather
than the feature set. The section above named this the clearest remaining lead
in the repository: the run model is two teams' season rates and nothing else,
when the feature layer already knows where the game is played and who is
throwing it. Both were built. Neither earns a place.

Reproduce with:

```
python -m app.cli run-model-check --seasons 2024,2025 --start 2025-04-01
python -m app.cli run-model-check --seasons 2024
```

Four variants, scored on identical games with identical seeds — the only thing
that varies is the model. Δ is against the **base run model**, not against the
logistic one: a refinement has to beat the crude version it refines.

**2025, 2,363 games** (park applied on 93.3%, pitching on 94.0%):

| Run model | Log loss | Brier | Calibration error | Accuracy | Δ vs base, paired 95% CI |
|---|---|---|---|---|---|
| **base** (incumbent) | **0.68348** | **0.24530** | 2.98% | 54.55% | — |
| park | 0.68366 | 0.24537 | **2.81%** | 54.51% | −0.000176 [−0.00047, +0.00011] |
| pitching | 0.68466 | 0.24586 | 3.57% | 54.21% | −0.00118 [−0.00483, +0.00265] |
| park+pitching | 0.68463 | 0.24584 | 3.85% | 54.08% | −0.00115 [−0.00485, +0.00276] |

**2024, 1,741 games** (park 99.8%, pitching 94.0%):

| Run model | Log loss | Brier | Calibration error | Accuracy | Δ vs base, paired 95% CI |
|---|---|---|---|---|---|
| **base** (incumbent) | 0.68081 | 0.24394 | 1.35% | 55.49% | — |
| park | **0.68080** | **0.24393** | **1.23%** | **56.29%** | +0.000008 [−0.00033, +0.00035] |
| pitching | 0.67973 | 0.24344 | 1.99% | 55.31% | +0.00108 [−0.00329, +0.00523] |
| park+pitching | 0.67985 | 0.24349 | 1.46% | 55.54% | +0.00096 [−0.00335, +0.00520] |

Every interval spans zero in both seasons, and **the point estimates flip sign
between them** — pitching is nominally +0.0011 in 2024 and −0.0012 in 2025. That
is the disqualifier this repository already committed to. The simulation itself
was believed because it held its sign across both seasons and both intervals
excluded zero; this does neither. Coverage is not the excuse either, at 93–99.8%.

Blended with the logistic model at the pre-registered 0.5, every variant lands
where the base already was — 2025 Δ vs logistic +0.0047 for base against +0.0046
for both refined versions, 2024 +0.0064 against +0.0069. The refinements neither
add to the merged result nor damage it.

### The two refinements fail for opposite reasons

Their intervals are not the same width, and that is the finding rather than an
artefact. Measured per game on the 2025 slate, against the base model's own
probability for the same game on the same seed:

| Refinement | median \|Δp\| | max \|Δp\| | games moved >0.01 | side flipped |
|---|---|---|---|---|
| park | 0.0022 | 0.0144 | 1.0% | 1.3% |
| pitching | 0.0298 | 0.1880 | 83.0% | **17.8%** |

**The park is inert on this target by construction, not by measurement.** A park
factor multiplies both teams' expected runs by the same scalar. It moves the
expected *total* and leaves the *margin* almost exactly where it was, and a win
probability is a margin question. Even Coors Field at 1.27 — the largest factor
in the database — never moves a win probability by more than 1.5 points, and the
paired interval is ±0.0003 because there is almost nothing there to measure. This
is not a null result that a larger sample would overturn. It is the arithmetic of
applying one scalar to both sides.

That has a corollary worth keeping: the park work is not wasted, it is aimed at
the wrong target. `ParkFactors` is the input a **totals** model would need, and
the run model already emits a full distribution over total runs. Nothing in this
repository scores totals yet.

**The starter is the opposite case: it changed its mind, and was no better for
it.** The pitching multiplier acts asymmetrically — each team's starter suppresses
the *opponent's* runs — so it does move the margin, and it moves it hard. It
shifts 83% of games by more than a point of win probability and **flips which
team it favours on 17.8% of them, nearly one game in five.** That is a large
change of opinion, and it bought a log-loss delta indistinguishable from zero in
both seasons. The value of that measurement is precisely that the change was
large: this is not "the refinement did nothing", it is "the refinement disagreed
with the base model about one game in five and was not the more accurate of the
two."

The reason is the identity the split was built on. `R_team = s·R_sp + (1−s)·R_pen`
holds within one window, so replacing the average starter with the named one is a
genuine redistribution of a fixed total — and the team's own season rate already
contains that starter's contribution to it. Over a season each rotation slot comes
up about the same number of times, so the team rate is close to an average over
the same pitchers the split is redistributing.

### What this null does and does not cover

Being honest about the power, because the two refinements are not equally settled:

* **Park: settled.** Half-width ±0.0003 in both seasons. An effect this test
  could not see would have to be smaller than a third of a thousandth of a nat,
  and the per-game movement above explains why there is none.
* **Pitching: bounded, not settled.** Half-width ±0.004. For scale, `fs_v1`'s
  entire edge over a coin flip is 0.0063. This rules out a refinement worth more
  than about two thirds of the whole model's edge; it cannot see one worth
  +0.001, which would still be worth having. Two seasons of ~2,000 games is what
  is available, so the honest claim is an upper bound on the effect, not proof of
  its absence.
* **Calibration moved, in one direction only.** Park improves calibration error
  in both seasons (2.98%→2.81%, 1.35%→1.23%) while pitching worsens it in both
  (→3.57%, →1.99%). Neither is a large enough or well-enough-powered move to act
  on, but the signs are at least consistent, which is more than the log loss can
  say.
* **Weather is not in this comparison, and cannot be.** The only weather in the
  database sits on the `games` row, whose `knowledge_time` is first pitch plus
  three and a half hours. Not one final game is knowable before it starts, the
  `weather` table is empty, and the registry marks every weather feature
  `available=False`. A weather feature at T−3h would be reading the game it is
  predicting. This stays UNAVAILABLE until a forecast provider is enabled — the
  leakage rule working, not a gap in the ablation.

### Four rejections in a row, and what changed about the fourth

The first three rejections were all the same shape: a group of team-level
features, redundant with team strength. This one is not. It is a refinement of a
different model, on a different quantity, and it still lands in the same place —
which sharpens the earlier diagnosis rather than repeating it. The ceiling is not
where the *columns* run out. Adding structure to the run model reaches it just as
quickly as adding columns to the feature set did.

The run model stays crude. Not because refining it was never tried, but because
the refined versions were measured against it and were not better.

---

## Serving the blend: the promotion, and what it cost to check

The simulation beat the served model on both seasons and was still not served.
That gap was deliberate — §4 requires ensemble weights fitted on out-of-sample
predictions only, and a measurement is not a promotion — but leaving it open
meant the best thing in the repository was not the thing on the screen. It is
now. The served probability is

    logit(p) = (1 − w)·logit(logistic) + w·logit(simulation),   w = 0.5

Reproduce with:

```
python -m app.cli simulate-check --seasons 2024,2025 --start 2025-04-01 --asof-dispersion
python -m app.cli simulate-check --seasons 2024 --asof-dispersion
```

`--asof-dispersion` is the difference between measuring the model and measuring
*the product*. See below.

**2025, 2,363 games:**

| | Log loss | Brier | Calibration error | Accuracy |
|---|---|---|---|---|
| logistic (previous incumbent) | 0.68682 | 0.24677 | 2.11% | **55.56%** |
| simulation alone | 0.68362 | 0.24536 | 2.68% | 54.38% |
| **blend at 0.5 — served** | **0.68219** | **0.24461** | **1.13%** | 55.18% |

Δ log loss **+0.004622**, paired 95% CI [+0.00149, +0.00772]. Brier +0.002164
[+0.00064, +0.00365].

**2024, 1,741 games:**

| | Log loss | Brier | Calibration error | Accuracy |
|---|---|---|---|---|
| logistic (previous incumbent) | 0.68882 | 0.24743 | 3.27% | 56.58% |
| simulation alone | **0.68066** | **0.24386** | 1.43% | 55.94% |
| **blend at 0.5 — served** | 0.68230 | 0.24460 | 1.47% | **56.92%** |

Δ log loss **+0.006523**, paired 95% CI [+0.00317, +0.00998]. Brier +0.002832
[+0.00137, +0.00433].

Same sign both seasons, both intervals excluding zero, on both metrics. That is
the standard this repository set for itself when it believed the simulation, and
the blend meets it.

**Calibration is the gain that holds everywhere.** 3.27% → 1.47% and 2.11% →
1.13%; roughly halved in both seasons. Accuracy moves in opposite directions
(+0.34pp and −0.38pp) and nothing is selected on it, which is the correct
treatment of a metric that discards the probability it is derived from.

### Why the served weight is 0.5 when neither season chose 0.5

The grid picks **1.0 on 2024 and 0.7 on 2025**. Those are not the same answer,
and neither is the one being served.

That is the argument, not an embarrassment. A weight chosen by scoring a grid on
the games it is then evaluated on is fitted on the evaluation set, and this pair
of results shows why it matters here rather than in principle: on 2024 the
argmax discards the logistic model **entirely**, and on 2025 it keeps almost a
third of it. A weight that swings that far between two adjacent seasons is
estimated far too noisily to serve, and 0.5 is the obvious a priori split rather
than anything that won a search.

What that costs is small and measurable. On 2025 the pre-registered weight gives
up 0.00016 of log loss against the argmax — the grid is nearly flat between 0.4
and 0.7. On 2024 it gives up 0.00164, which is real but is the price of not
selecting on the test set.

| Weight on the simulation | 0.0 | 0.3 | 0.4 | **0.5** | 0.7 | 1.0 |
|---|---|---|---|---|---|---|
| 2025 log loss | 0.68682 | 0.68332 | 0.68264 | **0.68219** | *0.68204* | 0.68362 |

### Measuring the product rather than the model

The two-season result above was produced with dispersion fitted **once on the
training side**. Serving cannot do that — at serving time there is no training
side, only a moment — so the run dispersion is re-fitted as-of each slate. Those
are two different numbers, and quietly assuming they are interchangeable would
have made the headline describe a model nobody runs.

Measured rather than assumed, the difference is nil:

| Season | Training-side fit | As-of fit | Moved by |
|---|---|---|---|
| 2024 | +0.006431 | +0.006523 | +0.000092 |
| 2025 | +0.004717 | +0.004622 | −0.000095 |

Two ten-thousandths, in opposite directions. The serving-time fit is not quietly
a different model.

The fit uses **all history rather than the season in progress**, and that is a
correction to the first version of this change rather than a preference. Season
-restricted, the parameter is 2.201 in 2024 and 2.179 in 2025 — it does not move
— while the restriction would have withheld the simulation from every card in
the opening fortnight of a season, which is precisely when a reader has least
other information. It is a shape parameter, not a rate.

### What the promotion does not do

* **It does not blend against a number that is not there.** A game that cannot
  be simulated — a team without enough games on record — serves the logistic
  model alone and records why in `feature_snapshot["blend"]`. The simulation key
  is *absent* from `component_probs` rather than null, so an absence can never
  render as a probability.
* **It does not re-simulate on read.** The Monte Carlo result is persisted per
  prediction and the Simulation tab reads it back. Re-running it inside a GET
  would be a second opinion on the same game — seeded identically, so usually
  the same number, but not necessarily the one that was served.
* **It does not change the feature set.** `fs_v1` is still what the logistic
  half is fitted on, and the four rejected groups are still rejected.
* **It is still two seasons.** The blend now has four intervals across two
  seasons rather than two, and every one excludes zero. It does not have a third
  season, and the optimal weight moving between the two it does have is the
  clearest single argument for getting one.

---

## Phase 2A: what changes, and what does not

The calibrated logistic regression **remains the baseline and remains what is
served** until something beats it on out-of-sample log loss and Brier score.
That is not deference to the incumbent; it is the same rule that has already
rejected one challenger on this repository's own evidence.

### Stacking, not voting

When the additional models exist, they combine through a **stacked meta-model
trained only on out-of-fold predictions** — each base model's prediction for a
game comes from a fold that did not contain that game, and the meta-model is
fit only on predictions from *earlier* walk-forward folds. Majority voting is
explicitly not used: it discards probability magnitude, which is the entire
output this system is judged on.

The final ensemble is calibrated **separately**, after stacking, on a
validation slice later than the meta-model's training folds.

### Comparison protocol

Every model is scored on the *same* out-of-sample games, with the same
training window, validation slice and test window at every step:

| Model | Status |
|---|---|
| Calibrated logistic | Baseline, currently served |
| Gradient boosting | Built; **measured and rejected** — see above |
| Negative-binomial run model | Not built |
| Starter + bullpen innings allocation | Not built |
| Monte Carlo simulation | Not built |
| Elo with starter adjustment | Elo exists as a reference signal; starter adjustment not built |
| Stacked ensemble | Not built |

Nothing in that table is claimed to improve anything until the walk-forward
proves it. The one row that has been measured says the opposite of what was
hoped, and it is recorded rather than retried until it agreed.
