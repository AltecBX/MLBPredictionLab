# ARCHITECTURE — Jerry MLB Prediction Lab

> A transparent, historically validated MLB game win-probability platform.

This document defines the system boundaries, components, data flow, and the
engineering rules that every module must obey. It is the entry point for the
other design documents:
[DATA_SOURCES](DATA_SOURCES.md) ·
[DATABASE_SCHEMA](DATABASE_SCHEMA.md) ·
[FEATURE_DICTIONARY](FEATURE_DICTIONARY.md) ·
[MODELING_PLAN](MODELING_PLAN.md) ·
[LEAKAGE_PREVENTION](LEAKAGE_PREVENTION.md) ·
[BACKTEST_PLAN](BACKTEST_PLAN.md) ·
[IMPLEMENTATION_PLAN](IMPLEMENTATION_PLAN.md).

---

## 1. Product statement

For every scheduled MLB game the platform answers seven questions, in this order:

| # | Question | Where it is answered |
|---|----------|----------------------|
| 1 | Which team is more likely to win? | Game card headline, detail Prediction tab |
| 2 | What is the probability? | Calibrated win probability, both sides |
| 3 | Why does the model favor that team? | Contribution breakdown (top 5 for / top 5 against) |
| 4 | What information is uncertain? | Data completeness + freshness + risk list |
| 5 | How reliable have similar predictions been? | Backtest evidence sliced by probability band |
| 6 | What changed since the previous prediction? | Prediction diff against the prior immutable snapshot |
| 7 | Which model inputs had the greatest impact? | Per-feature contribution in probability points |

Everything in the UI must serve one of those seven. A statistic that cannot be
traced to a prediction contribution does not belong on the screen.

---

## 2. Non-negotiable engineering rules

These are enforced in code, not by convention. Violations fail tests.

1. **As-of correctness.** Every feature value is computed from an explicit
   `as_of` timestamp and may only read facts whose `knowledge_time <= as_of`.
   See [LEAKAGE_PREVENTION](LEAKAGE_PREVENTION.md).
2. **No fabricated values.** If a source is unavailable the field is `null` and
   carries an explicit `UNAVAILABLE` status that reaches the UI. There is no
   default-to-league-average that is silently presented as observed data.
   (League-average *shrinkage priors* are legitimate and are labelled as such —
   see rule 4.)
3. **Immutable predictions.** A prediction row is never updated. A new
   prediction supersedes the old one; both remain queryable so a historical
   prediction can be evaluated exactly as it was issued.
4. **Estimated values are labelled.** Any field that is derived from a prior,
   shrunk toward a baseline, or projected rather than observed carries
   `is_estimated = true` and a stated sample size.
5. **Reproducibility.** `(model_version_id, game_id, as_of)` deterministically
   reproduces a prediction. Feature vectors are persisted with the prediction.
6. **Calibration before accuracy.** Log loss, Brier score and calibration error
   rank above accuracy in every model-selection decision.

---

## 3. Component topology

```
                        ┌──────────────────────────────────┐
                        │ External sources                 │
                        │  MLB Stats API   (schedule,      │
                        │    results, boxscores, venues)   │
                        │  Statcast        (Phase 2)       │
                        │  Weather API     (Phase 2)       │
                        │  Licensed odds   (Phase 3)       │
                        └───────────────┬──────────────────┘
                                        │  provider interfaces only
                                        ▼
┌───────────────────────────────────────────────────────────────────────┐
│ INGESTION LAYER  (backend/app/ingestion)                              │
│  • one job per data category, each idempotent and re-runnable         │
│  • writes raw_payload → normalized rows → data_source_status          │
│  • records source name, retrieval_time, knowledge_time, freshness     │
└───────────────┬───────────────────────────────────────────────────────┘
                ▼
┌───────────────────────────────────────────────────────────────────────┐
│ STORAGE  (PostgreSQL 16)                                              │
│  fact tables (games, player_game_stats, …) · raw_source_payloads      │
│  feature store (model_features) · predictions (immutable)             │
│  model_versions · backtest_results · data_source_status               │
└───────────────┬───────────────────────────────────────────────────────┘
                ▼
┌───────────────────────────────────────────────────────────────────────┐
│ FEATURE LAYER  (backend/app/features)                                 │
│  as-of rolling aggregates · shrinkage · sample-size tracking          │
│  every feature declares: window, prior, min sample, source, phase     │
└───────────────┬───────────────────────────────────────────────────────┘
                ▼
┌───────────────────────────────────────────────────────────────────────┐
│ MODEL LAYER  (backend/app/modeling)                                   │
│  M1 logistic  M2 GBDT  M3 Elo  M4 run-scoring  M5 Monte Carlo         │
│  → ensemble (out-of-sample weights) → calibration → confidence        │
│  registry: model_versions + persisted artifacts + metrics             │
└───────────────┬───────────────────────────────────────────────────────┘
                ▼
┌───────────────────────────────────────────────────────────────────────┐
│ SERVICE LAYER  (backend/app/services)                                 │
│  prediction service · freshness service · confidence service          │
│  explanation service · backtest engine · diagnostics                  │
└───────────────┬───────────────────────────────────────────────────────┘
                ▼
┌───────────────────────────────────────────────────────────────────────┐
│ API  (FastAPI, /api/v1)   ← Redis cache, short TTL, versioned keys    │
└───────────────┬───────────────────────────────────────────────────────┘
                ▼
┌───────────────────────────────────────────────────────────────────────┐
│ FRONTEND  (Next.js App Router, TypeScript, Tailwind)                  │
│  Daily Game Center · Game Detail · Backtest · Diagnostics             │
└───────────────────────────────────────────────────────────────────────┘
```

---

## 4. Technology choices

| Layer | Choice | Rationale |
|---|---|---|
| API | FastAPI + Pydantic v2 | Typed request/response contracts that generate the OpenAPI schema the frontend types are derived from. |
| ORM | SQLAlchemy 2.0 (typed) | Explicit SQL control for as-of window queries; typed models. |
| Migrations | Alembic | Schema history required for an auditable feature store. |
| DB | PostgreSQL 16 | Window functions and partial indexes drive the as-of aggregates. `JSONB` holds raw payloads. |
| Cache | Redis 7 | Read-through cache for game-day endpoints; key namespace carries the model version so a retrain invalidates cleanly. |
| Modeling | scikit-learn (P1) → LightGBM, Optuna, SHAP (P2/P3) | Start with a transparent, well-calibrated linear baseline; add trees only when walk-forward validation shows a gain. |
| Registry | Local artifact registry backed by `model_versions` | MLflow-compatible layout without requiring an MLflow server in Phase 1. |
| Frontend | Next.js 15 (App Router) + React 19 + Tailwind 4 | Server components for fast first paint on the game list; client islands for sorting and charts. |
| Charts | Hand-rolled SVG components | No chart library dependency for the small number of chart types needed; keeps bundle small and styling consistent. |
| Logging | structlog → JSON | Structured, greppable, ships to any log backend. |
| Tests | pytest + Vitest + Playwright | Unit, feature-calculation, leakage, API integration, component, and E2E. |

---

## 5. Request lifecycle — daily game center

```
GET /api/v1/games?date=2026-08-01
   │
   ├─ Redis: games:v{model_version}:{date}:{schema_rev}   ─── hit ──► respond
   │
   └─ miss
      ├─ read games + teams + venues for date
      ├─ read latest prediction per game (immutable, most recent as_of)
      ├─ read data_source_status → freshness per category
      ├─ assemble GameCard DTO (probability, projected score, confidence,
      │  top-3 drivers, lineup status, warnings, last update)
      └─ cache with TTL from freshness policy (§7) and respond
```

The API never computes a prediction inside a `GET`. Predictions are produced by
the prediction job and read back; that keeps request latency flat and makes the
served probability identical to the stored, auditable one.

---

## 6. Job schedule

| Job | Cadence | Purpose |
|---|---|---|
| `ingest_reference` | daily 09:00 UTC | Teams, venues, park metadata, player master. |
| `ingest_schedule` | every 30 min | Schedule ± window, status changes, probable pitchers. |
| `ingest_results` | every 15 min during games, hourly otherwise | Final scores, boxscores, per-player game lines. |
| `ingest_lineups` | every 5 min from T‑3h to first pitch | Lineup confirmation transitions. *(Phase 2)* |
| `ingest_weather` | hourly, every 15 min from T‑6h | Forecast → observed. *(Phase 2)* |
| `build_features` | after each ingest, and T‑3h / T‑60m / T‑15m | Materialize as-of feature rows. |
| `generate_predictions` | after `build_features`, plus on material change | Write a new immutable prediction snapshot. |
| `train_models` | nightly | Walk-forward refit; register a new `model_version` only if out-of-sample metrics improve. |
| `run_backtest` | nightly + on demand | Walk-forward evaluation and calibration report. |
| `check_sources` | every 5 min | Update `data_source_status`, raise stale/unavailable flags. |

A **material change** — starting pitcher change, lineup confirmation, scratch of
a projected starter, weather change beyond threshold, bullpen availability change
— forces feature rebuild, prediction regeneration and simulation rerun.

---

## 7. Freshness and caching policy

Freshness is tracked *per data category*, not globally, because a stale weather
feed and a stale schedule feed have different consequences.

| Category | Fresh | Aging | Stale | Cache TTL |
|---|---|---|---|---|
| Schedule | < 1 h | 1–6 h | > 6 h | 300 s |
| Probable pitchers | < 2 h | 2–12 h | > 12 h | 300 s |
| Lineups | < 15 min | 15–60 min | > 60 min | 60 s |
| Injuries | < 6 h | 6–24 h | > 24 h | 600 s |
| Weather | < 1 h | 1–3 h | > 3 h | 600 s |
| Player statistics | < 12 h | 12–36 h | > 36 h | 900 s |
| Bullpen usage | < 6 h | 6–24 h | > 24 h | 600 s |
| Odds | < 10 min | 10–60 min | > 60 min | 60 s |

Within 3 hours of first pitch every TTL is clamped to 60 s.

---

## 8. Module layout

```
backend/app/
  core/         settings (validated), logging, errors, cache, clock
  db/           engine, session, base, models/*.py  (one module per domain)
  providers/    base.py (Protocols) · mlb_statsapi/ · unavailable.py · registry.py
  ingestion/    reference.py · schedule.py · results.py · runner.py
  features/     registry.py · asof.py · team.py · pitcher.py · builder.py
  modeling/     dataset.py · logistic.py · calibration.py · registry.py · train.py
  backtest/     engine.py · metrics.py · report.py
  services/     prediction.py · freshness.py · confidence.py · explanation.py
  schemas/      typed API DTOs
  api/v1/       games.py · predictions.py · backtest.py · diagnostics.py · admin.py
  cli.py        operational entry point for every job
frontend/
  app/          (routes) games · game/[id] · backtest · diagnostics
  components/   game-card · matchup-bar · probability-bar · calibration-chart · …
  lib/          api client, types generated from the API contract, formatters
```

**Dependency direction is strictly downward.** `providers` never import
`features`; `features` never import `modeling`; `api` never imports `ingestion`.
This keeps a data-source swap contained to one directory.

---

## 9. Provider abstraction

Every external source is reached through a Protocol in
`app/providers/base.py`. Concrete implementations register themselves in
`registry.py` and are selected by environment variable. A source that is not
configured resolves to an `Unavailable*Provider` that returns an explicit
`ProviderUnavailable` result — never an empty list that could be mistaken for
"no games today" and never a synthetic value.

```python
class ScheduleProvider(Protocol):
    name: str
    def fetch_schedule(self, start: date, end: date) -> ProviderResult[list[RawGame]]: ...
```

`ProviderResult` carries `status` (`OK` / `UNAVAILABLE` / `PARTIAL`),
`retrieved_at`, `source_name`, the parsed payload and the raw payload for audit.

---

## 10. Observability

* **Structured logs** — every ingest, feature build and prediction emits a JSON
  event with `job`, `source`, `rows`, `duration_ms`, `status`.
* **Data source status table** — last success, last failure, consecutive
  failures, freshness class, per source. Surfaced on the Diagnostics screen.
* **Error tracking** — Sentry DSN honoured when configured; otherwise errors are
  logged structurally. No silent failures: a failed job writes a
  `data_source_status` failure row.
* **Health endpoints** — `/health` (process), `/api/v1/diagnostics/health`
  (database, cache, source freshness, model availability).

---

## 11. Security and configuration

* All configuration flows through a validated `Settings` object
  (`pydantic-settings`). The process refuses to start on a missing or malformed
  required variable rather than defaulting silently.
* Secrets are never logged; the settings repr redacts credential fields.
* CORS is an explicit allow-list.
* The admin/diagnostics API is gated by `ADMIN_API_KEY`; when the key is unset,
  those routes are disabled rather than left open.
* No betting-related functionality ships without a licensed odds provider
  configured. Market comparison is a Phase 3 feature that stays hidden when the
  provider is absent.

---

## 12. Deployment

`docker-compose.yml` runs api, worker, scheduler, postgres, redis and web.
The API image runs migrations at start, then `uvicorn`. The worker image runs
the job runner. Both are the same image with a different command, so a build is
tested once and deployed twice.

---

## 13. Phase mapping

| Phase | Adds |
|---|---|
| **1 (this delivery)** | Architecture, full schema, provider interfaces, schedule + results + boxscore ingestion, as-of team/pitcher features, calibrated logistic model, daily games page, game detail page, walk-forward backtest, diagnostics. |
| 2 | Statcast metrics, expected/confirmed lineups, bullpen availability, weather + park factors, GBDT model, SHAP explanations. |
| 3 | Run-scoring model, Monte Carlo simulation, ensemble, advanced matchup features, licensed odds and market comparison. |
| 4 | Automated retraining, drift monitoring, advanced diagnostics, performance work, production deployment. |

Phase 1 ships end-to-end on real data. Categories that belong to later phases
are present in the schema and the API contract and report an explicit
`UNAVAILABLE` state in the UI — they are never populated with invented values.
