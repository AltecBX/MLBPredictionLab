# Jerry MLB Prediction Lab

A transparent, historically validated MLB win-probability platform. For every
scheduled game it produces a **calibrated probability**, not a pick — and every
number traces back to a stored, timestamped, reproducible record.

---

## Get it running

The whole thing runs from GitHub: no server, no hosted database, nothing to
pay for and nothing that expires. Two steps, both doable from a phone:

1. **Run the seed.** Actions tab → **Seed the database** → Run workflow.
   It backfills six seasons of MLB history into a database that lives inside
   the job, trains the model, issues today's predictions, runs the backtest,
   and saves the database as the repository's `data` release. About an hour,
   once.
2. **Open the site** at `https://<you>.github.io/MLBPredictionLab/`, then
   Share → Add to Home Screen.

From then on it keeps itself current. `.github/workflows/refresh.yml`
re-ingests, retrains and reissues predictions every morning at 09:15 UTC;
`pregame.yml` polls lineups and the forecast hourly through the evening; and
`pages.yml` republishes the site after each of them. Every one of those jobs
restores the database from the `data` release, does its work, and saves it
back — see [DEPLOYMENT.md](DEPLOYMENT.md) for how that works and what it
replaced.

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
| [DEPLOYMENT.md](DEPLOYMENT.md) | Getting it onto a URL: Render one-click, Vercel + Render, or self-hosted |

---

## What is built

**Phase 1 is complete and runs end-to-end on real MLB data.**

* Full database schema (28 tables), applied by migration.
* Provider interfaces with a live MLB Stats API implementation and explicit
  `Unavailable*` providers for every category that is not yet configured.
* Schedule, probable-pitcher, game-result and full-boxscore ingestion —
  idempotent, chunked, resumable, rate-limited and concurrent.
* As-of feature layer: 46 features rebuilt from dated game logs, each carrying
  its sample size and an estimated flag — including four multi-season
  projections that pool a team's or a starter's prior seasons with the season
  in progress.
* Calibrated L2 logistic model with walk-forward hyperparameter selection and an
  Elo reference model for the disagreement signal.
* Immutable prediction records with per-feature contributions in probability
  points, multi-signal confidence, and per-category freshness.
* Walk-forward backtest with calibration reporting, eight slice dimensions,
  feature-group ablation and automatic leakage tripwires.
* Daily Game Center, Game Detail (ten tabs), Backtest and Diagnostics screens.
* Mobile-first interface: installable to an iPhone home screen, thumb-reachable
  bottom navigation, 44pt touch targets everywhere, no horizontal scroll on any
  route at any iPhone width.
* 139 backend tests, 30 frontend unit tests and 22 end-to-end tests, ten of
  which are an explicit iPhone layout contract — plus a CI job that builds both
  container images and asserts they serve on a host-assigned port.

### Deliberately not populated

These are schema-complete and API-complete, and the UI reports them as
`UNAVAILABLE` naming the source that would enable them. **They are never filled
with placeholder numbers.**

Statcast metrics · pregame lineup confirmation · per-pitcher bullpen
availability · forecast weather · empirical park factors · umpire profiles ·
injuries · odds, market comparison, ROI and CLV · Monte Carlo simulation ·
run-scoring model · model ensemble.

---

## Running it on the web

`render.yaml` deploys the whole stack — Postgres, Redis, API, web app and the
daily job — from one blueprint.
[**DEPLOYMENT.md**](DEPLOYMENT.md) covers that path, a Vercel + Render split for
the fastest phone experience, and self-hosting, with the measured sizing and
timing numbers each one needs.

Two things worth knowing before you start:

* A fresh deploy comes up **empty**, and every screen will say `UNAVAILABLE`.
  That is correct. Seeding is a separate, deliberate step.
* **Budget 60–90 minutes for the seed** on the hosted path, then ~9 minutes to
  train. The runner writes across the public internet to a hosted database, and
  that network is the whole cost — locally, with Postgres on the same machine,
  four seasons takes 45 minutes. The seed workflow skips storing verbatim
  provider payloads by default because at 56 KB a game they are three quarters
  of the write volume; DEPLOYMENT.md explains the tradeoff.
* Four seasons lands at **908 MB**, which does not fit a 1 GB free Postgres
  tier. Two seasons do.

---

## On a phone

The interface is built for an iPhone first, because that is where it is
actually read.

* **Installable.** Safari → Share → Add to Home Screen gives a standalone app
  with no browser chrome — roughly 120px of an 844px screen back.
* **One-row header, bottom tab bar.** The four destinations sit within thumb
  reach instead of at the top of the screen.
* **Sticky date bar and tab strip.** Changing date, and moving between a game's
  ten sections, never require scrolling back up past a twelve-game slate.
* **44pt touch targets throughout**, including the inline info icons, which
  expand their hit area without disturbing the text they sit in.
* **Tooltips open on tap** and render as a sheet above the tab bar rather than
  a popover that would be clipped at the screen edge.
* **Safe-area aware**, so nothing hides under the notch or the home indicator.

These are enforced, not asserted: `frontend/e2e/mobile.spec.ts` fails the build
on horizontal page scroll at 375px or 390px, on any control under 44pt, on a
sticky layer covering another, and on a broken home-screen install.

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
46 features, each with (value, sample size, estimated flag)
   │  shrinkage toward stated league/team baselines, and toward
   │  each team's and starter's own prior seasons
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

**What is served now.** The probability on the screen is a log-odds blend of
the calibrated logistic model and a run simulation. Both halves were changed
on the evidence in [MODELING_PLAN.md](MODELING_PLAN.md) § Multi-season
projections, and the change is measured paired, per game, on the same 6,900
games — every regular-season game from April 2024 to September 2026, trained
from 2021, regularisation pinned:

| | Log loss | Brier | Calibration error | Accuracy | AUC |
|---|---|---|---|---|---|
| Previous served combination | 0.68334 | 0.24516 | 1.20% | 55.0% | 0.568 |
| **Served now** | **0.68135** | **0.24418** | **0.93%** | **55.8%** | **0.578** |
| Always 50% | 0.69315 | 0.25000 | | 50.0% | 0.500 |
| Market closing line, achievable floor | 0.679–0.681 | 0.243–0.244 | | 56.4–57.0% | |

Δ log loss +0.00198, paired 95% interval [+0.00078, +0.00322], positive in
every season. The gap to the market's floor was roughly halved. No game is a
lock; the highest probability the served model has emitted on those three
seasons is 87.6%, and it went past the market's forty-two-year maximum of
73.7% on 0.06% of games.

**The logistic model alone**, walk-forward over four seasons of real MLB
games (2023–2026) on the previous feature set `fs_v1`, trained only on games
before each prediction date — kept as the reference the ablation below reads
against:

| Metric | Value | Reference |
|---|---|---|
| Games evaluated | 8,134 (26 steps; 14 early steps skipped for too little training data) | |
| **Log loss** | **0.6845** | 0.6931 = always 50% · 0.679–0.681 = market close |
| **Brier score** | **0.2457** | |
| **Calibration error** | **1.12%** (max 6.72%) | |
| Accuracy | 55.6% | ~58–60% = market close |
| ROC AUC | 0.570 | |

Reliability by probability band — the honest answer to "how often does a 62%
pick actually win?":

| Band (favorite) | Games | Model said | Actually won | Gap |
|---|---|---|---|---|
| 50–55% | 3,634 | 52.5% | 52.9% | +0.4 |
| 55–60% | 2,714 | 56.9% | 55.4% | −1.5 |
| 60–65% | 1,081 | 62.1% | 58.5% | −3.7 |
| 65–70% | 505 | 66.9% | 64.0% | −2.9 |
| 70–75% | 153 | 71.9% | 67.3% | −4.6 |
| 75%+ | 47 | 78.0% | 72.3% | −5.7 |

The model is well calibrated where most of the volume sits and **mildly
overconfident in its strongest picks**. That is a real, measured limitation, not
a rounding artefact: the per-step Platt calibrator is fit on a 45-day validation
window, which is thin in the tails. It is surfaced in the product rather than
smoothed over — the backtest page shows this table with its gap column, and the
game detail page links each prediction to the band it falls in.

### The ensemble question, answered

A gradient-boosted component was built and evaluated walk-forward against the
logistic model on the same 8,339 out-of-sample games. It loses on every metric
(log loss 0.6895 vs 0.6842), and the out-of-sample blend-weight search — over a
grid that includes zero so the null hypothesis can win — chose **zero**. Log
loss rises monotonically as the boosted component gains weight.

So the logistic model is served unchanged. That is the rule working: a
component is kept only when it improves out-of-sample performance, and this one
does not. On ~9,000 rows of 42 correlated, already-shrunk rate differences
there is little interaction structure left for trees to find. Re-run it with
`make ensemble-check` when Statcast adds genuinely non-linear inputs;
[MODELING_PLAN.md](MODELING_PLAN.md) has the full table.

### What the ablation actually found

Refitting the whole walk-forward once per feature group, plus a second pass
using each group alone:

| Group | Δ log loss if removed | Log loss alone | Reading |
|---|---|---|---|
| Starting pitching | **+0.0022** | 0.6904 | Unique signal |
| Team strength | +0.0006 | **0.6865** | Strongest single cluster |
| Offense / bullpen / form / defense | +0.0003 … −0.0001 | 0.6908 … 0.6916 | Redundant given team strength |
| Rest and travel | −0.0003 | 0.6932 | No standalone signal |
| Ballpark attributes | −0.0001 | 0.6946 | No standalone signal |
| Head-to-head | −0.0000 | 0.6981 | Worse than a coin flip alone |

Leave-one-out alone would have called almost everything "neutral", because
several groups encode the same thing — team quality. The group-alone column is
what separates *redundant* from *worthless*. Rest/travel, the physical ballpark
attributes and head-to-head are the three that fail both tests. Head-to-head is
the sharpest result: on its own it is *worse than a coin flip*, which is exactly
the small-sample trap the k=40 shrinkage exists to contain — and evidence that
the constraint is doing its job. All three are kept because the platform is
required to account for them and they are useful context for a reader, but the
finding is recorded rather than buried, and they are the first things Phase 2's
weather and empirical park factors should replace.

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
ARCHITECTURE.md …                design documents (8) + DEPLOYMENT.md
backend/
  app/core/       validated settings, structured logging, cache, clock
  app/db/         SQLAlchemy models for all 28 tables + upsert helper
  app/providers/  Protocols, MLB Stats API client, explicit Unavailable providers
  app/ingestion/  reference · schedule · results · runner · status · maintenance
  app/features/   as-of store · shrinkage · Elo · aggregates · registry · builder
  app/modeling/   dataset · logistic · calibration · artifact registry · training
  app/backtest/   walk-forward · metrics · slices · ablation · engine
  app/services/   prediction · confidence · explanation · freshness · diagnostics
  app/api/v1/     games · backtest · diagnostics · meta
  app/cli.py      every operational job
  tests/          unit · feature · leakage · model · backtest · API · maintenance
frontend/
  app/            game center · game detail · backtest · diagnostics · methodology
  components/     game card · probability bar · matchup bars · calibration chart …
  lib/            API client, contract types, formatters
  manifest.ts     + public/ icons — installable home-screen app
  tests/ e2e/     component tests · daily-workflow suite · iPhone layout suite
docker-compose.yml · render.yaml · Makefile · .env.example
```

---

## Testing

```bash
make test          # backend + frontend
make test-backend  # pytest: unit, feature, leakage, model, backtest, API
make test-frontend # vitest component tests
make e2e           # Playwright: daily-workflow + iPhone layout suites
make lint          # ruff
make typecheck     # tsc
```

The leakage suite is the one to run after any refactor of the feature layer.
`e2e/mobile.spec.ts` is the one to run after any layout change — it is the only
thing standing between a stray `whitespace-nowrap` and a page that scrolls
sideways on a phone.

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
