# Jerry MLB Prediction Lab

A transparent, historically validated MLB win-probability platform. For every
scheduled game it produces a **calibrated probability**, not a pick — and every
number traces back to a stored, timestamped, reproducible record.

---

## The seven questions

The product exists to answer these, in this order:

| Question | Where |
|---|---|
| Which team is more likely to win? | Game card headline |
| What is the probability? | Calibrated probability, both sides |
| Why does the model favor that team? | Top five drivers for, top five against, in probability points |
| What information is uncertain? | Completeness, per-source freshness, explicit risk list |
| How reliable have similar predictions been? | Backtest evidence for this prediction's probability band |
| What changed since the previous prediction? | Diff against the prior immutable snapshot |
| Which model inputs had the greatest impact? | Full contribution table |

If a statistic cannot be traced to a prediction contribution, it is not on the
screen.

---

## Design documents

Written before any code, and kept current:

| Document | Contents |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Components, data flow, engineering rules, freshness and caching policy |
| [DATA_SOURCES.md](DATA_SOURCES.md) | Provider contract, source registry, `knowledge_time` semantics, completeness scoring |
| [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) | Every table, column and invariant |
| [FEATURE_DICTIONARY.md](FEATURE_DICTIONARY.md) | Every feature, its window, shrinkage constant and minimum sample |
| [MODELING_PLAN.md](MODELING_PLAN.md) | The five models, ensembling, calibration, confidence, recommendation labels |
| [LEAKAGE_PREVENTION.md](LEAKAGE_PREVENTION.md) | Twelve leakage vectors, the mechanism blocking each, and the test proving it |
| [BACKTEST_PLAN.md](BACKTEST_PLAN.md) | Walk-forward protocol, metrics, slices, ablation, sanity gates |
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | Phase boundaries and acceptance criteria |

---

## What is built

**Phase 1 is complete and runs end-to-end on real MLB data.**

* Full database schema (28 tables), applied by migration.
* Provider interfaces with a live MLB Stats API implementation and explicit
  `Unavailable*` providers for every category that is not yet configured.
* Schedule, probable-pitcher, game-result and full-boxscore ingestion —
  idempotent, chunked, resumable, rate-limited and concurrent.
* As-of feature layer: 42 features rebuilt from dated game logs, each carrying
  its sample size and an estimated flag.
* Calibrated L2 logistic model with walk-forward hyperparameter selection and an
  Elo reference model for the disagreement signal.
* Immutable prediction records with per-feature contributions in probability
  points, multi-signal confidence, and per-category freshness.
* Walk-forward backtest with calibration reporting, eight slice dimensions,
  feature-group ablation and automatic leakage tripwires.
* Daily Game Center, Game Detail (ten tabs), Backtest and Diagnostics screens.
* 106 backend tests and 23 frontend component tests, plus an end-to-end suite.

### Deliberately not populated

These are schema-complete and API-complete, and the UI reports them as
`UNAVAILABLE` naming the source that would enable them. **They are never filled
with placeholder numbers.**

Statcast metrics · pregame lineup confirmation · per-pitcher bullpen
availability · forecast weather · empirical park factors · umpire profiles ·
injuries · odds, market comparison, ROI and CLV · Monte Carlo simulation ·
run-scoring model · model ensemble.

---

## Quick start

### With Docker

```bash
cp .env.example .env          # edit if you want a non-default database
docker compose up --build     # postgres, redis, api, scheduler, web
```

Then seed the data and train:

```bash
docker compose exec api python -m app.cli bootstrap
docker compose exec api python -m app.cli ingest-history --seasons 2023,2024,2025,2026
docker compose exec api python -m app.cli train
docker compose exec api python -m app.cli backtest
docker compose exec api python -m app.cli predict
```

Web app on <http://localhost:3000>, API docs on <http://localhost:8000/docs>.

### Locally

```bash
make install                                       # venv + npm install
cp .env.example .env                               # point DATABASE_URL at your Postgres
make migrate bootstrap
make ingest-history SEASONS=2023,2024,2025,2026    # ~45 min, ~10,500 games
make train
make backtest
make predict
make dev                                           # API on :8000, web on :3000
```

`make help` lists every target.

---

## How a prediction is built

```
MLB Stats API
   │  provider interface, rate limited, raw payload stored with a content hash
   ▼
games · team_game_stats · player_game_stats          ← one row per game, dated
   │  as-of filter: knowledge_time <= T  AND  game_date < T
   ▼
42 features, each with (value, sample size, estimated flag)
   │  shrinkage toward stated league/team baselines
   ▼
L2 logistic regression → calibrator → probability
   │  contributions in probability points, Elo reference for agreement
   ▼
immutable prediction snapshot + explanation + warnings
   │
   ▼
API (read-only; never computes a prediction inside a GET) → Next.js
```

### The one decision that matters most

The MLB `/stats` endpoints return **current** season totals. Attaching those to
a game played in April would embed that game's own result — and every game
since — into its own input. That single mistake can inflate backtest accuracy by
ten points and is the most common way a sports model silently becomes worthless.

So this platform never consumes them. Every rolling statistic is reconstructed
from per-game boxscore lines, which carry a game date and are trivially
filterable to a strict as-of cut. The HTTP client refuses those endpoints
outright, and a test asserts it.

---

## Measured performance

Walk-forward over four seasons of real MLB games (2023–2026), trained only on
games before each prediction date:

| Metric | Value | Reference |
|---|---|---|
| Games evaluated | 8,339 | |
| **Log loss** | **0.6840** | 0.6931 = always 50% · ~0.65 = market close |
| **Brier score** | **0.2455** | |
| **Calibration error** | **0.91%** | |
| Accuracy | 55.7% | ~58–60% = market close |
| ROC AUC | 0.572 | |

Read those numbers honestly: the edge over a coin flip is real but modest, and
that is what an MLB model without Statcast, lineups or weather should look like.
The backtest's sanity gates flag any run that claims better than the market's
closing line, because such a claim is nearly always leakage rather than skill.
No gate is tripped by this model, and no single feature carries more than 8.8%
of its weight.

---

## Leakage prevention

Twelve vectors, each with a mechanism and a test — see
[LEAKAGE_PREVENTION.md](LEAKAGE_PREVENTION.md). The load-bearing ones:

* **Two timestamps on every fact.** `knowledge_time` (when it became knowable)
  and `retrieved_at` (when we fetched it). Feature queries filter on the first
  and never the second, so a boxscore backfilled in 2026 is correctly visible to
  a 2024 prediction and correctly invisible before that game ended.
* **The target game is structurally absent.** `GameContext` has no outcome
  fields, so a builder cannot reference the result even by accident. A test
  proves a feature vector is bit-identical whether or not the game's own rows
  exist in the database.
* **Scaler, imputer and calibrator are fit inside the training fold only.**
* **No random cross-validation anywhere.** A static scan fails the build on
  `KFold`, `GridSearchCV`, `train_test_split` and friends inside the modeling
  and backtest packages.
* **Elo exports only the pre-game rating.**

---

## Repository layout

```
ARCHITECTURE.md …                design documents (8)
backend/
  app/core/       validated settings, structured logging, cache, clock
  app/db/         SQLAlchemy models for all 28 tables + upsert helper
  app/providers/  Protocols, MLB Stats API client, explicit Unavailable providers
  app/ingestion/  reference · schedule · results · runner · source status
  app/features/   as-of store · shrinkage · Elo · aggregates · registry · builder
  app/modeling/   dataset · logistic · calibration · artifact registry · training
  app/backtest/   walk-forward · metrics · slices · ablation · engine
  app/services/   prediction · confidence · explanation · freshness · diagnostics
  app/api/v1/     games · backtest · diagnostics · meta
  app/cli.py      every operational job
  tests/          unit · feature · leakage · model · backtest · API integration
frontend/
  app/            game center · game detail · backtest · diagnostics · methodology
  components/     game card · probability bar · matchup bars · calibration chart …
  lib/            API client, contract types, formatters
  tests/ e2e/     component tests and the daily-workflow end-to-end suite
docker-compose.yml · Makefile · .env.example
```

---

## Testing

```bash
make test          # backend + frontend
make test-backend  # pytest: unit, feature, leakage, model, backtest, API
make test-frontend # vitest component tests
make e2e           # Playwright daily-workflow suite
make lint          # ruff
make typecheck     # tsc
```

The leakage suite is the one to run after any refactor of the feature layer.

---

## Data and attribution

Game data comes from the **MLB Stats API**, a public structured JSON source. No
page scraping is used anywhere; a category with no permitted structured source
stays `UNAVAILABLE` rather than being scraped from a rendered page. Raw
responses are stored verbatim with their copyright notice, deduplicated by
content hash, so a normalization bug can be replayed without refetching.

Probabilities are model estimates. No game is a lock — the words "lock",
"guaranteed" and "sure thing" are banned by test, and the strongest label the
system will emit is *Strong lean*.
