# Deployment

Getting Jerry MLB Prediction Lab onto a URL you can open from your phone.

Three paths, in order of how little work they are. All three end with the same
thing: a web address, an installable home-screen app, and a daily job that keeps
the predictions current.

| | Path | Effort | Cost | Best for |
|---|---|---|---|---|
| **A** | [Render blueprint](#a--render-one-click) | One click plus a seed command | Free to start | Getting it live today |
| **B** | [Vercel web + Render API](#b--vercel--render) | ~15 minutes | Free to start | The fastest phone experience |
| **C** | [Your own machine or VPS](#c--self-hosted) | Docker knowledge | Hardware you already have | Full control, no cold starts |

---

## Before you start: what "deployed" actually means here

This is not a static site. It is a database with four seasons of real MLB game
logs, a model fit on them, and a job that reissues predictions every morning. A
deploy has two distinct stages:

1. **Bring the services up.** Minutes. Nothing is wrong at this point if every
   screen says `UNAVAILABLE` — the database is empty and the app is telling you
   the truth about that rather than showing you invented numbers.
2. **Seed and train.** Roughly 45 minutes of ingestion for four seasons, then a
   training run. Both are measured, not estimated: ingestion pulls 10,544 games
   and 333,501 player game lines, and a full training pass takes **8m42s**.

   This is a one-time cost. Afterwards the daily job re-runs ingestion for the
   schedule window only — seconds — plus the same training pass, so budget
   about **ten minutes a day** for it.

Skipping stage 2 leaves you with a correct, honest, and completely empty app.

---

## A — Render, one click

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/AltecBX/MLBPredictionLab)

`render.yaml` in the repository root declares everything: Postgres, a Key Value
(Redis) store, the API, the web app, and the daily job.

### 1. Create the blueprint

Render dashboard → **New** → **Blueprint** → pick this repository → **Apply**.
Render reads `render.yaml` and creates all five resources. First build takes
roughly 5–10 minutes.

### 2. Point the API's CORS at the web app

`CORS_ORIGINS` is deliberately left blank in the blueprint, because the web
app's URL does not exist until Render has created it. Once it does:

**jerry-api** → **Environment** → set

```
CORS_ORIGINS = https://jerry-web-xxxx.onrender.com
```

using your actual web URL. Save; Render redeploys the API.

> Server-rendered pages fetch over Render's private network and work without
> this. It matters for anything the browser requests directly, so set it.

### 3. Seed the database

**jerry-api** → **Shell** (or run the same commands as a one-off job):

```bash
python -m app.cli bootstrap
python -m app.cli ingest-history --seasons 2025,2026   # ~20 min; see Sizing
python -m app.cli train                                 # ~9 min
python -m app.cli predict                               # seconds
python -m app.cli backtest                              # optional, ~10 min
```

`ingest-history` is idempotent and resumable. If the shell disconnects, run it
again — it picks up where it stopped rather than refetching.

### 4. Open it on your phone

Visit the **jerry-web** URL, then [add it to your home
screen](#installing-on-an-iphone).

### What free costs you on Render

Verify current limits on Render's pricing page before depending on any of this —
they change. At the time of writing:

- **Services sleep after ~15 minutes idle.** The first request after that takes
  roughly 30–60 seconds while the container wakes. On a phone this reads as a
  hung page. It is the single most annoying free-tier property, and the reason
  path B exists.
- **Free Postgres is 1 GB and time-limited.** Render expires free database
  instances after a set period. If this becomes something you rely on daily,
  the paid Postgres tier is the first thing worth spending money on.
- **The daily cron runs on Render's schedule**, in UTC. `render.yaml` sets
  09:15 UTC, which is after every previous-day game is final and before the
  earliest first pitch. It takes about ten minutes, nearly all of it training.

Upgrading the API and web services off free removes the cold starts. Upgrading
Postgres removes the size ceiling and the expiry.

---

## B — Vercel + Render

The web app on Vercel, everything else on Render. More setup, and worth it: the
Next.js frontend gets Vercel's edge network and never sleeps, so opening the
site on your phone is instant even when the API underneath is cold.

### 1. Backend on Render

Deploy the blueprint as in path A, then **delete or suspend the `jerry-web`
service** — Vercel is serving that role.

The API must be publicly reachable for this split to work, which it is by
default.

### 2. Frontend on Vercel

Vercel dashboard → **Add New** → **Project** → import this repository.

| Setting | Value |
|---|---|
| Root Directory | `frontend` |
| Framework Preset | Next.js (detected) |
| Build Command | *(default)* |

Environment variable:

```
API_BASE_URL = https://jerry-api-xxxx.onrender.com/api/v1
```

### 3. Close the loop

Back on Render, set the API's `CORS_ORIGINS` to your Vercel URL
(`https://your-project.vercel.app`, plus any custom domain).

Every page in this app is `force-dynamic` and server-rendered, so the Vercel
function still waits on a cold API. What you gain is that the *shell* — HTML,
CSS, fonts — is always instant, and a cold backend degrades to a slow section
rather than a blank tab.

---

## C — Self-hosted

No cold starts, no size ceiling, no vendor. Needs a machine that stays on.

```bash
git clone https://github.com/AltecBX/MLBPredictionLab.git
cd MLBPredictionLab
cp .env.example .env          # set POSTGRES_PASSWORD at minimum
docker compose up --build -d  # postgres, redis, api, scheduler, web
```

Seed it:

```bash
docker compose exec api python -m app.cli bootstrap
docker compose exec api python -m app.cli ingest-history --seasons 2023,2024,2025,2026
docker compose exec api python -m app.cli train
docker compose exec api python -m app.cli predict
docker compose exec api python -m app.cli backtest
```

Web app on `:3000`, API docs on `:8000/docs`. The `scheduler` service already
runs `daily` on a loop; add the `training` profile
(`docker compose --profile training up -d`) for nightly retrains.

### Reaching it from your phone

On the same Wi-Fi, `http://<machine-ip>:3000` works immediately. From
anywhere, put it behind a reverse proxy with TLS — Caddy is two lines:

```
lab.example.com {
    reverse_proxy localhost:3000
}
```

**Serve it over HTTPS if you want the home-screen install.** iOS only offers
"Add to Home Screen" as a real standalone app over a secure origin.

---

## Sizing

Measured on the real four-season dataset (2023–2026), not estimated:

| Table | Size | |
|---|---|---|
| `raw_source_payloads` | 687 MB | Verbatim provider responses, deduplicated by content hash |
| `player_game_stats` | 142 MB | 333,501 rows — the foundation of every rolling statistic |
| `lineups` | 42 MB | |
| everything else | ~37 MB | |
| **Total** | **908 MB** | 10,544 games |

Roughly **230 MB per season**, three quarters of it raw payloads.

That total does not fit in a 1 GB free Postgres instance with any headroom, so:

- **Free Postgres → ingest two seasons** (`--seasons 2025,2026`, ~460 MB). The
  model trains on what is there, the walk-forward backtest has fewer steps, and
  every screen still reports honest sample sizes.
- **Paid Postgres → ingest all four.** The measured performance numbers in the
  README come from the full history.

Raw payloads are kept on purpose: they are what makes a normalization bug
replayable without refetching, and what makes the pipeline auditable. They are
also the first thing to drop when you need the space, and the reason to drop
them is disk, never correctness — every fact derived from a payload was already
normalized into its own table with its own `knowledge_time`.

`RAW_PAYLOAD_RETENTION_DAYS` (default 90) bounds the archive, and the daily job
enforces it on every pass. To reclaim space immediately, or to set a tighter
bound for a small database:

```bash
python -m app.cli prune --older-than-days 30
```

The cut is on `retrieved_at` — when we fetched something — and never on
`knowledge_time`, which belongs to the as-of logic and must not be entangled
with a storage decision.

---

## Installing on an iPhone

The app is a PWA. Installed, it opens without Safari's address and tab bars —
about 120px of a 844px screen back, which is the difference between seeing two
game cards and seeing one.

1. Open the site in **Safari** (Chrome on iOS cannot install web apps).
2. Tap **Share** → **Add to Home Screen**.
3. It appears as **Jerry MLB** with the app icon.

You get standalone display, the theme-aware status bar, the bottom tab bar
sitting above the home indicator, and shortcuts to Games, Backtest and Health
on a long-press of the icon.

---

## Environment variables

Everything the backend reads. `.env.example` is the authoritative copy;
this is the deployment-relevant subset.

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | yes | `postgresql+psycopg://user:pass@host:5432/db` |
| `REDIS_URL` | no | Blank disables caching rather than failing startup |
| `CORS_ORIGINS` | yes in production | Comma-separated. The browser origin of the web app |
| `API_BASE_URL` | yes (frontend) | Where the web app fetches from, including `/api/v1` |
| `ADMIN_API_KEY` | no | Guards write endpoints when set |
| `LOG_FORMAT` | no | `json` in production, `console` locally |
| `MODEL_ARTIFACT_DIR` | no | Defaults to `artifacts/models` |
| `RAW_PAYLOAD_RETENTION_DAYS` | no | Default 90. Bounds the raw archive; the daily job enforces it |
| `LINEUP_PROVIDER` · `WEATHER_PROVIDER` · `STATCAST_PROVIDER` · `INJURY_PROVIDER` · `PARK_FACTOR_PROVIDER` · `ODDS_PROVIDER` | no | Leave unset. Each one unset makes its category report `UNAVAILABLE` in the UI, naming itself as what would enable it. **Never set one to a fake value to make a screen look complete** |

---

## Model artifacts and ephemeral filesystems

`predict` loads a pickled model from the path recorded in `model_versions`.
Render cron jobs get a fresh, empty filesystem every run, so a job that runs
`predict` alone would find nothing there.

This is why the blueprint's daily job runs `train` before `predict` in the same
command. It is not a workaround so much as the better default — a model refit
on last night's completed games beats yesterday's.

Training is the expensive part of the daily job — 8m42s of the roughly ten
minutes. If you would rather train weekly and predict daily, attach a
persistent disk to a background worker instead of using a cron job, mount it at
`MODEL_ARTIFACT_DIR`, and split the schedule. That needs a paid instance,
because Render disks are not available on free plans.

---

## Troubleshooting

**Every screen says the prediction API is unavailable.**
The web app cannot reach `API_BASE_URL`. On Render, check that the value
resolves on the private network; on Vercel, that it is the full public URL
*including* `/api/v1`. Then check the API's own logs — a failed migration on
boot looks identical from the frontend.

**The site loads but every category is `UNAVAILABLE` and there are no games.**
The database is empty. Run the seed commands. This state is correct behaviour,
not a bug — the app will not invent data to fill a screen.

**First load takes 40 seconds, then everything is fast.**
A sleeping free service waking up. Expected; see path B, or upgrade off free.

**`ingest-history` stopped partway.**
Run it again. It is idempotent and resumable, and it will not refetch what it
already stored.

**Predictions exist but are days old.**
The daily job is not running or is failing. Check its logs, then the
**Diagnostics** page — per-source freshness and the last run of every job are
both on it.

**The backtest page says no report is available.**
`backtest` has not been run. It is optional and takes about ten minutes; the
game screens do not depend on it, though the per-prediction reliability band
does.
