# BACKTEST_PLAN — Jerry MLB Prediction Lab

The backtest is the product's evidence base. It must be walk-forward,
leak-free, sliced, and reproducible — and it must be honest about the ceiling.

---

## 1. Protocol

**Walk-forward, expanding window, chronological, no shuffling.**

```
for each evaluation step (default: one calendar month of games):
    train_end   = step_start − 1 day
    train_start = earliest available game            (expanding window)
    validate    = last VALIDATION_DAYS before train_end   (calibration fit)
    test        = games in [step_start, step_end]

    1. build features for train rows at as_of = first_pitch − 3h
    2. fit scaler + imputer + model on TRAIN only
    3. fit calibrator on VALIDATION only
    4. predict TEST, store row-level output
    5. advance
```

Guarantees:

* A test game is never in any training fold that precedes it.
* The scaler, imputer and calibrator never see test rows.
* Features are produced by the same code path as live predictions.
* Every step records `train_end_date` and `n_train_rows` on each row-level
  prediction, so any claim can be re-derived from `backtest_predictions`.

**Minimum training volume.** A step is skipped (not predicted with a stub) if
the training window has fewer than `MIN_TRAIN_ROWS` (default 500) usable games.
Skipped steps are reported, not hidden.

**Minimum calibration volume.** A step's calibrator is fitted only when the
validation slice holds at least `MIN_CALIBRATION_ROWS` (300) games; below
that the step serves the model's raw probability. The slice is the last
forty-five days of the training window, which for the first step of a season
is the opening week, and a Platt fit on one opening week once pulled every
prediction for the following month toward the visitor (MODELING_PLAN.md
§ The opening-week calibrator). The final served model's calibrator obeys the
same floor.

---

## 2. Row-level output

`backtest_predictions` stores one row per predicted game:
`run_id, game_id, as_of, predicted_home_win_prob, actual_home_win,
train_end_date, n_train_rows, features`.

Every slice in §4 is computed from this table, so adding a slice never requires
re-running the walk-forward.

---

## 3. Metrics

| Metric | Definition | Priority |
|---|---|---|
| **Log loss** | `−mean[y·ln p + (1−y)·ln(1−p)]` | Primary |
| **Brier score** | `mean[(p − y)²]` | Primary |
| **Calibration error (ECE)** | `Σ (nᵦ/N)·\|obs_freqᵦ − mean_predᵦ\|` over 10 equal-width bins | Primary |
| **Max calibration error (MCE)** | `max_b \|obs_freqᵦ − mean_predᵦ\|` for bins with n ≥ 30 | Primary |
| ROC AUC | Ranking quality | Secondary |
| Accuracy | `mean[1(p>0.5) = y]` | Reported only |
| Baseline comparison | Log loss vs. always-0.5 (0.6931) and vs. home-field-constant | Context |
| ROI | Only when timestamped historical odds exist | Conditional |
| CLV | Only when timestamped opening **and** closing odds exist | Conditional |

ROI and CLV are **omitted entirely**, not reported as zero, when a licensed odds
provider has not supplied historical timestamped prices. A backtest without odds
reports a null for these fields and the UI hides the section.

---

## 4. Required slices

Every slice is computed from `backtest_predictions` and stored in
`backtest_results` with its `slice_type` / `slice_key`.

| Slice type | Keys |
|---|---|
| `overall` | `all` |
| `season` | 2021, 2022, … |
| `month` | 03…10 |
| `probability_band` | 50–55, 55–60, 60–65, 65–70, 70–75, 75–80, 80+ (by favorite probability) |
| `favorite_underdog` | `favorite`, `underdog` |
| `home_away` | `home_favored`, `away_favored` |
| `starter_quality` | quartiles of the favored side's starter quality index |
| `lineup_confirmed` | `confirmed`, `unconfirmed` *(populated once lineups are ingested pregame — Phase 2)* |

Each slice reports n, accuracy, log loss, Brier, calibration error and AUC.
A slice with n < 30 reports the count but suppresses the metrics, because a
calibration error computed on 12 games is noise.

---

## 5. Calibration reporting

The calibration chart plots mean predicted probability against observed win
frequency in 10 bins, with:

* the diagonal reference line,
* per-bin sample counts (bar underlay),
* Wilson score intervals per bin, so a reader can see which deviations are
  meaningful,
* the overall ECE and MCE printed beside it.

A bin that is off the diagonal with a wide interval is not a calibration
failure; the chart must make that legible rather than inviting over-reading.

---

## 6. Ablation suite

For each feature group, refit the entire walk-forward with the group removed and
compare out-of-sample log loss to the full model.

| Group | Members |
|---|---|
| `starting_pitcher` | all `sp_*` |
| `bullpen` | all `bp_*` |
| `expected_lineups` | lineup-derived features *(Phase 2)* |
| `weather` | `env_temperature_f`, wind, humidity, air density *(Phase 2)* |
| `park_factors` | `env_park_*` *(Phase 2)* |
| `recent_form` | `*_w7`, `*_w14`, `*_w30`, `*_form_delta_*` |
| `head_to_head` | `h2h_*` |
| `batter_vs_pitcher` | `bvp_*` *(Phase 3)* |
| `travel_rest` | `sched_*` |
| `market_odds` | market features *(Phase 3)* |
| `team_strength` | `elo_*`, `team_*` |
| `defense` | `def_*` |

Output is a **model comparison table**: group, Δ log loss, Δ Brier, Δ ECE, Δ AUC,
n, and a verdict of `IMPROVES` / `NEUTRAL` / `HURTS`. The threshold for
`IMPROVES` is a Δ log loss beyond the run-to-run noise band, estimated by
repeating the full model with different seeds.

### Marginal value is not total value

Leave-one-out ablation measures a group's **marginal** contribution *given every
other group*. On a correlated feature set that systematically understates a
group's worth: Elo, season win percentage, run differential and Pythagorean
expectation all encode team quality, so removing any one of them barely moves
the metric even though team quality is the single most valuable thing the model
knows.

The ablation suite therefore reports a second, complementary view — each group
**alone** — and the two are read together:

| Reading | Interpretation |
|---|---|
| Removal hurts **and** the group alone predicts | Carries unique signal; keep. |
| Removal is neutral **but** the group alone predicts | Redundant given the rest; keep one representative, revisit when correlated groups change. |
| Removal is neutral **and** the group alone does not predict | Carries nothing; the removal rule applies. |
| Removal helps beyond the noise band | Actively harmful; remove. |

**A group in the last two rows is removed or reduced in the active feature set**,
and that decision is recorded in the model version notes so the history of what
was tried and rejected is preserved. A group in the middle row is kept but
flagged, because deleting every redundant member of a correlated cluster would
delete the cluster's signal entirely.

---

## 7. Feature importance stability

For each walk-forward step, record standardized coefficients (Model 1) or gain
importances (Model 2). Report:

* the mean and standard deviation of each feature's rank across steps,
* Spearman rank correlation between consecutive steps,
* features whose rank standard deviation exceeds a threshold, flagged as
  unstable and referred to the ablation review.

Stability matters because a feature that is the top driver in April and
irrelevant in July is usually fitting noise.

---

## 8. Sanity gates

The backtest is a leakage detector as much as a performance report. A run is
automatically flagged `SUSPECTED_LEAKAGE` when any of these hold over a full
season:

| Gate | Threshold |
|---|---|
| Accuracy | > 62% |
| Log loss | < 0.62 |
| ROC AUC | > 0.70 |
| Single-feature share of total absolute contribution | > 40% |
| Volume in the 0–5% or 95–100% probability bands | > 2% of games |

Context for these numbers: the betting market's closing line — the strongest
publicly available predictor — achieves roughly 0.65 log loss and 58–60%
accuracy over a season. A model materially exceeding that has almost certainly
seen the future. The flag is displayed prominently in the backtest UI, and the
headline metrics are visually de-emphasized until the flag is cleared.

Symmetrically, a run is flagged `UNDERPERFORMING` if log loss exceeds 0.6931
(the always-50% baseline), which indicates a broken feature or label alignment.

---

## 9. Reproducibility

Every run stores: `run_id` (UUID), model name and version, feature-set version,
as-of policy, date range, step size, validation length, seed, git SHA, and the
full metric set. Re-running with the same `run_id` inputs reproduces identical
row-level predictions; `tests/test_backtest_reproducibility.py` asserts this on
a small fixture window.

---

## 10. What the backtest is shown as, in the product

**The figure reported first is the figure that is served.** The product
serves the calibrated logistic model blended in log-odds with the run
simulation at the pre-registered weight, or the logistic alone for a game the
simulation cannot form (`app/modeling/serving.py`). The two do not calibrate
the same way — over 2024–26 the logistic's favourites above 65% won about five
points less often than stated, the simulation's about ten points more, and the
blend landed within two points of its word — so a reliability report on the
component alone was not a reliability report on the number a reader acts on.
`app/backtest/served.py` scores the served figure on the same walk-forward
games, the way serving produces it: the simulation's dispersion is fitted
once per slate, at the slate's earliest as-of, from everything knowable
then, and shared by every game on the card (a slate whose sample is too
small for a fit is served as the logistic alone, as serving serves it);
each game's run means are read as-of the same moment the logistic's features
were; and the projected means come first with the season-to-date means as
the fallback — so a game the projection covers in a team's opening
fortnight is scored as served rather than dropped at the base model's
ten-game gate. The sanity gates run
on the served figure as well as on the component, and a tripped flag names
which figure tripped it.
Every backtest row stores the served and simulated probabilities beside the
logistic's, and the served slices are stored under a `served_` slice type
beside the component's. A run that skipped the served evaluation
(`backtest --no-served`) shows the component and says so; it never presents
the component's figures as the served figure's.

The Backtest page presents, in this order:

1. **Headline** — n games, log loss, Brier, ECE, accuracy of the served figure,
   with the always-50% baseline beside them for context, plus any sanity flag,
   and how many games were blended versus served as the logistic alone.
2. **Calibration chart** — predicted vs. observed with per-bin counts.
3. **Probability band table** — the honest answer to "how reliable have similar
   predictions been?", which is what the game detail page links into.
4. **The logistic component alone** — the same three readouts for the
   component before blending, so the two can be compared on the page.
5. **Ablation table** — which feature groups of the component earn their place.
6. **Slice tables** — season, month, favorite/underdog, home/away, starter
   quality, for the served figure.
7. **Run metadata** — model version, feature set, date range, git SHA, and the
   served figure's blend weight, run model and draw count.

The game detail page's *Backtest evidence* tab shows only the probability band
that the current prediction falls into, with its n and observed frequency, so
the user gets the directly relevant historical reliability without reading the
whole report. That band, the health screen's backtest summary and the
confidence score's historical-calibration component all read the served rows
when the latest run has them and the component's rows when it does not.

---

## Phase 2A: the gate every new group passes

A feature group is registered, then it must survive all seven of these before
it enters `fs_v1`. Failing any one disables the group.

| Gate | What it rules out |
|---|---|
| **Leakage test** | The feature vector is bit-identical with and without the target game's rows present |
| **Walk-forward ablation** | Leave-one-out *and* group-alone. Marginal value and total value are different questions on correlated features |
| **Permutation importance, out-of-sample only** | Importance measured on training data ranks whatever was memorised hardest |
| **Season stability** | A group whose sign flips between seasons is fitting a season, not the game |
| **Calibration comparison** | A group may improve log loss while degrading reliability in the tails. Reliability wins |
| **Missing-data sensitivity** | Statcast is null on older seasons and on untracked pitches. A group that only works when complete is a liability on a live slate |
| **Before/after confirmed lineup** | Lineup features must be scored at the snapshot where a lineup is genuinely knowable, never against a backfilled one |

### Reporting

Each run records, per group: Δ log loss, Δ Brier, Δ calibration error, log loss
with the group alone, permutation importance, per-season sign, and the verdict
string. A rejected group stays in the registry with `available=False` and its
measurement attached, so the next person sees the evidence rather than
re-litigating it.

### Sizing note

Walk-forward with Statcast features is bounded by how many seasons of pitches
are stored, not by compute. With one season ingested the backtest has one
season of Statcast-eligible steps; earlier steps fall back to the box-score
feature set and are reported separately rather than silently mixed.
