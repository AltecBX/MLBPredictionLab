# IMPLEMENTATION_PLAN — Jerry MLB Prediction Lab

Delivery is phased. **Phase 1 is complete in this delivery and runs end-to-end
on real MLB data.** Phases 2–4 are specified here with their acceptance
criteria so the next increment starts from a defined boundary.

---

## Phase 1 — Foundation *(delivered)*

### Scope

| Item | Status |
|---|---|
| Architecture and design documents | Done — the eight documents in this repository |
| Database schema (all tables from [DATABASE_SCHEMA](DATABASE_SCHEMA.md)) | Done — created by migration, including tables that stay empty until their provider is enabled |
| Data provider interfaces | Done — Protocols + MLB Stats API implementation + explicit `Unavailable*` providers |
| Schedule ingestion | Done — real, from MLB Stats API |
| Game results ingestion | Done — real, final scores + full boxscores |
| Basic team and pitcher statistics | Done — rebuilt as-of from per-game boxscore lines |
| Initial logistic regression model | Done — L2 logistic, walk-forward selected `C`, Platt calibration, Elo reference model for the agreement signal |
| Daily games page | Done |
| Game detail page | Done |
| Walk-forward backtest | Done — engine, metrics, slices, calibration chart, ablation |

### Acceptance criteria

1. `make ingest` populates teams, ballparks, players, games, `team_game_stats`
   and `player_game_stats` from the live API with zero synthetic rows.
2. `make train` produces a registered `model_versions` row with out-of-sample
   metrics from a walk-forward split.
3. `make backtest` produces `backtest_results` rows for every slice in
   [BACKTEST_PLAN](BACKTEST_PLAN.md) §4 plus the ablation table.
4. `GET /api/v1/games?date=…` returns every scheduled game with a stored,
   immutable prediction, its confidence, completeness, freshness and top drivers.
5. The leakage test suite passes, including the assertion that a game's own
   result cannot influence its own feature vector.
6. Every UI surface that lacks data shows an explicit unavailable state naming
   the required source.

### Phase 1 boundary — what is deliberately not populated

These are schema-complete and API-complete, and report `UNAVAILABLE` with the
name of the required source. They are **not** filled with placeholder numbers:

* Statcast metrics (`sp_xwoba_allowed`, barrels, spin, movement, xERA, SIERA)
* A weighted model ensemble — Elo ships as a reference probability only, and
  carries no weight in the served number
* Pregame lineup confirmation (lineups are ingested from final boxscores for
  historical features; pregame confirmation needs the Phase 2 poller)
* Per-pitcher bullpen availability (usage and fatigue *are* computed — they are
  observable from game logs; availability is a separate feed)
* Forecast weather, empirical park factors, umpire profiles
* Injuries and transactions
* Odds, market comparison, ROI, CLV
* Monte Carlo simulation, run-scoring model, ensemble

---

## Phase 2 — Contact quality, pregame state, environment

### Deliverables

1. **Statcast provider** — pitch-level ingestion into `pitches` and
   `batted_ball_events`; derived `xwOBA`, exit velocity, hard-hit rate, barrel
   rate, spin, movement, pitch usage, velocity trend.
2. **Pregame lineup poller** — 5-minute cadence from T−3h, writing append-only
   `lineups` snapshots with correct `knowledge_time`; lineup status transitions
   surfaced in the UI and in completeness.
3. **Bullpen availability provider** — per-pitcher availability, closer/setup
   status, consecutive-day tracking.
4. **Injury/transaction provider** — bitemporal `injuries` rows.
5. **Weather provider** — forecast at first pitch, wind converted to
   field-relative using venue azimuth, air density computed.
6. **Park factors** — multi-year regressed run/HR factors with handedness
   splits, stored with `method` and `sample_games`.
7. **LightGBM model** — Optuna tuning under a chronological split, registered
   alongside Model 1.
8. **Calibration selection** — isotonic vs. Platt chosen on validation data.
9. **SHAP explanations** — translated into the existing probability-point
   vocabulary so the UI is unchanged.

### Acceptance criteria

* Ablation shows Statcast, lineup and weather groups each measurably improve or
  are removed.
* Lineup-confirmed vs. unconfirmed backtest slice is populated.
* GBDT is promoted only if walk-forward log loss improves without worsening ECE.

---

## Phase 3 — Runs, simulation, ensemble, market

### Deliverables

1. **Run-scoring model** — Poisson / negative binomial per side, dispersion
   tested rather than assumed.
2. **Monte Carlo simulation** — ≥ 10,000 seeded runs per game producing win
   distribution, run distributions, most common scores, extra-innings
   probability, one-run probability, upset probability; re-run triggers on
   material change.
3. **Ensemble** — non-negative weights fit on out-of-sample walk-forward
   predictions only, shrunk toward equal weight.
4. **Advanced matchup features** — times-through-order, platoon splits,
   arsenal-vs-lineup, batter-vs-pitcher under the ≥ 25 PA gate and contribution
   cap.
5. **Licensed odds integration** — timestamped snapshots, de-vigging, market
   comparison, ROI and CLV in the backtest.
6. **Game detail tabs completed** — Simulation and Market comparison.

### Acceptance criteria

* Simulation win% agrees with the ensemble probability within a stated
  tolerance, and disagreement beyond it is surfaced as reduced model agreement.
* No market feature can be read by a prediction whose `as_of` precedes the odds
  snapshot timestamp (enforced by test).

---

## Phase 4 — Operations

### Deliverables

1. **Automated retraining** — nightly walk-forward refit; activation gated on
   out-of-sample improvement beyond the noise band.
2. **Drift monitoring** — feature PSI, calibration drift, importance stability,
   with alerts on the diagnostics screen.
3. **Advanced diagnostics** — job failure history, missing-data inventory, stale
   source list, prediction generation failures, API usage, database health.
4. **Performance** — query plans reviewed for the as-of aggregates, materialized
   rollups where justified, cache warming before the daily slate.
5. **Production deployment** — container images, migrations on start, health and
   readiness probes, log shipping, error tracking.

---

## Build order actually followed in Phase 1

1. Design documents (this set) — before any code.
2. `core` — validated settings, structured logging, error types, clock, cache.
3. `db` — SQLAlchemy models for the full schema + migration.
4. `providers` — Protocols, `ProviderResult`, MLB Stats API client with rate
   limiting and retries, `Unavailable*` providers, registry.
5. `ingestion` — reference → schedule → results/boxscore, each idempotent, each
   writing `data_source_status` and `job_runs`.
6. `features` — as-of query helpers, registry, Elo engine, team/pitcher/bullpen
   builders, feature vector assembly with sample sizes and estimated flags.
7. `modeling` — dataset assembly, logistic pipeline, calibration, artifact
   registry, training entry point.
8. `backtest` — walk-forward engine, metrics, slices, ablation, sanity gates.
9. `services` — prediction generation, freshness, confidence, explanation.
10. `api` — typed routers for games, predictions, backtest, diagnostics.
11. `frontend` — Daily Game Center, Game Detail, Backtest, Diagnostics.
12. `tests` — unit, feature, leakage, reproducibility, API, component, E2E.

---

## Operational commands

| Command | Effect |
|---|---|
| `make migrate` | Apply database migrations |
| `make ingest-reference` | Teams, ballparks, players |
| `make ingest-schedule` | Schedule window (default ±10 days) |
| `make ingest-history SEASONS=2023,2024,2025` | Backfill schedule + results + boxscores |
| `python -m app.cli ingest-statcast --seasons 2025` | Backfill Statcast pitches, then reconcile against the box scores. Also a `workflow_dispatch` job, `statcast.yml` |
| `make train` | Walk-forward fit, register a model version |
| `make predict` | Generate immutable predictions for the current slate |
| `make backtest` | Full walk-forward evaluation with slices and ablation |
| `make daily` | ingest-schedule → ingest-results → predict |
| `make test` | Backend test suite |
| `make dev` | API + web in development |

---

## Risk register

| Risk | Mitigation |
|---|---|
| Upstream API schema change | Raw payloads stored; normalization isolated in one module per endpoint; contract tests on recorded fixtures |
| Rate limiting during backfill | Configurable request interval, chunked and resumable ingestion, request budget per run |
| Leakage regression during refactor | Twelve enforcement tests run in CI; the backtest's sanity gates act as a second tripwire |
| Overfitting to a single season | Walk-forward across multiple seasons; ablation; importance stability |
| Overconfident presentation | Confidence is multi-signal, not probability-derived; `INSUFFICIENT_DATA` is a first-class label; "lock" language is banned by test |
| Silent data gaps | Per-category freshness, explicit `UNAVAILABLE` states, completeness score, diagnostics screen |

---

# Phase 2A

Ordered so each step is usable on its own and the next one depends on it.

| # | Step | State |
|---|---|---|
| 1 | Statcast ingestion and data validation | **Done — verified on 188 real games** |
| 2 | Starting-pitcher Statcast features | **Built, measured, rejected** — MODELING_PLAN.md |
| 3 | Expected and confirmed lineup features | Blocked on the timeline — see below |
| 4 | Pitch arsenal matchup engine | Not started |
| 5 | Individual bullpen availability | Not started |
| 6 | Weather and empirical park factors | Not started |
| 7 | Gradient boosting | Built, **measured and rejected** (MODELING_PLAN.md) |
| 8 | Run scoring model and simulation | Not started |
| 9 | Stacked ensemble and calibration | Not started |
| 10 | Prediction timeline and change explanations | Not started |
| 11 | UI context features | Partly done — records, streaks, standings, nine-row summary shipped |

## Acceptance for step 1 — met

| Criterion | Evidence |
|---|---|
| Provider reaches Savant's CSV export, rate limited, resumable, timestamped | `app/providers/baseball_savant/`, `pending_statcast_dates()` |
| `pitches` and `batted_ball_events` widened to what the export carries | Migration `e37fd67a9dfd`; 41 and 25 columns |
| Reconciliation against the independently ingested box scores, loud on mismatch | Four checks, DATA_SOURCES.md § Reconciliation |
| Ingested a real date range and verified it | 2024-07-01 … 07-14: 188 games, 55,167 pitches, 9,677 balls in play, **0 discrepancies** |

Two defects were found by the reconciliation rather than by inspection, and both
are recorded in DATABASE_SCHEMA.md rather than quietly patched:

1. **Awarded balls and strikes are not pitches.** Savant emits a row for the
   no-pitch intentional walk and the pitch-timer violation. Counting them
   disagreed with the box score on 14 of the first 30 games, by up to 20 pitches.
   `is_pitch` now carries the distinction and every denominator uses it.
2. **Statcast measures fouls.** Defining a batted ball as "has a launch
   measurement" gave 98 per game against a true rate near 52 — an 85% inflation
   that would have diluted every contact-quality metric. Membership is now
   decided by `description`.

Derived rates on the ingested window, for the record: 88.1 mph average exit
velocity, 8.3% barrel, 38.2% hard hit, 48.0% swing, 23.2% whiff per swing, 49.6%
zone, 22.5% strikeout, 8.0% walk. Home runs agree exactly across three
independent paths — batted-ball table, pitch table and box score, 485 each.

## Result of step 2

The nine `sc_sp_*` features were built as feature set `fs_v2` and evaluated
against `fs_v1` walk-forward over the full 2024 season. **They do not improve
out-of-sample performance and are not adopted.** The active set is unchanged at
`fs_v1`.

| | fs_v1 (served) | fs_v2 |
|---|---|---|
| Log loss | **0.68383** | 0.68423 |
| Brier | **0.24545** | 0.24560 |
| Calibration error | **0.50%** | 1.59% |
| Accuracy | 56.17% | **56.69%** |

1,741 scored games, regularisation selected per set by the same rule, paired
95% intervals all spanning zero. Leave-one-out agrees: removing the group
improves log loss by 0.0041, and the group alone beats a coin flip by 0.00001.
MODELING_PLAN.md § Starting-pitcher Statcast has the full table and the
univariate diagnosis of why.

The features stay in the registry with `available=False` and the measurement
attached, so the next attempt starts from a different hypothesis rather than
from this one again.

**What this changes about the order below.** Step 4, the pitch-arsenal matchup
engine, was already next after 2 and 3; this result raises its priority relative
to more pitcher-level aggregates, because the thing that was measured — a
starter's stuff *in general* — is not the thing that decides a game. His stuff
against tonight's nine hitters might be.

## Why step 3 is not simply "next"

The lineups already ingested come from completed-game box scores and carry
`knowledge_time = first pitch + 3h30m`. Measured across all 188,604 rows:
**zero are knowable before first pitch.** That is correct and conservative —
the batting order that *played* is not the lineup that was *posted* — but it
means lineup features cannot enter the T−3h snapshot at all.

So step 3 in its useful form depends on step 10: a later snapshot, at a time
when a posted lineup genuinely exists. Building lineup features against
backfilled box-score lineups and scoring them at T−3h would produce an
improvement that could not be reproduced live. That is the exact failure this
repository exists to avoid, so it is not done.

The honest sequence is therefore **1 → 2 → 10 → 3**, and this document is
updated rather than the constraint being worked around.
