# DATA_SOURCES — Jerry MLB Prediction Lab

Every external fact enters the system through a **provider**. A provider is a
narrow interface with one job: return normalized records plus the metadata
needed to audit them. Nothing else in the codebase issues an outbound request.

---

## 1. Provider contract

```python
class ProviderStatus(StrEnum):
    OK          = "OK"           # request succeeded, payload parsed
    PARTIAL     = "PARTIAL"      # succeeded but some records incomplete
    UNAVAILABLE = "UNAVAILABLE"  # not configured, not permitted, or failing

@dataclass(frozen=True)
class ProviderResult[T]:
    status: ProviderStatus
    source_name: str          # "mlb_statsapi"
    retrieved_at: datetime    # when we made the call (UTC)
    knowledge_time: datetime  # earliest time this fact was knowable (§4)
    data: T | None
    raw_payload: dict | None  # persisted verbatim for audit
    message: str | None       # why UNAVAILABLE / what is PARTIAL
```

Rules:

1. A provider **never** invents a value. Missing → `None` + status.
2. A provider **never** raises past its own boundary. Failures become
   `UNAVAILABLE` with a message, and a `data_source_status` row.
3. Every successful call persists `raw_payload` to `raw_source_payloads` with a
   content hash so a normalization bug can be replayed without refetching.
4. Providers are stateless and retryable. Retries use exponential backoff with
   jitter, capped; a provider that exhausts retries returns `UNAVAILABLE`.

### Interfaces

| Protocol | Methods | Phase |
|---|---|---|
| `ReferenceProvider` | `fetch_teams`, `fetch_venues`, `fetch_people` | 1 |
| `ScheduleProvider` | `fetch_schedule(start, end)` | 1 |
| `ResultsProvider` | `fetch_game_results(game_pks)`, `fetch_boxscore(game_pk)` | 1 |
| `LineupProvider` | `fetch_lineup(game_pk)` | 2 |
| `StatcastProvider` | `fetch_pitch_events(start, end)` | 2 |
| `InjuryProvider` | `fetch_injuries(as_of)` | 2 |
| `WeatherProvider` | `fetch_forecast(venue, first_pitch_utc)` | 2 |
| `ParkFactorProvider` | `fetch_park_factors(season)` | 2 |
| `OddsProvider` | `fetch_odds_snapshot(game_ids, as_of)` | 3 |

Selection is by environment variable, e.g. `WEATHER_PROVIDER=noaa`. Unset →
the `Unavailable*Provider` for that protocol, which returns `UNAVAILABLE` with
the message *"No weather provider configured. Set WEATHER_PROVIDER."* That
message is what the UI displays. No silent substitution ever occurs.

---

## 2. Source registry

| Source | Categories | Access | Phase | Status in this delivery |
|---|---|---|---|---|
| **MLB Stats API** (`statsapi.mlb.com`) | Schedule, results, boxscores, probable pitchers, lineups (from boxscore), venues, players, officials, observed weather for played games | Public HTTP JSON, no key | 1 | **Active** |
| **Baseball Savant / Statcast** | Pitch-level events, xwOBA, exit velocity, barrels, spin, movement | Public CSV/JSON export, rate-limited | 2 | Interface defined, not enabled |
| **Weather provider** (NOAA/NWS or equivalent) | Forecast temperature, wind speed/direction, humidity, precipitation | Public API, key may be required | 2 | Interface defined, not enabled |
| **Park factors** | Multi-year run/HR park factors, handedness splits | Derived in-house from ingested game data, or licensed table | 2 | Interface defined; venue geometry already ingested |
| **Injury / transactions** | IL placements, activations, scratches | MLB Stats API transactions + roster endpoints | 2 | Interface defined, not enabled |
| **Licensed odds provider** | Moneyline snapshots, opening/closing prices | Commercial, key required | 3 | Interface defined, disabled by default |

Nothing in this list is page-scraped. Every entry is an API or a structured
export. If a category has no permitted structured source, the category stays
`UNAVAILABLE` rather than being scraped from a rendered page.

---

## 3. MLB Stats API — endpoints used in Phase 1

Base: `https://statsapi.mlb.com/api/v1`

| Endpoint | Used for | Key fields consumed |
|---|---|---|
| `/teams?sportId=1&season={y}` | Team master | `id, name, abbreviation, teamName, locationName, league, division, venue` |
| `/venues?hydrate=location,fieldInfo&season={y}` | Ballpark master | `location.defaultCoordinates{lat,lon}`, `location.elevation`, `location.azimuthAngle`, `fieldInfo{roofType,turfType,capacity,leftLine,leftCenter,center,rightCenter,rightLine}` |
| `/schedule?sportId=1&startDate=&endDate=&hydrate=probablePitcher,linescore,team,venue,weather,decisions` | Games, status, probables, observed weather | `gamePk, gameDate, officialDate, status, gameType, dayNight, doubleHeader, gameNumber, seriesGameNumber, teams{away,home}{team,score,isWinner,leagueRecord,probablePitcher}, venue, weather{condition,temp,wind}` |
| `/game/{gamePk}/boxscore` | Per-player game lines, batting order, bullpen, umpires | `teams{away,home}{teamStats{batting,pitching,fielding}, players{ID*}{stats,position,battingOrder}, pitchers[], batters[], bullpen[], battingOrder[]}`, `officials[]` |
| `/people/{ids}` | Handedness, position, birthdate | `pitchHand.code, batSide.code, primaryPosition, birthDate, mlbDebutDate` |

**Terms.** The MLB Stats API returns a copyright notice with each response; it is
stored with the raw payload. The platform uses the data for analysis and
displays attribution in the UI footer. No redistribution of raw feeds occurs.

**Rate limiting.** The client applies a configurable minimum interval between
requests (default 120 ms), a bounded connection pool, exponential backoff on
`429`/`5xx`, and a per-run request budget. Boxscore backfill is chunked and
resumable.

### Why boxscores, not season-stat endpoints

The `/stats` family returns **current** season totals. Using them to build a
feature for a game played earlier in that same season would embed the outcome of
that game — and every game after it — into the input. Phase 1 therefore
reconstructs all rolling statistics from per-game boxscore lines, which carry a
game date and are trivially filterable to `game_date < as_of`. This is the
single most important data-sourcing decision in the project and is expanded in
[LEAKAGE_PREVENTION](LEAKAGE_PREVENTION.md) §3.

---

## 4. `knowledge_time` — when a fact became knowable

Every stored fact carries a `knowledge_time` in addition to `retrieved_at`.
Feature computation filters on `knowledge_time`, never on `retrieved_at`,
because backfill happens long after the fact was true.

| Fact | `knowledge_time` |
|---|---|
| Game result / boxscore | Game end time; when absent, first pitch + 3 h 30 m (conservative) |
| Probable pitcher announcement | `retrieved_at` of the first snapshot that contained it |
| Lineup | `retrieved_at` of the first snapshot showing it as confirmed |
| Injury entry | Transaction effective timestamp |
| Weather forecast | Forecast issue time |
| Observed weather | First pitch |
| Odds snapshot | Exchange/book timestamp, never our fetch time |

A backfilled 2023 boxscore fetched today gets `knowledge_time` = the 2023 game
end, so it is correctly available to a 2024 prediction and correctly invisible
to a prediction made before that 2023 game finished.

---

## 5. Freshness classification

Each category is classified `FRESH` / `AGING` / `STALE` / `UNAVAILABLE` from the
age of its most recent successful update, against the thresholds in
[ARCHITECTURE](ARCHITECTURE.md) §7. The classification is stored per source in
`data_source_status` and returned with every prediction so the UI can show
freshness beside the number it affects.

`UNAVAILABLE` is a first-class state, distinct from `STALE`:

* `STALE` — we have data, it is older than we would like.
* `UNAVAILABLE` — we have no data and the UI must say so explicitly.

---

## 6. Data completeness score

Each prediction carries a completeness score in `[0, 1]`, computed as a weighted
coverage of the categories the active model actually consumes:

| Category | Weight (Phase 1) |
|---|---|
| Schedule + teams + venue | 0.20 |
| Both starting pitchers identified | 0.25 |
| Starter workload history sufficient (≥ `min_starts`) | 0.20 |
| Team form history sufficient (≥ `min_games`) | 0.25 |
| Rest / travel derivable from schedule | 0.10 |

Weights are re-normalized over categories the active model version consumes, so
adding Phase 2 categories automatically re-weights without a code change to the
scorer. A category that is `UNAVAILABLE` contributes zero and is listed by name
in the prediction's `missing_data` array.

---

## 7. Raw payload retention

`raw_source_payloads` stores `(source_name, endpoint, request_params, payload
JSONB, content_hash, retrieved_at, knowledge_time)`. Identical consecutive
payloads deduplicate on `content_hash` so polling a slow-moving endpoint every
30 minutes does not multiply storage. Retention defaults to 90 days for
high-frequency endpoints and unlimited for boxscores, which are the training
substrate.

---

## 8. Adding a source

1. Implement the Protocol in `app/providers/<source>/`.
2. Return `ProviderResult` with correct `knowledge_time`.
3. Register in `app/providers/registry.py` under an env-var value.
4. Add its categories to `data_source_status` seeds.
5. Add a contract test with a recorded fixture payload.
6. Document the endpoints and terms in this file.

No other file changes. That is the test of whether the abstraction holds.

---

## Phase 2A: Baseball Savant (Statcast)

### Standing

| | |
|---|---|
| Source name | `baseball_savant` |
| Category | `statcast` |
| Endpoint | `https://baseballsavant.mlb.com/statcast_search/csv` |
| Format | CSV, one row per pitch — a documented, structured export, not a rendered page |
| Enabled by | `STATCAST_PROVIDER=baseball_savant` |
| Unset | Every Statcast feature reports `UNAVAILABLE`; nothing is estimated |

This is a **structured export**, which is why it satisfies the no-scraping rule.
The request is a query-string search against the same endpoint Savant's own
CSV download button uses. No HTML is parsed and no page layout is depended on.

### Join keys

The export carries MLB's own identifiers, so nothing has to be matched by name:

| Savant column | Joins to |
|---|---|
| `game_pk` | `games.id` |
| `pitcher`, `batter` | `players.id` |
| `game_date` | `games.official_date` (used only to reconcile, never to join) |

Name matching is never used. A pitch whose `game_pk` is not already in `games`
is **rejected, not invented** — the schedule ingest is the sole authority on
which games exist.

### Knowledge time

A pitch becomes knowable when it is thrown. There is no separate publication
lag to model, but Savant's export is only reliably complete after a game ends,
so ingestion is keyed to completed games and:

```
knowledge_time = game_end_utc  (falls back to first pitch + RESULT_KNOWLEDGE_LAG)
```

This is the same lag `results` already uses, for the same reason: it is when
the whole game's data became available to us, and claiming anything earlier
would let a fourth-inning pitch inform a first-inning prediction.

### Rate limiting and volume

One request per calendar date, not per game — a date returns every pitch in
every game that day. Measured on 2024-04-01: **2.67 MB, 4,189 pitches**.

| | |
|---|---|
| Pitches per full season | ~700,000 |
| Requests per full season | ~190 (one per date with games) |
| Minimum spacing | `STATCAST_MIN_INTERVAL_MS`, default 2000 |

The spacing default is deliberately an order of magnitude slower than the MLB
Stats API client's. Savant is a smaller service serving a much larger payload,
and a backfill is not urgent.

**Storage is the binding constraint, not time.** A season of pitches is roughly
120 MB before indexes. Two seasons will not fit alongside everything else in a
1 GB free Postgres — see DEPLOYMENT.md § Sizing. `ingest-statcast` therefore
takes an explicit date range rather than defaulting to all history.

### Resumability

Identical in shape to the boxscore backfill: `pending_statcast_dates()` returns
dates where *some* final regular-season game still has no pitches, so an
interrupted run picks up where it stopped and a completed date is never
refetched. A date counts as done only when **every** game on it has pitches —
"any game has pitches" would abandon the rest of a date that died halfway.

A date whose games predate Statcast tracking has no rows to find and so stays
pending forever. That is the second reason `ingest-statcast` takes an explicit
range: the caller says which seasons are tracked, rather than the system
refetching 1998 on every run.

### Reconciliation against the MLB Stats API

Savant and the box scores already ingested are two independent descriptions of
the same game, so they can be made to check each other. Every ingested game is
compared on four counts, and a mismatch is recorded as a named discrepancy —
the rows are still stored, but the affected game is identified so a feature
query can exclude it.

| Check | Statcast side | Box-score side |
|---|---|---|
| `pitch_count` | rows where `is_pitch` | Σ `pitches_thrown` |
| `strikeouts` | `pa_event` in the strikeout set | Σ `strikeouts_pitched` |
| `home_runs` | `pa_event = 'home_run'` | Σ `home_runs_allowed` |
| `balls_in_play` | rows in `batted_ball_events` | Σ (`at_bats` − `strikeouts` + `sac_flies` + `sac_bunts`) |

The fourth exists because `batted_ball_events` is the table every contact-quality
feature reads, and it should not inherit the pitch table's clean bill of health.

**Measured on 2024-07-01 … 2024-07-14 — 188 games, 55,167 pitch rows, 9,677
balls in play: zero discrepancies on all four checks.** Getting there found two
real defects, both documented in DATABASE_SCHEMA.md: `automatic_ball` /
`automatic_strike` rows are not pitches (14 of the first 30 games disagreed
until they were excluded), and Statcast measures fouls, so a batted ball has to
be defined by `description` rather than by having a launch measurement (98 per
game before the fix, 51.5 after).

The tolerance for the discrete checks is one event, to absorb a later scoring
correction. The pitch-count tolerance is 0.5%; before the `is_pitch` fix it had
been set to 2%, which is exactly the kind of number that hides a real defect
behind a plausible-looking allowance.

---

## Sources used for research, not for data

These informed which statistics are worth computing. **No production code
fetches from any of them**, and a test asserts their hostnames appear in no
provider module.

| Source | Used for | Why it is not a provider |
|---|---|---|
| **FanGraphs** | Definitions of wRC+, FIP, xFIP, plate discipline, park-factor regression | Definitions are ideas; the equivalents here are recomputed from Statcast and box-score data we already hold. Depending on their pages would be fragile and is not licensed for this |
| **Baseball Reference** | Manual validation and historical sanity checks only | No data permission has been obtained. Nothing from it trains the model |
| **TeamRankings** | Which team comparisons are worth showing | Terms do not clearly permit automated commercial use. Every comparable statistic is recomputed from our own data |
| **ZionC27 MLB Game Winner Predictor** | Confirmation that xwOBA, hard-hit rate, barrel rate, launch speed, bat speed, velocity, spin and whiff rate are worth measuring, and that gradient boosting is worth *testing* | Concepts only. No architecture, notebook, trained model, dataset or voting scheme is reused. Its accuracy-only evaluation and uncalibrated probabilities are explicitly rejected — this system ranks calibration and proper scoring above accuracy, and has already measured and rejected a boosted model on its own evidence (MODELING_PLAN.md) |
