# LEAKAGE_PREVENTION — Jerry MLB Prediction Lab

Data leakage is the failure mode that makes a bad sports model look excellent.
This document lists every leakage vector the platform is exposed to, the
mechanism that blocks it, and the test that proves the mechanism works.

---

## 1. The as-of principle

> A feature computed for a prediction at time `T` may read a fact only if that
> fact was **knowable** strictly before `T`.

Two timestamps are required to enforce this, and both are stored on every fact
row:

| Column | Meaning |
|---|---|
| `knowledge_time` | The earliest wall-clock time at which this fact could have been known. |
| `retrieved_at` | When our ingestion actually fetched it. Operational only. |

Feature queries filter on `knowledge_time`. They never filter on `retrieved_at`,
because a 2023 boxscore backfilled in 2026 has `retrieved_at = 2026` and would
be wrongly excluded from a 2024 prediction — or, worse, a lineup snapshot
fetched after first pitch would be wrongly *included*.

The rule is implemented once, in `app/features/asof.py`, and every feature
builder is required to route its queries through it.

---

## 2. Prediction timestamp policy

A prediction is always issued *for* an explicit `as_of`. Live predictions use
`now()`. Backtest predictions use a **policy-derived** timestamp, never `now()`:

| Policy | `as_of` | Purpose |
|---|---|---|
| `T_MINUS_3H` | first pitch − 3 h | Default backtest and training timestamp. Probable pitchers are typically known; lineups typically are not. |
| `T_MINUS_60M` | first pitch − 60 min | Lineup-confirmed comparison arm. |
| `T_MINUS_15M` | first pitch − 15 min | Final pregame refresh. |

Backtest and training rows are generated with the same policy so that a metric
computed across them is comparable. The policy used is stored on every
prediction and every backtest run.

---

## 3. Vector: season-to-date statistics endpoints

**The risk.** `/api/v1/stats?stats=season` returns totals for the *current*
season as of today. Attaching that to a game played in April embeds the outcome
of that April game — and of every game between April and today — into the
input. This single mistake can inflate backtest accuracy by ten points or more
and is the most common way a sports model silently becomes worthless.

**The mechanism.** Phase 1 does not consume season-aggregate endpoints at all.
Every rolling statistic is reconstructed from `player_game_stats` and
`team_game_stats`, which are one row per game and carry `game_date_utc`. An
aggregate is therefore, by construction, a filtered sum over dated rows:

```sql
SELECT ... FROM player_game_stats
WHERE player_id = :pid
  AND game_date_utc < :as_of      -- strictly before
  AND knowledge_time <= :as_of    -- and knowable by then
  AND game_date_utc >= :window_start
```

**The test.** `tests/test_leakage.py::test_no_season_stat_endpoints` scans the
provider layer and fails if any request path contains a season-aggregate stats
endpoint. `tests/test_leakage.py::test_rolling_stats_exclude_target_game`
asserts that a feature computed for game *G* is bit-identical whether or not
*G*'s own boxscore row exists in the database.

---

## 4. Vector: the target game's own result

**The risk.** Joining `games` for context and accidentally carrying `home_score`,
`away_score`, `home_win`, `innings_played` or `game_end_utc` into the feature
vector.

**The mechanism.**
1. The feature builder receives a `GameContext` DTO that structurally omits
   every outcome field. The outcome columns are not present on the object, so
   they cannot be referenced.
2. The as-of window is `game_date_utc < as_of`, and `as_of < first_pitch` by
   policy, so the target game is outside every window.
3. A denylist of outcome-bearing column names is asserted against the emitted
   feature keys.

**The test.** `test_target_game_excluded_from_own_features`,
`test_feature_keys_contain_no_outcome_fields`.

---

## 5. Vector: later lineup and injury information

**The risk.** Lineups and injuries are *revised*. Reading "the lineup" as a
single mutable row means a prediction made at T−3h is evaluated against a lineup
that was published at T−50m.

**The mechanism.** `lineups` and `injuries` are append-only snapshot tables.
Each snapshot carries `knowledge_time`. The as-of reader selects the latest
snapshot with `knowledge_time <= as_of` — which for a T−3h prediction is often
"no confirmed lineup", and that is the honest answer. `lineup_status` then
reports `PROJECTED` or `UNAVAILABLE`, and completeness drops accordingly.

**The test.** `test_lineup_snapshot_respects_as_of` inserts two snapshots either
side of `as_of` and asserts the later one is invisible.

---

## 6. Vector: odds

**The risk.** Closing odds are the strongest single predictor of a baseball
game. Feeding a closing price into a model that claims to predict at T−3h is
leakage, and using closing odds to *evaluate* an earlier prediction conflates
two different questions.

**The mechanism.**
1. Odds are snapshots with a book timestamp. A feature may read only snapshots
   with `snapshot_at <= as_of`.
2. Closing-line value is computed **after** the fact as a *diagnostic*, in a
   code path that is physically separate from feature building and cannot write
   into a feature vector.
3. Market features are opt-in per model version and recorded in
   `model_versions.feature_set_version`, so a model trained with market features
   can never be silently evaluated as if it were a no-market model.

**The test.** `test_odds_after_as_of_are_invisible`,
`test_clv_module_does_not_import_feature_registry`.

---

## 7. Vector: normalization and imputation fit on the full dataset

**The risk.** Fitting a `StandardScaler` or a median imputer on the whole
dataset before splitting leaks the test distribution into training. It is
subtle, common, and inflates results.

**The mechanism.** The scaler, the imputer and the calibrator live inside the
model artifact and are fit exclusively inside the training fold, in a pipeline
whose `fit` is called once per walk-forward step on training rows only.
Transform-only is applied to validation and test.

**The test.** `test_scaler_fit_only_on_train` compares the scaler's fitted mean
against the training slice's mean and asserts it differs from the full-dataset
mean.

---

## 8. Vector: hyperparameter selection across the time boundary

**The risk.** `GridSearchCV` with a random `KFold` shuffles future games into
the training folds.

**The mechanism.** No random cross-validation anywhere. Selection is walk-forward
by date. The tuning code path takes an ordered date index and asserts
monotonicity of fold boundaries.

**The test.** `test_no_random_cv_in_training` scans the modeling package for
shuffling CV constructs and fails on any hit.

---

## 9. Vector: Elo and other stateful features

**The risk.** Elo is updated *after* each game. Computing a full-season Elo
series and then reading the value at row *i* is fine only if the update for game
*i* has not yet been applied.

**The mechanism.** The Elo engine exposes `rating_before(game)` and applies the
update strictly after emitting the pre-game rating. The series is built in
chronological order with a single forward pass, and the pre-game rating is the
only value ever exported to the feature layer.

**The test.** `test_elo_pregame_rating_excludes_current_game` asserts the rating
used for game *i* equals the rating produced after game *i−1*.

---

## 10. Vector: season totals that span the prediction date

**The risk.** "2024 season ERA" for a game on 2024-05-01 means ERA through
2024-05-01, not through 2024-09-30.

**The mechanism.** There is no stored "season stat". `season` is a *window*
argument to the same as-of aggregator, bounded by `season_start <=
game_date_utc < as_of`.

**The test.** `test_season_window_is_bounded_by_as_of`.

---

## 11. Vector: backfill contaminating knowledge time

**The risk.** Setting `knowledge_time = now()` on backfilled rows would make all
historical facts invisible to historical predictions, silently degrading the
backtest — or, if set to the row's date without care, could make a result
visible before the game ended.

**The mechanism.** `knowledge_time` is derived from the fact itself
([DATA_SOURCES](DATA_SOURCES.md) §4), never from the ingestion clock. Game
results use `game_end_utc`, falling back to first pitch + 3 h 30 m, which is
conservative — it can only make a fact *less* available, never more.

**The test.** `test_backfilled_result_knowledge_time_is_game_end`.

---

## 12. Vector: same-day doubleheaders

**The risk.** A date-only comparison (`game_date < as_of_date`) includes game 1
of a doubleheader in the features for game 2 only by accident, or excludes it
when it should be included.

**The mechanism.** All comparisons are on `timestamptz`, never on `date`. Game 1
of a doubleheader that ended before game 2's `as_of` is correctly included;
one that had not finished is correctly excluded.

**The test.** `test_doubleheader_uses_timestamp_not_date`.

---

## 13. Enforcement summary

| # | Vector | Mechanism | Test |
|---|---|---|---|
| 1 | Season stat endpoints | Not consumed; aggregates rebuilt from dated game logs | `test_no_season_stat_endpoints` |
| 2 | Target game's own result | Outcome fields absent from the DTO; window excludes the game | `test_target_game_excluded_from_own_features` |
| 3 | Later lineups | Append-only snapshots filtered by `knowledge_time` | `test_lineup_snapshot_respects_as_of` |
| 4 | Later injuries | Bitemporal rows | `test_injury_snapshot_respects_as_of` |
| 5 | Closing odds | `snapshot_at <= as_of`; CLV isolated | `test_odds_after_as_of_are_invisible` |
| 6 | Scaler/imputer fit | Fit inside training fold only | `test_scaler_fit_only_on_train` |
| 7 | Random CV | Walk-forward only; static scan | `test_no_random_cv_in_training` |
| 8 | Elo state | Pre-game rating only | `test_elo_pregame_rating_excludes_current_game` |
| 9 | Unbounded season window | Season is an as-of-bounded window | `test_season_window_is_bounded_by_as_of` |
| 10 | Backfill clock | `knowledge_time` derived from the fact | `test_backfilled_result_knowledge_time_is_game_end` |
| 11 | Doubleheaders | Timestamp comparisons only | `test_doubleheader_uses_timestamp_not_date` |
| 12 | Feature/label alignment | Label read only from `games.home_win` at scoring time | `test_label_never_in_feature_vector` |

---

## 14. The smell test

Leakage usually announces itself as results that are too good. The backtest
report therefore includes an automatic sanity gate:

* Accuracy > 62% over a full season → **flagged**.
* Log loss < 0.62 over a full season → **flagged**.
* Any single feature carrying more than 40% of total absolute contribution →
  **flagged**.
* Calibration that is near-perfect at the extremes with very high volume in the
  0–5% / 95–100% bands → **flagged**.

A flagged run is marked `SUSPECTED_LEAKAGE` in `backtest_results.extra` and the
UI displays the warning rather than the headline number. The gate is a
tripwire, not a proof of correctness — but a model that trips it has to be
explained before it can be trusted.

---

## Phase 2A vectors

### 13. A pitch informing a prediction made before it was thrown

Statcast is pitch-level, so the temptation is to aggregate a whole game. Every
Statcast feature query filters on `knowledge_time <= as_of` exactly like the
box-score layer, and `knowledge_time` is the **game's** end, not the pitch's
timestamp — a game's data becomes available to us as a unit.

*Mechanism*: `AsOfStore` gains pitch and batted-ball frames built through the
same `_slice` used for team and pitcher games. There is no separate path.

*Test*: a pitcher's 30-day xwOBA-allowed is bit-identical whether or not the
target game's pitches are present in the database.

### 14. Season-aggregate Statcast endpoints

Savant also exposes leaderboard endpoints returning **current-season totals**
per player. Attaching one of those to an April game embeds that game and every
game since — the same mistake the MLB `/stats` endpoints invite, and the reason
the HTTP client refuses them.

*Mechanism*: the Statcast client refuses any path that is not
`/statcast_search/csv`, and refuses a request without both `game_date_gt` and
`game_date_lt`. Aggregation happens here, from dated rows, never at the source.

*Test*: asserts both refusals raise.

### 15. A confirmed lineup that was not knowable yet

The lineups already ingested come from **completed-game box scores**, and carry
`knowledge_time = first pitch + 3h30m` — i.e. after the game. That is
deliberate and conservative: the box-score batting order is the lineup that
*played*, which is not the same object as the lineup that was *posted*
pregame, and treating them as identical would import late scratches and
in-game substitutions into a pregame prediction.

Consequence, stated plainly: **no lineup is knowable at the current T−3h
prediction time**, so lineup features cannot enter the default snapshot. They
belong to a later snapshot in the prediction timeline, at a time when a posted
lineup genuinely exists. Reconstructing an earlier `knowledge_time` for a
box-score lineup would be inventing knowledge, and is not done.

*Mechanism*: lineup rows keep the ingest's `knowledge_time`; the as-of filter
does the rest.

*Test*: asserts zero lineups are visible at `first_pitch − 1s` across the whole
ingested history.


---

## 16. A projected lineup is not a posted one

Phase 2B builds lineup features, which vector 15 says cannot be read from the
`lineups` table before first pitch. It does not read them from there.

The nine are **projected** from the team's own completed starts inside a 21-day
window: who started, and in which slot. Every row behind that projection is a
finished game carrying its own `knowledge_time`, so the as-of slice excludes the
game being predicted by exactly the rule everything else obeys — its lineup rows
are stamped first pitch + 3h30m, and `as_of` precedes first pitch.

Two properties keep this honest rather than merely legal:

* **A reader could do the same thing at the same moment.** The projection uses no
  information a person watching the same slate lacks. That is the test for
  whether a pregame feature is real.
* **The projection says how confident it is.** `lineup_continuity` reports what
  share of the projected nine started the most recent game. A feature that
  guesses should be measured on how often the guess holds, and a model that
  consumes it should be handed that number rather than left to assume.

What is still not available is the *posted* lineup — the one a team releases two
or three hours before first pitch. That needs a pregame poller writing rows with
an honest observation time, and until one exists the confirmed-lineup half of
step 3 stays unbuilt rather than approximated.
