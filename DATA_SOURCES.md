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
