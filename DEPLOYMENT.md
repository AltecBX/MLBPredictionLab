# Deployment

Getting Jerry MLB Prediction Lab onto a URL you can open from your phone.

The short version is in the README: tap the Deploy button, paste one secret
into GitHub, run one workflow. This file is what that actually does, what it
costs, and what to do when something breaks.

| | Path | Effort | Cost | Best for |
|---|---|---|---|---|
| **A** | [Render + GitHub Actions](#a--render--github-actions) | 5 taps, no terminal | Free for 30 days, then ~$7/mo | Getting it live today |
| **B** | [Vercel web + Render API](#b--vercel--render) | Path A, plus ~10 minutes | Same | No cold start on the shell |
| **C** | [Your own machine or VPS](#c--self-hosted) | Docker knowledge | Hardware you have | No sleeping, no ceiling |

---

## Before you start

This is not a static site. It is a database with real MLB game logs, a model
fit on them, and a job that reissues predictions every morning. A deploy has
two distinct stages, and they are separate on purpose:

1. **Bring the services up.** Minutes. Every screen will say `UNAVAILABLE` at
   this point and nothing is wrong — the database is empty and the app is
   telling you so rather than inventing numbers to fill a screen.
2. **Seed and train.** **Budget 60–90 minutes** for two seasons on the hosted
   path, then about 9 minutes to train. One time only.

### Where that number comes from, and why an earlier one was wrong

An earlier version of this file said 30 minutes. That was measured with
Postgres on **localhost**, and it does not transfer: on the hosted path the
GitHub runner writes across the public internet to a Render instance, and the
network becomes the whole story.

Measured on the real dataset:

| | |
|---|---|
| Games, two seasons (2025 + 2026 to date) | 4,104 |
| Player game lines written | ~130,000 |
| Verbatim provider payloads | ~4,600, averaging **56 KB each** |
| Payload bytes over the wire, if kept | **~260 MB** |

The payloads are three quarters of the write volume and a quarter of a 1 GB
free tier. That is why the seed workflow **skips them by default** — see
`STORE_RAW_PAYLOADS` below. With them on, expect the runtime and the database
to roughly triple.

Local timings still hold for the self-hosted path, where the database is on the
same machine: four seasons in about 45 minutes, a training pass in 8m42s.

---

## A — Render + GitHub Actions

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/AltecBX/MLBPredictionLab)

`render.yaml` declares three resources — Postgres, the API, the web app — all
on plans that are genuinely free.

### What is not in the blueprint, and why

Two things you might expect are deliberately absent, because including either
would fail the whole blueprint on Apply:

* **No cron job.** Render has no free plan for cron jobs. The daily refresh is
  a GitHub Actions workflow instead. That is free, it lives next to the code,
  and you can trigger it from a phone.
* **No Key Value / Redis.** Also not free. The backend treats an unset
  `REDIS_URL` as *caching disabled* rather than as an error, so the only
  consequence is that every request reaches Postgres. At one reader's traffic
  that is nothing.

### 1. Create the blueprint

Tap the button, or: Render dashboard → **New** → **Blueprint** → pick this
repository → **Apply**. First build takes roughly 5–10 minutes.

### 2. Point the API's CORS at the web app

`CORS_ORIGINS` is blank in the blueprint because the web app's URL does not
exist until Render has created it. Once it does:

**jerry-api** → **Environment** → `CORS_ORIGINS` = your `jerry-web` URL, e.g.
`https://jerry-web-xxxx.onrender.com`. Save; Render redeploys the API.

### 3. Give GitHub the database URL

The workflows connect straight to Postgres, so they need its external address.

Render → **jerry-db** → copy the **External Database URL** (the external one,
not the internal — a GitHub runner is not inside Render's network).

Then: this repo → **Settings** → **Secrets and variables** → **Actions** →
**New repository secret**, named `DATABASE_URL`.

> The value Render gives you starts `postgresql://`. Paste it exactly as-is.

### 4. Seed

**Actions** tab → **Seed the database** → **Run workflow**. Leave the seasons
at `2025,2026` unless you have upgraded Postgres — see [Sizing](#sizing).

It creates the schema, backfills the seasons, fits the model, issues today's
predictions, and runs the backtest. Watch it in the Actions tab. It is
idempotent and resumable: if it fails or times out, run it again and it picks
up where it stopped rather than refetching.

### 5. Open it

Visit the **jerry-web** URL, then [add it to your home
screen](#installing-on-an-iphone).

### From then on

`.github/workflows/refresh.yml` runs at 09:15 UTC daily — after every
previous-day game is final, before the earliest first pitch. It re-ingests,
retrains, reissues predictions and prunes the raw archive. You can also trigger
it by hand from the Actions tab whenever you want fresher numbers.

It retrains on every pass rather than only when the model is stale. A CI runner
starts with an empty filesystem and `predict` loads the pickled model from
disk, so training in the same job is what puts the artifact there. It is also
the better default: a model refit on last night's completed games beats
yesterday's.

### What free actually costs you

Verify these on Render's pricing page before depending on them — they change.
As documented at the time of writing:

* **Services sleep after 15 minutes without inbound traffic**, and take about a
  minute to wake. Opening the site after a gap means a minute of loading page,
  every time. This is the single most annoying property of the free tier and
  the reason path B exists.
* **A free Postgres instance expires 30 days after creation**, with a 14-day
  grace period to upgrade before deletion. Set a reminder. This is the first
  thing worth paying for.
* **A free web service cannot receive private network traffic.** That is why
  `API_BASE_URL` points at the API's public hostname rather than its private
  one — a private address here would simply refuse the connection.
* **GitHub Actions** is free for public repositories; private ones get a
  monthly minute allowance. The daily refresh uses about 10 minutes a day.

Upgrading the two web services off free removes the sleeping. Upgrading
Postgres removes both the expiry and the 1 GB ceiling.

---

## B — Vercel + Render

Same backend, but the web app moves to Vercel, which does not sleep. Worth the
extra ten minutes if you open this several times a day: the shell — HTML, CSS,
fonts — becomes instant, and a cold API degrades to one slow section rather
than a blank tab.

1. Deploy path A first, then **suspend the `jerry-web` service** on Render.
2. Vercel → **Add New** → **Project** → import this repository. Set **Root
   Directory** to `frontend`; the Next.js preset is detected.
3. Add the environment variable
   `API_BASE_URL = https://jerry-api-xxxx.onrender.com/api/v1`.
4. Back on Render, set the API's `CORS_ORIGINS` to your Vercel URL.

Every page is `force-dynamic` and server-rendered, so the Vercel function still
waits on a cold API. What you gain is that nothing else does.

---

## C — Self-hosted

No sleeping, no size ceiling, no expiry. Needs a machine that stays on.

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

Web app on `:3000`, API docs on `:8000/docs`. Unlike the hosted path this one
does have Redis and its own scheduler: the `scheduler` service runs `daily` on
a loop, and `docker compose --profile training up -d` adds nightly retrains.

### Reaching it from your phone

On the same Wi-Fi, `http://<machine-ip>:3000` works immediately. From
anywhere, put it behind a reverse proxy with TLS — Caddy is two lines:

```
lab.example.com {
    reverse_proxy localhost:3000
}
```

**Serve it over HTTPS if you want the home-screen install.** iOS only offers a
real standalone app over a secure origin.

---

## Sizing

Measured on the real four-season dataset (2023–2026):

| Table | Size | |
|---|---|---|
| `raw_source_payloads` | 687 MB | Verbatim provider responses, deduplicated by content hash |
| `player_game_stats` | 142 MB | 333,501 rows — the foundation of every rolling statistic |
| `lineups` | 42 MB | |
| everything else | ~37 MB | |
| **Total** | **908 MB** | 10,544 games |

Roughly **230 MB per season**, three quarters of it raw payloads.

908 MB leaves no headroom in a 1 GB free Postgres instance, so:

* **Free Postgres → two seasons** (`2025,2026`, ~460 MB), which is the seed
  workflow's default. The model trains on what is there, the backtest has fewer
  walk-forward steps, and every screen still reports honest sample sizes.
* **Paid Postgres → all four.** The measured performance numbers in the README
  come from the full history.

### Statcast on top

Statcast is sized separately because it is ingested separately, by the
`statcast.yml` workflow rather than by the seed:

| | |
|---|---|
| Per pitch, `pitches` + `batted_ball_events`, including indexes | **403 bytes** |
| Per season (~712,000 pitches) | **~282 MB** |

On a free 1 GB Postgres, two seasons of schedule and box scores (~460 MB) leave
room for **one** season of Statcast and little else:

| | Schedule + box scores | Statcast | Total |
|---|---|---|---|
| Two seasons, one of Statcast | 460 MB | 282 MB | ~742 MB |
| Two seasons, two of Statcast | 460 MB | 564 MB | ~1,024 MB — **does not fit** |

Turning off `STORE_RAW_PAYLOADS` for the seed reclaims most of the 687 MB of
payloads and changes that arithmetic entirely; it is off by default in
`seed.yml` for exactly this reason. The Statcast ingest stores only the shape of
each response — row count, columns, the request — so it adds almost nothing to
that archive either way.

Raw payloads are kept on purpose: they are what makes a normalization bug
replayable without refetching, and what makes the pipeline auditable. They are
also the first thing to drop when you need space, and the reason to drop them
is disk, never correctness — every fact derived from a payload was already
normalized into its own table with its own `knowledge_time`.

`RAW_PAYLOAD_RETENTION_DAYS` (default 90) bounds the archive, and the daily
refresh enforces it on every pass. To reclaim space immediately, or to set a
tighter bound for a small database:

```bash
python -m app.cli prune --older-than-days 30
```

The cut is on `retrieved_at` — when we fetched something — and never on
`knowledge_time`, which belongs to the as-of logic and must not be entangled
with a storage decision.

---

## Installing on an iPhone

The app is a PWA. Installed, it opens without Safari's address and tab bars —
about 120px of an 844px screen back, which is the difference between seeing two
game cards and seeing one.

1. Open the site in **Safari** (Chrome on iOS cannot install web apps).
2. Tap **Share** → **Add to Home Screen**.
3. It appears as **Jerry MLB** with the app icon.

You get standalone display, the theme-aware status bar, the bottom tab bar
sitting above the home indicator, and shortcuts to Games, Backtest and Health
on a long-press of the icon.

---

## Environment variables

`.env.example` is the authoritative copy; this is the deployment-relevant
subset.

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | yes | `postgresql+psycopg://user:pass@host:5432/db`. Also the GitHub secret the workflows read |
| `REDIS_URL` | no | Blank means caching disabled, not broken |
| `CORS_ORIGINS` | yes in production | Comma-separated. The browser origin of the web app |
| `API_BASE_URL` | yes (frontend) | Where the web app fetches from. A bare hostname or a root URL is accepted and normalised |
| `ADMIN_API_KEY` | no | Guards write endpoints when set |
| `LOG_FORMAT` | no | `json` in production, `console` locally |
| `MODEL_ARTIFACT_DIR` | no | Defaults to `artifacts/models` |
| `RAW_PAYLOAD_RETENTION_DAYS` | no | Default 90. Bounds the raw archive; the daily refresh enforces it |
| `STORE_RAW_PAYLOADS` | no | Default true. Set false for a historical backfill over a network: ~56 KB per game dominates the write cost and a past game can be refetched from a stable public API. The normalized rows and their `knowledge_time` are written either way, so the model is identical |
| `LINEUP_PROVIDER` · `WEATHER_PROVIDER` · `STATCAST_PROVIDER` · `INJURY_PROVIDER` · `PARK_FACTOR_PROVIDER` · `ODDS_PROVIDER` | no | Leave unset. Each one unset makes its category report `UNAVAILABLE` in the UI, naming itself as what would enable it. **Never set one to a fake value to make a screen look complete** |

---

## Troubleshooting

**Every screen says the prediction API is unavailable.**
The web app cannot reach `API_BASE_URL`. On Render, confirm it is the API's
*public* hostname — a free service cannot receive private network traffic, so a
private address will refuse the connection. Then check the API's own logs; a
failed migration on boot looks identical from the frontend.

**The site loads but there are no games and everything is `UNAVAILABLE`.**
The database is empty — the seed has not run, or it failed. Check the Actions
tab. This state is correct behaviour, not a bug; the app will not invent data.

**First load takes a minute, then everything is fast.**
A sleeping free service waking up. Expected. Path B, or upgrade off free.

**The `jerry-web` or `jerry-api` deploy fails during build or never goes live.**
Both images are built and exercised by the `images` job in CI, so a failure
here usually means something environmental rather than a code bug — check the
Render build log first. Two failures were shipped and fixed once: `npm ci
--omit=optional`, which strips the platform-specific SWC and oxide binaries the
build needs, and a hard-coded port, which a host that assigns `PORT` will never
reach. Both are now asserted in CI.

**The seed or refresh workflow fails immediately.**
Usually the `DATABASE_URL` secret: missing, or the *internal* URL rather than
the external one. The workflow's first step says which.

**Everything worked, then one day every connection failed.**
A free Render Postgres instance expires 30 days after creation. There is a
14-day grace period to upgrade before it is deleted.

**Predictions are days old.**
The daily refresh is failing. Check the Actions tab, then the **Diagnostics**
screen — per-source freshness and the last run of every job are both on it.

**The backtest page says no report is available.**
`backtest` has not been run. Re-run the seed workflow with the backtest input
enabled, or trigger it separately. The game screens do not depend on it, though
the per-prediction reliability band does.
