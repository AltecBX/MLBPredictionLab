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

* Nightly walk-forward refit. A new version is registered every run. The same
  configuration (same `C`, same feature set) refitted on newer games is a
  refresh and is activated; a changed `C` or feature set is a challenger and is
  scored against the active model on the same walk-forward games, paired, with
  the incumbent on the columns it registered — and activated only if the
  interval excludes zero (`app/modeling/promotion.py`). A tie on `C` alone,
  with the feature set unchanged, is a refresh at the incumbent's `C`: the
  first retrain after the spring-training correction held on exactly such a
  tie (grid 0.001, active 0.003) and left the model fitted on the old features
  served, which is the freeze the gate exists to prevent.
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
2. **A market baseline.** ~~Nothing here has ever been compared against a
   de-vigged closing line. Without one there is no way to know whether 0.6868 is
   close to the achievable floor or a long way above it — and that is the single
   most useful unknown left. It needs a licensed odds provider.~~
   **Answered, and it did not need one** — see *The market baseline, answered
   without a market feed* below. Forty-two published seasons of moneyline prices
   give an achievable floor of **0.679–0.681**, so 0.6868 is close to it rather
   than a long way above: the entire remaining gap is smaller than this model's
   own margin over a coin flip. A live odds feed is still wanted for the Market
   tab, but no longer for this question.
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

## Forecast weather: measured, and rejected on data better than production's

The sixth negative result, and the one the park measurement predicted.

Weather was recorded as blocked on a paid provider. It was not: Open-Meteo
serves forecasts without an API key, and serves an archive of past forecasts as
well, which is what makes a weather feature measurable rather than only
servable. 10,297 forecasts at first pitch, 99.9% coverage of 2023–25.

**The hypothesis was shaped by the park result rather than ignoring it.** A
condition both teams share moves the total and not the margin — that is why the
park factor is inert on a win probability by construction. Temperature, density
and wind are shared conditions, so the group was built around the one part that
is *not* shared: two pitching staffs are not equally exposed to carrying air,
and that gap is asymmetric. `wx_carry_x_flyball_diff` is that interaction; the
two absolute features are included so the ablation could say whether shared
conditions do anything at all, instead of the claim resting on the park
argument alone.

| Season | n | fs_v1 | fs_v6 | Δ log loss | Paired 95% CI | Verdict |
|---|---|---|---|---|---|---|
| 2024 | 1,741 | 0.68383 | 0.68607 | −0.002244 | [−0.00537, +0.00088] | NO_EFFECT |
| **2025** | 2,363 | **0.68682** | 0.68697 | **−0.000151** | **[−0.00027, −0.00003]** | **REJECT** |

Negative in both seasons, and on the larger one the interval excludes zero: the
group measurably hurts. Both arms drew the same regularisation in both seasons —
C=0.03 on 2024 and C=0.001 on 2025 — so unlike the bullpen group there is no
confound to unpick. This is the features.

### Why this null is stronger than the others

Backfilled forecasts come from an archive that does not expose which model run
produced each value. A reading for a 7pm first pitch may come from a run
initialised that afternoon — later, and therefore more accurate, than the
forecast that actually existed at T−3h. Backfilled weather is **optimistically
biased**, and the consequence is asymmetric:

* had the group succeeded, the effect size would have been an upper bound
  needing live confirmation;
* it failed, which means it failed **with better information than production
  would ever have had**.

That is the most conclusive rejection in this document. The others leave open
"perhaps with better data"; this one does not.

The interaction did not rescue the shared part. Two staffs' fly-ball exposure
gap is apparently too small a lever to recover what a condition both sides play
in cannot say about which of them wins.

**What the work is still worth.** `ParkFactors` and the weather pipeline are
both aimed at the wrong target rather than wasted, and they are aimed at the
*same* wrong target. Air density, carry and park factor are all inputs to how
many runs get scored, and the run model already emits a full distribution over
total runs that nothing currently scores. A totals model is where six rejections
have been quietly pointing.

---

## Individual bullpen availability: measured, rejected, and a warning about the method

The fifth negative result. The hypothesis was the best-motivated one left, and
the way it failed matters more than that it failed.

**The hypothesis.** Every bullpen feature in `fs_v1` is a team total — relief
innings over three days, a fatigue index, a thirty-day relief ERA. A pen that
threw four innings yesterday across four pitchers and one that threw four
innings out of its two best arms produce **identical values on every one of
them**, and are in completely different states tonight. So this group is not
another season aggregate over the same population, which is what the first four
rejections all were. It is a per-pitcher constraint the aggregate provably
cannot express: how many relievers can pitch, how good the ones who can are, and
whether the best arm is among them.

Reproduce with:

```
python -m app.cli compare-feature-sets --baseline fs_v1 --candidate fs_v5 --seasons 2024
python -m app.cli compare-feature-sets --baseline fs_v1 --candidate fs_v5 \
    --seasons 2024,2025 --start 2025-04-01
# and the same two with --C 0.01 and --C 0.03
```

### The result depends on a constant nobody has a reason to set

| Season | Regularisation | fs_v1 | fs_v5 | Δ log loss | Paired 95% CI | Verdict |
|---|---|---|---|---|---|---|
| 2024 | selected per set | 0.68383 | 0.68240 | +0.001434 | [−0.000002, +0.002927] | NO_EFFECT |
| 2024 | pinned 0.03 | 0.68383 | 0.68325 | +0.000578 | [−0.000683, +0.001870] | NO_EFFECT |
| **2024** | **pinned 0.01** | 0.68433 | 0.68240 | **+0.001932** | **[+0.000350, +0.003576]** | **ADOPT** |
| 2025 | selected per set | 0.68682 | 0.68683 | −0.000013 | [−0.000384, +0.000368] | NO_EFFECT |
| **2025** | **pinned 0.03** | 0.69103 | 0.69165 | **−0.000621** | **[−0.001178, −0.000071]** | **REJECT** |
| 2025 | pinned 0.01 | 0.69021 | 0.69060 | −0.000395 | [−0.000896, +0.000098] | NO_EFFECT |

Coverage was 100% on all three features in both seasons, so nothing here is a
missing-data artefact.

Six comparisons. One says adopt, one says reject, four say nothing, **and the
sign flips between seasons at every single regularisation setting**. The one
ADOPT does not replicate at its own C: run the identical C=0.01 comparison on
the larger season and the difference is negative.

The group is rejected. It stays registered with `available=False` and the
measurement attached.

### The part worth keeping is about the protocol

At C=0.01 on 2024 this group clears the bar outright — interval excluding zero,
verdict ADOPT, three features into the model. At C=0.03, same season, same
features, same games, same walk-forward, it is worth a third of that and nothing
is distinguishable. **Nobody changed a feature between those two rows.**

Two things follow, and both are about the machinery rather than about bullpens.

**Per-set regularisation selection can manufacture an effect.** The default
protocol gives each feature set its own C, chosen walk-forward. On 2024 that
handed the baseline C=0.03 and the candidate C=0.01, and the +0.001434 it
reported decomposes into two pieces that are not the same thing:

* the baseline is 0.00050 *worse* at C=0.01 than at its own selected C, and
* the candidate is 0.00085 *better* at C=0.01 than at C=0.03.

Neither is a feature effect. Letting each set choose its own regulariser is
defensible — it is what the trainer does — but it means a head-to-head is not
purely a comparison of feature sets, and on a difference this small that is the
whole result.

**A single comparison is not evidence here.** The honest reading of this table
is that the machinery has enough freedom in it to reach ADOPT or REJECT for the
same three features by choosing a season and a constant. Every previous
rejection in this document ran the default protocol on two seasons and stopped;
this one ran six configurations because the first result had the baseline and
the candidate on different regularisation, and that turned out to matter more
than the features did.

Nothing above invalidates the four earlier rejections — they were all null in
the same direction on both seasons, which is a much more stable finding than a
sign that flips. It does mean that any *future* group landing near the
significance boundary should be run at pinned regularisation across both
seasons before anybody believes it, and that a group whose verdict moves with C
has not been measured, only sampled.

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

## The market baseline, answered without a market feed

MODELING_PLAN.md has carried this as the single most useful unknown left, and
gated it on a licensed odds provider:

> Nothing here has ever been compared against a de-vigged closing line. Without
> one there is no way to know whether 0.6868 is close to the achievable floor or
> a long way above it.

It does not need one. Forty-two seasons of MLB moneylines, 1977–2018, are
published in [Bouchard (2019), Harvard](https://dash.harvard.edu/bitstreams/24950429-b1b7-4372-a029-1b68de1872e3/download),
and the price *distribution* is enough to compute what the closing line itself
scores.

**What the market knows.** Favourites win 55.92% (home favourites 57.04%, away
53.30%). Favourite implied probability has mean .584 and **range .515–.737** —
over forty-two seasons the market never priced an MLB game past about 74%. Its
calibration slope sits in [0.963, 1.05], so there is no exploitable bias in it.

**What that implies is achievable**, de-vigging at 2.5–3.5% and treating the
market as the true probability:

| | Best achievable | Best public model (538, 2016–19, 9.7k games) |
|---|---|---|
| Log loss | **0.679–0.681** | **0.6769** |
| Brier | 0.243–0.244 | 0.2420 |
| Accuracy | 56.4–57.0% | 56.96% |

**The best public model is already sitting on the ceiling.** There is
essentially nothing between a good model and the closing line.

### Where this repository actually stands

| Season | fs_v1 | Edge over a coin flip | Gap to a 0.681 ceiling |
|---|---|---|---|
| 2024 | 0.68383 | 0.0093 | +0.0028 |
| 2025 | 0.68682 | 0.0063 | +0.0058 |

So the honest position is: **this model holds somewhere between a third and
nine tenths of the available edge**, and the whole remaining distance to a
market that has never been beaten is smaller than fs_v1's own margin over a coin
flip. Two seasons of games resolve about ±0.0009. The remaining headroom is a
handful of multiples of the noise floor.

That reframes every null in this document. Six groups were diagnosed as
redundant with team strength; the seventh could not be resolved at this sample
size. Both readings are consistent with a simpler one: **the measurable space
between here and the ceiling is only a few thousandths of a nat wide**, and this
repository's instrument bottoms out at about one.

### A prior correction

An earlier working note in this session put the ceiling at 0.65 and the headroom
at 0.032 nats. That was wrong by roughly a factor of five. The real figure is
0.679–0.681, and the headroom is ~0.006. Anything that claims a large gap is
worth re-deriving before it is acted on.

### The leakage test this gives for free

Every published claim above 60% accuracy that could be inspected has an
identifiable defect, and the failure mode is one this repository already guards:

* **Cui (2020), 61.77%** — proven at code level. `pandas .rolling(7)` is
  right-aligned and *includes the current row*, and there is no `.shift(1)`
  anywhere in the repository. The covariate for a game contains that team's H,
  AB, BB, TB and SF **from the game being predicted**. The tells corroborate it:
  OBP carries by far the largest coefficient, April accuracy is already 60.57%,
  and the model has no current-season starting-pitcher data at all yet beats 538,
  which does model starters.
* **Allen & Savala (2025), 62.94%** — no code, but the output is impossible. It
  put 181 games at ≥85% home win probability where 538 put **zero** above 85%
  and the market's forty-two-year maximum is 73.7% *including* vig. Its own
  betting test loses 51% of money wagered; the positive return comes from
  grid-searching 400 cutoff pairs on the test set it reports.

Reverse-engineering a score into implied average confidence makes the shape of
the impossibility clear:

| Score | Implied average confidence |
|---|---|
| Brier 0.250 (coin flip) | 50.0% |
| Brier 0.242 (538) | 58.9% |
| Brier 0.225 (Allen & Savala) | 65.7% |

The market averages ~56.5% and its all-time extreme is ~74%. A model averaging
65.7% is claiming knowledge that does not exist in baseball.

**So: a backtest materially better than the market is evidence of leakage, not
skill.** In this literature that rule has a 100% hit rate, and the two culprits
are exactly the two this repository is built against — a rolling window that
includes the current game, and a season aggregate with no as-of date.

### What this says about the remaining candidate signals

Ranked by measured effect on the *winner*, which is not the same ranking as
effect on runs:

| Signal | Effect on the winner |
|---|---|
| Starting pitcher identity | Dominant. The market quotes moneylines *listed-pitcher* and voids the bet if a starter changes — its own statement of the top lever |
| Team talent | Base rate. Elo alone scores 54.4%, CI [.528, .560], which contains the 53.9% home base rate |
| Home field | +24 Elo ≈ +3.4pp |
| Rest and travel | <1pp (+2.3 Elo per rest day, capped at three) |
| Park, weather, umpire, framing | **≈ 0** — all apply to both teams |

Catcher framing is worth about **one win per season**, 0.006 wins per game, and
the spread has compressed by ~4 runs per 900 innings since 2014. Umpire effect on
run environment is real and forecastable (year-to-year r = 0.61, extremes ±half a
run per game) and **is a totals signal, not a sides signal** — which is exactly
what the park measurement in this document predicted for anything shared by both
teams.

There is also a leakage answer on umpires, measured rather than assumed. Probing
the MLB schedule endpoint with `hydrate=officials` on 2026-08-02: games starting
within about three hours carried four named officials; every game later that day
and every game on the two following days carried **none**. Umpire assignments
publish roughly two to three hours before first pitch, same day only. So
`knowledge_time` for an umpire is about first pitch minus three hours — after
most prediction times — and back-filling one from a completed boxscore into a
"pregame" feature is precisely the leak LEAKAGE_PREVENTION.md exists to stop.

---

## Roster availability: measured, not adopted, and the first sign that holds

The seventh candidate group, and the first one that does not flip. It is still
not adopted, and the reason it is not adopted is more interesting than the
result.

Reproduce with:

```
python -m app.cli compare-feature-sets --baseline fs_v1 --candidate fs_v7 \
    --seasons 2024 --C 0.03
python -m app.cli compare-feature-sets --baseline fs_v1 --candidate fs_v7 \
    --seasons 2024,2025 --start 2025-04-01 --C 0.03
```

and again with `--C 0.01`, because a group whose verdict moves with the
regularisation constant has not been measured — the lesson the bullpen group
paid for.

| Season | C | n | fs_v1 | fs_v7 | Δ log loss | Paired 95% CI | Δ Brier |
|---|---|---|---|---|---|---|---|
| 2024 | 0.03 | 1,741 | 0.68383 | **0.68374** | **+0.000091** | [−0.00145, +0.00167] | +0.000050 |
| 2024 | 0.01 | 1,741 | 0.68433 | **0.68429** | **+0.000034** | [−0.00130, +0.00142] | +0.000029 |
| 2025 | 0.03 | 2,363 | 0.69103 | **0.69065** | **+0.000385** | [−0.00048, +0.00129] | +0.000156 |
| 2025 | 0.01 | 2,363 | 0.69021 | **0.68990** | **+0.000310** | [−0.00055, +0.00124] | +0.000134 |

Coverage is 100% on both features in both seasons, so nothing here is a
coverage artefact. **Positive in all four on log loss and on Brier**, and AUC
improves in both 2025 arms (0.5355 → 0.5371). Every previous candidate either
flipped sign between seasons — the bullpen group did it at every C — or was
negative in both, as weather was. This one does neither.

It is still not adoption, because every interval spans zero.

### The honest verdict is about the sample, not the feature

Pooling the two seasons by inverse variance at C=0.03 gives **+0.000314, CI
[−0.00046, +0.00108]** over 4,104 games. Asking what it would take for that
interval to exclude zero:

| Arm | Point estimate | Games needed |
|---|---|---|
| 2025, C=0.03 | +0.000385 | ~12,400 (5 seasons) |
| 2025, C=0.01 | +0.000310 | ~19,700 (8 seasons) |
| **Pooled, C=0.03** | **+0.000314** | **~24,600 (10 seasons)** |
| Pooled, C=0.01 | +0.000227 | ~44,700 (18 seasons) |

So the finding is not "measured and absent". It is **smaller than two seasons of
baseball can see** — an effect worth roughly a twentieth of what the entire model
is worth over a coin flip (0.0063), in a sample whose resolution is about
±0.0009. Those are different sentences and the second one is the true one.

That distinction matters for what happens next. Six previous nulls were all
diagnosed the same way — redundant with team strength, a ceiling reached by
*information* — and the correct response to those was to stop adding columns of
that shape. This null does not license that inference. It licenses only the
statement that the repository's measuring instrument bottoms out around a
thousandth of a nat, and that anything genuinely smaller than that cannot be
adjudicated here at all.

### Why this group was expected to be different, and what the ablation said

Every group before it **decomposes** team strength, and a season rate is a
sufficient statistic for its own rearrangement. The starting-pitcher split is
the proof: `R_team = s·R_sp + (1−s)·R_pen` is an identity, it disagreed with the
base model about one game in five, and it was not the more accurate of the two.

Availability performs a different operation. It reports that the season rate is
**stale** — accumulated by a roster including somebody who is not playing
tonight — which cannot be inside the rate, because the rate contains his
contribution on account of his having made it. And it makes a distinguishing
prediction: a decomposition averages back to the team rate over a season, since
each rotation slot comes up about equally often, which is exactly why the
pitching split was worth nothing in aggregate. An availability loss does not
average back.

The natural competing explanation was that `fs_v1` already knows about injuries
through their *effects* — `off_runs_per_game_w30_diff` and
`off_form_delta_w14_diff` absorb a lost cleanup hitter within a fortnight — which
would make this group REDUNDANT rather than novel. The 2024 ablation says
otherwise:

| Group | features | Δ on removal | Group alone, vs a coin flip | Reading |
|---|---|---|---|---|
| team_strength | 8 | +0.000863 | **+0.002113** | REDUNDANT |
| starting_pitcher | 13 | +0.002607 | −0.003226 | UNIQUE SIGNAL |
| **roster_availability** | **2** | **+0.000120** | **−0.009791** | **NO SIGNAL** |

with one caveat that must travel with that column. Predicting the home base rate
alone is worth about +0.0024 against a coin flip; `team_strength` solo scores
+0.0021, which is the intercept and no more, and every other group scores
negative. In this configuration the solo column is dominated by how much a small
fitted model overfits, so its *level* should not be read as standalone
information. Its *ranking* can be, and availability is last of the ten testable
groups.

### What is measured about the data, and what is assumed

`IL_RECENCY_DAYS = 28` is a fitted constant, and the distinction that keeps this
honest is **what it was fitted against**. The `injuries` table is an event log —
an `IL` row superseded eventually by an `ACTIVE` one — and roughly 1,700 stints
across three seasons never received their closing row. Taken at face value it
marks 1,095 players unavailable on a single midsummer day.

So the flag was checked against what it claims. Eight probe dates across 2024
and 2025, batters with at least 20 PA in the previous 30 days, asking whether
the player actually appeared in the following seven:

| Days since the placement was knowable | players | played within 7 days |
|---|---|---|
| 0–14 | 161 | **22.4%** |
| 15–28 | 40 | 35.0% |
| 46–59 | 8 | 87.5% |
| 71+ | 146 | 91.1% |

A fresh placement more than halves the chance of appearing; past about six weeks
the record carries nothing, because those are the stints whose closing row is
missing. The window is where the signal has gone — and it was chosen against
**absence**, not against the win outcome the group was then scored on. Nothing
about who won any game entered it. That is precisely the failure the bullpen
section documents, avoided deliberately rather than by luck.

---

## XGBoost, LightGBM, Elo and the stack: measured, and the baseline stands

The eighth measurement, and the widest: every model the Phase 2A table still
listed as unbuilt, on identical out-of-sample games. The earlier ensemble
rejection covered one boosted model (sklearn HistGradientBoosting) at one
fixed configuration; this run measures what that left open — XGBoost and
LightGBM each with shallow trees, strong L1+L2, row and column subsampling, a
conservative learning rate, early stopping on the chronological validation
tail, and an eight-configuration grid searched *inside each training fold
only*; per-model calibration with Platt and isotonic both fitted and the
method chosen prequentially on strictly-earlier out-of-fold rows; the Elo
model as a calibrated component; and the stacked logistic meta-model over
prior-fold OOF probabilities. No random cross-validation exists anywhere in
the path.

Reproduce with `python -m app.cli challenger-check --step-days 30`.

**8,134 out-of-sample games, 26 walk-forward steps, 2023–2026:**

| | Log loss | Brier | Calibration error | Accuracy | AUC | Max prob | > market max |
|---|---|---|---|---|---|---|---|
| **Logistic (baseline)** | 0.68477 | 0.24584 | 1.33% | 55.72% | 0.5692 | 0.873 | 0.90% |
| XGBoost | 0.68409 | 0.24549 | **0.77%** | 55.64% | 0.5710 | 0.807 | **0.43%** |
| LightGBM | 0.70948 | 0.24640 | 0.78% | 55.35% | 0.5685 | 1.000 | 1.14% |
| Elo, calibrated | **0.68306** | **0.24498** | 0.71% | **55.79%** | **0.5743** | 0.794 | 0.98% |
| Stack (6,989 games) | 0.68493 | 0.24564 | 0.58% | 55.42% | 0.5714 | 0.923 | 0.99% |

("> market max" is the share of predictions past 73.7%, the market's
forty-two-year maximum implied favourite price.)

**Paired against the baseline, positive favouring the challenger:**

| | Δ log loss | Paired 95% CI | Season signs (23/24/25/26) | Verdict |
|---|---|---|---|---|
| XGBoost | +0.000675 | [−0.00142, +0.00274] | + − + + | NOT PROMOTED |
| LightGBM | −0.024707 | [−0.03865, −0.01295] | − − + + | NOT PROMOTED |
| Elo, calibrated | +0.001712 | [−0.00068, +0.00420] | − + + + | NOT PROMOTED |
| Stack | +0.000007 | [−0.00204, +0.00203] | − − + + | NOT PROMOTED |

Every interval spans zero except LightGBM's, which excludes it on the wrong
side, and no candidate holds its sign across all four seasons. The
pre-registered rule — total log loss improved with the interval excluding
zero, Brier held, calibration not materially worse, positive in every full
season — fails for all four, so nothing is promoted and the served model is
unchanged. The challengers were measured against the logistic model alone;
the *served* model is the logistic–simulation blend, which beats the logistic
alone by +0.005 to +0.007 per season, so the bar for serving is higher still
and nothing here approaches it.

**What the run actually found, model by model.**

* **XGBoost is real competition and still not an improvement.** Better
  calibration (0.77% against 1.33%), better AUC, half the baseline's
  overconfidence rate, and nominally better log loss in three seasons of
  four — but 2024 flips (−0.00188) and the pooled interval comfortably
  contains zero. Its per-fold search also never settled: all eight
  configurations won at least once in 26 folds. A model whose
  hyperparameters churn that much across adjacent windows is fitting noise
  at the margin, which is exactly what the season flip says.
* **LightGBM fails the protocol before it fails the data.** Its pooled
  number is ruined by the earliest 2023 folds, where on a few hundred
  training rows it emitted probabilities at 0.9999+ during the calibration
  passthrough phase — log loss 0.807 on 2023, while Brier barely moves,
  the signature of a handful of catastrophically confident rows. From 2024
  onward it is indistinguishable from neutral. The stack agrees: its fitted
  LightGBM weight is negative and near zero throughout.
* **Calibrated Elo is the finding.** Two numbers per game — a rating
  difference and a home-field constant — recalibrated prequentially, match
  and nominally beat the 42-feature model on every headline metric. The
  paired interval still spans zero and 2023 flips sign, so it is not
  promotable either; what it establishes is the same thing the market
  baseline did from the other side. Most of what fs_v1 knows about a game's
  winner, Elo already knew.
* **The stack has nothing to stack.** With components this correlated and
  this close, the meta-model converges to roughly 0.5 logistic + 0.35 Elo +
  0.15 XGBoost and delivers the baseline's number with the best calibration
  error of the table (0.58%) — a wash on log loss, purchased with 1,145
  fewer scored games spent warming it up.
* **Platt won calibration for all five models.** Isotonic was worse pooled
  for every stream and much worse for the trees (0.706, 0.713 against Platt's
  0.684) — on validation slices this size its flexibility is spent on noise,
  which is what calibration.py's tie-to-Platt rule predicted.

**The lineup split is honestly empty.** Every historical lineup row is
knowable only postgame (first pitch +3h30m, boxscore-derived), so the
"after confirmed lineups" arm contains zero games in every model's report.
It fills only as the pregame poller's archive accumulates; nothing was
inferred from postgame data to fake it.

---

## Streak features: measured, and rejected on three seasons

The ninth candidate group, requested with its own gate attached: raw streak
length was never to enter the model directly, and the processed streak story
— capped lengths, the shrunk historical continuation probability in win
direction, that probability minus the pre-game expectation for the same
historical games, the run differential and opponent quality across the
current streak — was to stay only if it improved out-of-sample log loss or
Brier across multiple seasons. It does the opposite.

Reproduce with:

```
python -m app.cli compare-feature-sets --baseline fs_v1 --candidate fs_v8 \
    --seasons 2024 --C 0.03
python -m app.cli compare-feature-sets --baseline fs_v1 --candidate fs_v8 \
    --seasons 2024,2025 --start 2025-04-01 --C 0.03
python -m app.cli compare-feature-sets --baseline fs_v1 --candidate fs_v8 \
    --seasons 2023,2024,2025,2026 --start 2026-04-01 --C 0.03
# and each again with --C 0.01
```

| Season | C | n | fs_v1 | fs_v8 | Δ log loss | Paired 95% CI | Verdict |
|---|---|---|---|---|---|---|---|
| 2024 | 0.03 | 1,741 | 0.68383 | 0.68261 | +0.001224 | [−0.00091, +0.00332] | NO_EFFECT |
| 2024 | 0.01 | 1,741 | 0.68433 | 0.68231 | +0.002021 | [−0.00019, +0.00421] | NO_EFFECT |
| 2025 | 0.03 | 2,363 | 0.69103 | 0.69099 | +0.000039 | [−0.00113, +0.00116] | NO_EFFECT |
| 2025 | 0.01 | 2,363 | 0.69021 | 0.69057 | −0.000363 | [−0.00154, +0.00076] | NO_EFFECT |
| **2026** | **0.03** | **1,569** | 0.69423 | 0.69675 | **−0.002511** | **[−0.00375, −0.00125]** | **REJECT** |
| **2026** | **0.01** | **1,569** | 0.69360 | 0.69578 | **−0.002179** | **[−0.00335, −0.00104]** | **REJECT** |

Brier agrees everywhere it is distinguishable: −0.001132 [−0.00171, −0.00054]
and −0.000994 [−0.00154, −0.00045] on the 2026 arms.

**The shape of the failure is the finding.** The delta decays monotonically
with the training window: nominally positive when trained on one prior
season, zero at two, and measurably negative at three — at both
regularisations, on both proper scores. That is what noise looks like as the
base model runs out of room to be fooled: with a weak baseline the streak
story happens to correlate with the residual, and as the baseline sharpens
the same six features are pure variance. The 2026 arms are the strongest
negative result in this document after weather — both intervals exclude zero
— and they are the arms with the most history behind both the features and
the model.

**This is also what the adjusted-effect column on the /streaks page shows
reader-facing:** continuation rates hug the pre-game expectation, and the
part of the streak story that is not already team strength is small and
unstable. The section stays — it answers a real reading question honestly —
and the model does not consume it. `fs_v8` remains registered with
`available=False` and this measurement attached.

Coverage caveat recorded with the run: the two history features
(`sk_continue_prob_diff`, `sk_adjusted_effect_diff`) are defined only when a
team enters the game on a streak of two or more — 24–31% of games across the
arms — and are median-imputed elsewhere. The four always-defined features
(capped lengths, streak run differential, streak opponent quality) carry the
group's negative 2026 result on their own coverage.

---

## Multi-season projections: measured, and adopted

The tenth candidate group, and the first to clear the gate. It is also the
first change to the served probability since the simulation was promoted.

### The diagnosis it came from

Every rate in `fs_v1` is season-to-date and shrunk toward the **league**. On
Opening Day a team that outscored its opponents by a run a game last year is
handed the same prior as one that was outscored by a run a game, and a starter
with three seasons of a 28% strikeout rate is handed the league's 22%. The
only feature in the set with any memory across seasons is Elo — and the
challenger run above found that calibrated Elo, two numbers per game, matched
the forty-two-feature model on every headline metric. Read together, those
two facts say the other forty-one features spend the spring relearning what
the previous season already knew.

FEATURE_DICTIONARY.md §1 rule 2 has always said the fix — "prior-season and
three-year baselines are themselves shrunk toward league average before being
used as a prior for the current season" — and nothing had built it.

### What was built

`features/projections.py`: a weighted pool of an entity's recent seasons plus
the season in progress, each season measured against **its own** league rate,
regressed toward the league:

    projection = L_now + Σᵢ wᵢ·(Eᵢ − Lᵢ·Dᵢ) + (E_now − L_now·D_now)
                         ───────────────────────────────────────────
                               Σᵢ wᵢ·Dᵢ + D_now + k

Four difference features, registered as `fs_v9 = fs_v1 + projections`:

| Feature | Pool | Weights on prior seasons | k |
|---|---|---|---|
| `proj_off_rpg_diff` | runs scored per game | 0.6, 0.3 | 80 games |
| `proj_ra_rpg_diff` | runs allowed per game | 0.6, 0.3 | 80 games |
| `proj_sp_k_minus_bb_pct_diff` | starter K% − BB%, starts only | 0.6, 0.35, 0.2 | 300 batters faced |
| `proj_sp_fip_diff` | starter FIP numerator, starts only | 0.6, 0.35, 0.2 | 200 innings |

**None of the constants were fitted on the win outcome.** The weights and the
regression are the published year-to-year persistence of these rates — team
run differential at r ≈ 0.55, a starter's K% and BB% at r ≈ 0.7 and 0.55,
his fielding-independent rate at about 0.5 — written down before the group was
measured. The bullpen section above is why that matters: a constant tuned on
the target it is about to be scored on can manufacture its own verdict.

The same projected rates are also a run-model variant, `projected`: each side's
expected runs from the projections instead of the season-to-date means, in the
same multiplicative combination the base model uses.

### The protocol, and what changed about it

Two things had to change before this could be measured honestly.

* **Six seasons.** The store now holds 2021 through 2026 — 16,600 games,
  14,244 usable regular-season rows — instead of four. A prior-season feature
  cannot be measured on the first season a store contains.
* **A lookback.** A `--seasons` measurement used to load a store cut to exactly
  the seasons being scored, so the earliest of them had no history for any
  prior-season feature to read. The store now carries three seasons before the
  earliest requested one and the dataset builder cuts the scored rows back
  (`features/asof.py`, `LOOKBACK_SEASONS`). This is also what makes a
  measurement look like production, which loads everything.

Everything below is scored on the **same 6,900 games** — every regular-season
game from 2024-04-01 to 2026-09-02, in 30-day walk-forward steps — with
regularisation **pinned** for both arms, at two settings, from each of two
training histories. That is the pinned-C protocol the bullpen group forced,
run four times over rather than once.

Reproduce:

```
python -m app.cli compare-feature-sets --baseline fs_v1 --candidate fs_v9 \
    --seasons 2023,2024,2025,2026 --start 2024-04-01 --C 0.03
python -m app.cli compare-feature-sets --baseline fs_v1 --candidate fs_v9 \
    --seasons 2021,2022,2023,2024,2025,2026 --start 2024-04-01 --C 0.03
# and each again with --C 0.01
python -m app.cli run-model-check --seasons 2021,2022,2023,2024,2025 --start 2025-04-01
python -m app.cli simulate-check --seasons 2021,2022,2023,2024,2025,2026 --start 2024-04-01
```

### The logistic model: fs_v1 against fs_v9

| Trained from | C | fs_v1 | fs_v9 | Δ log loss | Paired 95% CI | Δ Brier |
|---|---|---|---|---|---|---|
| 2023 | 0.03 | 0.68808 | 0.68675 | **+0.001330** | **[+0.00052, +0.00215]** | +0.000644 [+0.00025, +0.00105] |
| 2023 | 0.01 | 0.68750 | 0.68600 | **+0.001500** | **[+0.00070, +0.00234]** | +0.000729 [+0.00034, +0.00114] |
| 2021 | 0.03 | 0.68655 | 0.68555 | **+0.000998** | **[+0.00022, +0.00177]** | +0.000511 [+0.00014, +0.00088] |
| 2021 | 0.01 | 0.68628 | 0.68506 | **+0.001220** | **[+0.00040, +0.00201]** | +0.000614 [+0.00022, +0.00099] |

Four arms, four intervals excluding zero on log loss, four on Brier.
Calibration error is unchanged in every arm (Δ within ±0.0003, intervals
spanning zero). AUC rises in every arm, by 0.005 to 0.007.

**The sign holds in every season of every arm.** Per season, 2023/0.03 arm:
2024 +0.00179 [+0.00044, +0.00320], 2025 +0.00085 [−0.00043, +0.00212], 2026
+0.00136 [−0.00018, +0.00287]. Two of the three single-season intervals span
zero on their own — a season is 2,000–2,400 games and the effect is a
thousandth of a nat — and all twelve season-by-arm point estimates are
positive. Every earlier candidate either flipped sign between seasons or hurt.

**Where in the season.** Δ log loss by month, 2023/0.03 arm, all three seasons
pooled:

| Month | Games | fs_v1 | fs_v9 | Δ |
|---|---|---|---|---|
| April | 1,180 | 0.69496 | 0.69317 | +0.00179 |
| May | 1,239 | 0.69347 | 0.69141 | +0.00206 |
| June | 1,192 | 0.68571 | 0.68504 | +0.00067 |
| July | 1,109 | 0.69361 | 0.69268 | +0.00093 |
| August | 1,251 | 0.67951 | 0.67709 | +0.00242 |
| September | 786 | 0.67918 | 0.67954 | −0.00036 |

April and May are where the hypothesis said the gain would be, and it is
there. It is also in August, which the hypothesis did not predict: a
projection that includes the season in progress is a better-regularised
version of the season-to-date rate all year, not only a better April prior.

### The run model: projected means against the base

The same projections, as the simulation's expected runs. Scored on identical
games with identical seeds, 5,000 draws, dispersion fitted on the training
side (size 3.58, variance/mean 2.25 over 14,688 team-games):

| Season | n | base alone | projected alone | Δ log loss | Paired 95% CI |
|---|---|---|---|---|---|
| 2024 | 2,374 | — | — | +0.001603 | [−0.00235, +0.00563] |
| 2025 | 2,430 | — | — | +0.003616 | [+0.00060, +0.00668] |
| 2026 | 2,096 | — | — | +0.005206 | [+0.00110, +0.00936] |
| **all** | **6,900** | **0.68544** | **0.68204** | **+0.003406** | **[+0.00133, +0.00550]** |

This is the refinement the park and the named starter were not. Those
redistributed a season rate and averaged back to it; this replaces the
season-to-date rate with a better estimate of the same quantity, and the
simulation — which has no other input — moves with it.

### The served number, before and after

The product serves `logit(p) = 0.5·logit(logistic) + 0.5·logit(simulation)`.
Both halves changed. Paired per game on the same 6,900 games, trained from
2021, C pinned at 0.03:

| | Log loss | Brier | Calibration error | Accuracy | AUC | Beyond the market's 73.7% max |
|---|---|---|---|---|---|---|
| **Before**: fs_v1 logistic + base simulation | 0.68334 | 0.24516 | 1.20% | 54.96% | 0.5682 | 0.17% of games |
| **After**: fs_v9 logistic + projected simulation | **0.68135** | **0.24418** | **0.93%** | **55.83%** | **0.5778** | **0.06%** |

Δ log loss **+0.001984 [+0.00078, +0.00322]**; Δ Brier +0.000980
[+0.00040, +0.00159]. By season: 2024 +0.00152 [−0.00080, +0.00387], 2025
+0.00183 [+0.00009, +0.00354], 2026 +0.00269 [+0.00047, +0.00503]. By month:
April +0.0040, May +0.0010, June +0.0021, July +0.0017, August +0.0018,
September +0.0003.

Each half earns its place on top of the other: with fs_v9 already in the
logistic, swapping the simulation from base to projected means is worth
+0.001309 [+0.00027, +0.00234]; with the projected simulation already in the
blend, the logistic's move from fs_v1 to fs_v9 is worth the rest.

**The weight stays at 0.5.** The grid over the blend weight, with the projected
simulation, is 0.68184 at 0.5, 0.68148 at 0.6, 0.68132 at 0.7 and 0.68204 at
1.0. Every season that has been measured has preferred a weight above 0.5, and
this is the third. The pre-registered weight is kept for the same reason it was
kept the first time: a weight chosen by scoring a grid on the games it is then
evaluated on is fitted on the evaluation set, and the gap — 0.0005 — is small.
It is now the most consistent unmeasured lead in the document, and the right
way to act on it is a pre-registered move to 0.6 scored on a season none of
this has seen.

**Against the ceiling.** The market baseline section puts the achievable floor
at 0.679–0.681. The served number on these three seasons was 0.68334 and is
now 0.68135; the previous incumbent's remaining gap to a 0.681 ceiling has
been roughly halved, and the served probability is now inside the band
between the 42-year market floor and the best public model.

### Verdict

ADOPT, both halves. Log loss improves with the paired interval excluding zero
in every configuration; Brier agrees everywhere; calibration error improves;
the share of predictions past the market's forty-two-year maximum falls by
two thirds; the sign holds in every season of every arm. `fs_v9` is the served
feature set and `PROJECTED` is the served run model (`run_inputs.SERVED`); a
game whose projection cannot be formed falls back to the base means and says
so, and one with neither serves the logistic model alone, as before.

The nightly promotion gate still applies to the logistic half: the first
refresh after this change trains `fs_v9`, scores it against the active
`fs_v1` model on the same walk-forward steps, and activates it only if the
paired interval excludes zero there too.

The first production backtest of `fs_v9` (13,461 games, 2021-05-31 to
2026-09-02) read log loss 0.6810, Brier 0.2439, ECE 1.01%, accuracy 56.7% for
the logistic component alone — and a probability-band table that ran
overconfident from 65% up (70–75%: said 72.0%, won 65.5%; 80–100%: said 83.2%,
won 66.7%, n=45). That is the component, not the served figure: on the local
2024–26 walk-forward the served blend's favourites at 65–70% *won* 72.2% against
66.8% stated, because the simulation's compression toward .500 more than
offsets the logistic's stretch. So the backtest now scores the served figure on
the same games (`app/backtest/served.py`; BACKTEST_PLAN.md §10) and the
product's reliability readouts — the backtest page, the game page's evidence
band, the health screen — describe what is served, with the component shown
beside it rather than instead of it.

Wiring that up exposed a defect in the gate itself. Both arms were being run
on the *candidate's* matrix — the incumbent arm was "the candidate's columns
at the incumbent's C". For a `C` change that is the right comparison; for a
feature-set change it is not, and when the grid picks the same `C` for both
sets (it picks 0.003 for `fs_v1` on six seasons, and would for `fs_v9`) it
compares a model with itself: a delta of exactly zero, a HOLD, and a feature
set that could never reach the product however much it improved on the old
one. The gate now scores the incumbent on the columns the active version
registered — the candidate's matrix restricted to them, which is exact because
imputation is per column, and needs no second build. When the active model
uses a column the candidate no longer carries, training builds the incumbent's
own matrix on the same store; when its feature set has been retired from the
registry and the version recorded no column list, the gate holds and says so.
`tests/test_promotion.py` pins all three paths, including a real walk-forward
on synthetic games where the old gate returned a zero delta at equal `C`.

---

## Spring training was inside every season-to-date rate

### Diagnosis

`season_start_utc` returns January the first and its docstring said "spring
training games are excluded by game_type". Nothing excluded them. The as-of
store loaded every game type — 355 to 403 spring-training games a season (type
`S`), the postseason (`F`, `D`, `L`, `W`), the odd exhibition — into the team,
pitcher and batter frames that every season-to-date rate reads, and Elo
replayed all of them. On 28 March 2025, a day into the regular season, the
Yankees' "season-to-date" aggregate held 27 games: 26 of them spring training,
at 5.56 runs a game. Split squads, minor-league line-ups and pitchers working
on a change-up were a sixth of every team's sample at the All-Star break and a
seventh at the end of the season, in the offence rates, the run-prevention
rates, the bullpen and starter lines, the platoon splits, the run model's
means, the projections' current-season term, and the rating.

It was also why the run model's ten-game gate never bit in April: by opening
day every team had "played" twenty-five games. The base run model was served
on spring-training rates for the first fortnight of every season measured.

### The rule, and where it lives

`AsOfStore` now admits only regular-season lines (`RATE_GAME_TYPES = {"R"}`)
into the frames the rates read, and Elo and the schedule index see only
competitive games (`COMPETITIVE_GAME_TYPES`: the regular season and the
postseason). The rule is applied in the store's constructor rather than in its
loader so a store built any other way — a fixture, a measurement script —
obeys it too. `tests/test_game_types.py` pins it: a 15–0 exhibition ten days
before opening day moves no rate, no rating and no rest day.

One thing the exhibitions had been doing by accident: the Elo engine applies
its 30% offseason regression when it observes a new season's first game, and
a March exhibition was that game. Without it, a rating read on opening day
came back as last October's, unregressed, until the team's first result was
knowable. `AsOfElo.rating_at` now regresses a reading the same number of
times the engine does between the last snapshot and the moment asked for:
once for every season the league opened after the snapshot, plus once more
when the moment falls in a later year than the last opened season, because
the next game the engine sees will open a new one. That count is per season
opened, not per calendar year — a history loaded with a season missing
regresses once at the next opener, as the engine does — and it is exactly
the pregame rating the engine uses for that first game. It touches each
team's first game of a season and nothing else — too few games to measure,
and pinned by test instead.

Nothing about the dataset's *scored* rows changes — those were always regular
season only (`DEFAULT_GAME_TYPES`). What changes is what the features of those
rows were computed from.

### Measured

The whole `fs_v9` matrix was rebuilt from the corrected store and walked
forward against the matrix built from the old one: same games, same C
(0.003), same steps, paired per game, 2024–26, trained from 2021. The first
rebuild excluded spring training and nothing else.

| 6,869 games | Log loss | Brier | ECE | Δ log loss, paired 95% |
|---|---|---|---|---|
| Logistic, spring inside the rates | 0.68463 | 0.24574 | 1.89% | |
| Logistic, regular season only | 0.68361 | 0.24527 | 1.27% | +0.00103 [−0.00096, +0.00307] |
| Simulation (projected means), before | 0.68211 | 0.24455 | 1.67% | |
| Simulation, after | 0.68213 | 0.24456 | 1.49% | ≈ 0 |
| **Served blend, before** | 0.68102 | 0.24403 | 0.96% | |
| **Served blend, after** | 0.68112 | 0.24408 | **0.50%** | −0.00010 [−0.00119, +0.00101] |

By season the logistic gained 0.0047 in 2024 (interval just touching zero),
nothing in 2025 and lost 0.0021 in 2026 (interval just touching zero); by
month the gain sits in April and May and the one loss is the first week of
each season, where a vector now has missing season-to-date features instead
of spring-training ones. The served figure did not move on log loss and its
calibration error halved: the 65–70% band went from winning 73.7% of the time
against 66.9% stated to 71.0%.

### The opening-week calibrator

The second rebuild added the league prior, and its walk-forward lost 0.046
of log loss in April 2024 — one month, nothing else — with the features of
those games moved by a hundredth of a standard deviation. The features were
not the cause. The walk-forward fits each step's Platt calibrator on the last
forty-five days of its training window, and for the first step of a season
that slice is the opening week. With opening day now predictable the April
2024 slice held 55 games instead of 39, crossed the fifty-row floor that
existed, and the home side had won 38% of them. The calibrator fitted slope
1.93 and intercept −0.80 on that week and pulled every prediction for the
following month toward the visitor: the step's mean probability went from
0.536 raw to 0.371 calibrated.

That is a defect in the protocol, not in the prior, and it was latent: the
same step on the original matrix was calibrated on 49 games and cost 0.004.
The floor is now `MIN_CALIBRATION_ROWS = 300` (`app/backtest/walkforward.py`),
about three weeks of a season; a step whose slice is thinner serves the raw
fit, which is a probability already, rather than a two-parameter guess at
how to bend it. The final model's calibrator obeys the same floor. Measured
paired, the floor alone is worth +0.00268 [+0.00066, +0.00484] on the matrix
with the prior and +0.00146 [−0.00013, +0.00314] on the original one, all of
it April 2024, zero elsewhere. Pinned by
`test_a_thin_validation_slice_leaves_a_step_uncalibrated`.

### The league prior, with the floor

| 6,869 games, paired | Δ log loss | 95% |
|---|---|---|
| Regular season only → plus the league prior (logistic) | +0.00003 | [−0.00017, +0.00021] |
| Same, served figure | +0.00002 | [−0.00008, +0.00011] |

A null to four decimals. What it buys is not accuracy but existence: the
league rates are defined before a season's first pitch, so opening day is
predicted — 77 more games in the six seasons, 16 of them still skipped in
2021 which has no season before it in the store.

### All of it together

| 6,869 games, paired | Log loss | Brier | ECE | Δ log loss, paired 95% |
|---|---|---|---|---|
| Logistic, as it was | 0.68463 | 0.24574 | 1.89% | |
| Logistic, corrected store + prior + floor | 0.68346 | 0.24519 | 1.48% | +0.00105 [−0.00095, +0.00312] |
| **Served, as it was** | 0.68102 | 0.24403 | 0.96% | |
| **Served, corrected** | 0.68121 | 0.24413 | **0.52%** | −0.00019 [−0.00127, +0.00092] |

By month the served figure gives up 0.007 in the first week of a season
(112 games) and gains 0.002 in May; the rest is inside a thousandth. Its
favourites at 65–70% won 71.0% against 66.9% stated (n=248), at 70% and
above 74.4% against 72.3% (n=43).

**Verdict: adopted, as a correction.** On the product's own scoring rule the
three changes together are a measured NO_EFFECT with a calibration
improvement, and they are recorded as such. They are adopted because the
rates now mean what FEATURE_DICTIONARY.md says they mean and what every
downstream constant — the shrinkage `k`s, `MIN_TEAM_GAMES`, the ten-game
run-model gate — was set for; because the base run model no longer starts a
season on split-squad box scores; and because a calibrator fitted on an
opening week is a bug whichever matrix exposes it. The first-week loss is the
same finding as before, stated more sharply: only four features carry memory
across seasons, and every other rate is imputed until a team has played.
Extending the projections to the bullpen, the platoon splits and the team's
own strikeout and walk rates is the next measured lead.

Two consequences handled deliberately. With no spring games to count, a team
has zero games of history on opening day, and `FeatureVector.is_usable` used
to withhold the whole vector. It now accepts a side whose projection stands
on a real prior-season sample, so opening day is predicted — from the
projections and the rating, with the season-to-date features honestly
missing — rather than served as UNAVAILABLE or, as before, from spring
training.

And before a season's first pitch there is no *league* rate for that season
either: every shrinkage prior, the projections' league anchors and the run
model's league mean read one, and all of them were undefined on opening day
— which the spring games had hidden. `FeatureBuilder.league_baseline` now
shrinks the season in progress toward the previous season's completed rates
by a pre-registered `LEAGUE_PRIOR_K_TEAM_GAMES = 300` team-games: on opening
day the rates are last season's, the two carry equal weight ten days in, and
the previous season is a twentieth of the answer by October. League rates
move about a tenth of a run a year, so the pull is small where it lingers and
large only where this season has nothing to say. `tests/test_league_prior.py`
pins it. The rebuilt matrix with this prior was walked forward against the
one without it; the result is in the table below.

---

## Elo's constants: measured, and left alone

MODELING_PLAN.md §3 has always said that K, the home-field constant and the
between-season regression are "fit on historical data by minimizing
walk-forward log loss, not set by convention", and they were set by convention
— K=6, 24 points, 30%. So they were fitted.

**Replaying raw Elo**, scored on its own pre-game probability, with 2021 as an
unscored burn-in so a cold start does not reward a large K:

| Fit seasons | Best by fit | Hold-out 2024–26 log loss | Defaults' hold-out |
|---|---|---|---|
| 2022–23 | K=4, HFA=18, regression 0.5 | **0.68250** | 0.68386 |

A real improvement in the rating: 0.0014 on 6,955 held-out games. (The hold-out
itself would have preferred K=2.5 at 0.68173; that ranking is reported for
reference and was not selected on.)

**As a feature inside the logistic model**, the tuned rating is worth nothing:

| Trained from | Δ log loss, tuned Elo vs default | Paired 95% CI |
|---|---|---|
| 2023 | +0.000058 | [−0.00012, +0.00025] |
| 2021 | +0.000047 | [−0.00019, +0.00029] |

The logistic model re-weights `elo_diff` against seven other team-strength
features, so a better-scaled rating buys it almost nothing — and the
cross-season memory a slower K carries is exactly what the projections now
supply directly. The constants stay as they are. The measurement is kept
because the two results together are the finding: a rating can improve on its
own terms and change nothing downstream.

---

## Training history: six seasons, not two

The same `fs_v1` features, the same 6,900 test games, C pinned at 0.03, the
training window starting in 2021 instead of 2023:

| | Log loss | Δ | Paired 95% CI |
|---|---|---|---|
| Trained from 2023 | 0.68808 | | |
| Trained from 2021 | 0.68655 | **+0.001533** | **[+0.00041, +0.00275]** |

By season: 2024 +0.00214, 2025 +0.00211 [+0.00069, +0.00358], 2026 +0.00018.
The marginal season is worth most when the window is short and almost nothing
by the third season of the test — which is the shape more data should have.

This mattered more in production than in the measurement. The hosted database
held **two** seasons (2025 and the current one) because that is what fitted a
free tier, and the published backtest scored 3,413 games from mid-May 2025.
The data store now lives in the repository's `data` release with no size
ceiling that matters (DEPLOYMENT.md path 0), the seed loads 2021 through 2026,
and the model the refresh trains each morning sees all of it.

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
| Calibrated logistic | Baseline; served as half of the blend — on `fs_v9` since the projections were **adopted** (above) |
| Gradient boosting | **Measured twice and rejected** — HistGradientBoosting at a fixed configuration, then XGBoost and LightGBM with per-fold search; see above |
| Negative-binomial run model | Built; serves as the simulation half of the blend, on the **projected** means since they were measured and adopted |
| Starter + bullpen innings allocation | Built into the simulation; refinements measured and rejected |
| Monte Carlo simulation | Built; **promoted** as half of the served blend |
| Elo with starter adjustment | Elo **measured as a calibrated component** — matches the baseline, not promotable; its constants **fitted and left alone** (above); the starter adjustment is still not built |
| Stacked ensemble | Built; **measured and rejected** — see above |

Nothing in that table is claimed to improve anything until the walk-forward
proves it. Every row that has been measured is recorded with its verdict
rather than retried until it agreed.
