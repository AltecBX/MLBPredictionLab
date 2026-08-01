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
