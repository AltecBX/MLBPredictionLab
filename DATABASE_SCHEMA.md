# DATABASE_SCHEMA — Jerry MLB Prediction Lab

PostgreSQL 16. All timestamps are `TIMESTAMPTZ` in UTC. All tables that hold
external facts carry `source_name`, `retrieved_at` and `knowledge_time`.

Legend — **P** phase in which the table is populated. Tables marked P2/P3 are
created in Phase 1 (schema-complete) and remain empty until their provider is
enabled; the API reports their categories as `UNAVAILABLE` rather than inventing
rows.

---

## 1. Reference

### `teams` — P1
| Column | Type | Notes |
|---|---|---|
| `id` | `int` PK | MLB team id (stable) |
| `name` | `text` NOT NULL | "New York Yankees" |
| `abbreviation` | `text` NOT NULL | "NYY" |
| `team_name` | `text` | "Yankees" |
| `location_name` | `text` | "Bronx" |
| `league_id` / `league_name` | `int` / `text` | |
| `division_id` / `division_name` | `int` / `text` | |
| `home_venue_id` | `int` FK → `ballparks.id` | |
| `first_year_of_play` | `int` | |
| `active` | `bool` NOT NULL DEFAULT true | |
| `source_name`, `retrieved_at`, `knowledge_time` | | |

### `ballparks` — P1
| Column | Type | Notes |
|---|---|---|
| `id` | `int` PK | MLB venue id |
| `name` | `text` NOT NULL | |
| `city`, `state`, `country` | `text` | |
| `latitude`, `longitude` | `double precision` | drives travel distance + weather lookup |
| `elevation_ft` | `int` | air-density input |
| `azimuth_angle` | `double precision` | home-plate→CF bearing; converts wind direction to field-relative |
| `roof_type` | `text` | Open / Dome / Retractable |
| `turf_type` | `text` | |
| `capacity` | `int` | |
| `lf_line`, `lf_center`, `center`, `rf_center`, `rf_line` | `int` | fence distances (ft) |
| `timezone` | `text` | for local start time and day/night logic |
| `source_name`, `retrieved_at`, `knowledge_time` | | |

### `park_factors` — P2
`(id PK, venue_id FK, season int, factor_type text, handedness char(1) NULL,
value numeric(6,3), sample_games int, method text, is_estimated bool,
source_name, retrieved_at, knowledge_time)`
`UNIQUE(venue_id, season, factor_type, handedness)`.
`factor_type ∈ {runs, hr, hits, doubles, triples, k, bb}`. `method` records
whether the value is licensed, in-house multi-year regressed, or league-neutral.

### `players` — P1
| Column | Type | Notes |
|---|---|---|
| `id` | `int` PK | MLB person id |
| `full_name` | `text` NOT NULL | |
| `primary_position` | `text` | abbreviation |
| `position_type` | `text` | Pitcher / Hitter / Catcher |
| `bat_side` | `char(1)` | L / R / S |
| `pitch_hand` | `char(1)` | L / R |
| `birth_date` | `date` | |
| `mlb_debut_date` | `date` | |
| `height_in`, `weight_lb` | `int` | |
| `active` | `bool` | |
| `source_name`, `retrieved_at`, `knowledge_time` | | |

### `rosters` — P1 (thin) / P2 (full)
`(id PK, team_id FK, player_id FK, season int, status text, roster_type text,
jersey_number text, effective_from timestamptz, effective_to timestamptz NULL,
source_name, retrieved_at, knowledge_time)`.
Bitemporal: `effective_*` is real-world validity, `knowledge_time` is when we
could have known. Feature queries filter on both.

---

## 2. Games and outcomes

### `games` — P1
| Column | Type | Notes |
|---|---|---|
| `id` | `bigint` PK | `gamePk` |
| `game_guid` | `text` | |
| `season` | `int` NOT NULL | |
| `game_type` | `text` NOT NULL | R / F / D / L / W / S / E |
| `game_date_utc` | `timestamptz` NOT NULL | scheduled first pitch |
| `official_date` | `date` NOT NULL | MLB's official date (doubleheaders) |
| `status_abstract` | `text` | Preview / Live / Final |
| `status_detailed` | `text` | Scheduled, Postponed, Final, … |
| `status_code` | `text` | |
| `home_team_id`, `away_team_id` | `int` FK → `teams.id` | |
| `venue_id` | `int` FK → `ballparks.id` | |
| `day_night` | `text` | |
| `doubleheader` | `char(1)` | N / S / Y |
| `game_number` | `int` | |
| `series_game_number`, `games_in_series` | `int` | road-trip/home-stand context |
| `scheduled_innings` | `int` | |
| `home_score`, `away_score` | `int` NULL | NULL until final |
| `home_win` | `bool` NULL | **label**; NULL unless `status_abstract='Final'` and both scores present |
| `is_final` | `bool` NOT NULL DEFAULT false | |
| `innings_played` | `int` NULL | extra-inning detection |
| `game_end_utc` | `timestamptz` NULL | drives `knowledge_time` of the result |
| `home_probable_pitcher_id`, `away_probable_pitcher_id` | `int` NULL FK → `players.id` | |
| `probable_pitchers_confirmed` | `bool` NOT NULL DEFAULT false | |
| `weather_condition`, `weather_temp_f`, `weather_wind` | `text/int/text` NULL | observed, present only for played games from this source |
| `source_name`, `retrieved_at`, `knowledge_time` | | |

Indexes: `(official_date)`, `(season, game_type)`, `(home_team_id, game_date_utc)`,
`(away_team_id, game_date_utc)`, `(status_abstract)`, `(is_final, game_date_utc)`.

### `team_game_stats` — P1
One row per team per game, from the boxscore.
`(id PK, game_id FK, team_id FK, is_home bool, opponent_team_id, game_date_utc,
runs, hits, doubles, triples, home_runs, walks, strikeouts, hit_by_pitch,
stolen_bases, caught_stealing, left_on_base, at_bats, plate_appearances,
total_bases, sac_flies, sac_bunts, gidp,
runs_allowed, earned_runs_allowed, hits_allowed, walks_allowed,
strikeouts_pitched, home_runs_allowed, outs_pitched, batters_faced,
pitches_thrown, strikes_thrown, errors, ground_outs, air_outs,
source_name, retrieved_at, knowledge_time)`
`UNIQUE(game_id, team_id)`; index `(team_id, game_date_utc)`.

### `player_game_stats` — P1
One row per player per game with role split, from the boxscore.
`(id PK, game_id FK, player_id FK, team_id FK, opponent_team_id, game_date_utc,
is_home bool, role text CHECK (role IN ('batter','pitcher')),
batting_order int NULL, batting_order_slot int NULL, is_starter bool,
position text,
-- batting
pa, ab, hits, doubles, triples, home_runs, runs, rbi, bb, ibb, so, hbp, sb, cs,
sac_flies, sac_bunts, gidp, total_bases, left_on_base,
-- pitching
games_started, outs_pitched, batters_faced, hits_allowed, runs_allowed,
earned_runs, bb_allowed, ibb_allowed, so_pitched, hr_allowed, hbp_allowed,
pitches_thrown, strikes_thrown, ground_outs_pitched, air_outs_pitched,
inherited_runners, inherited_runners_scored, wild_pitches, balks,
source_name, retrieved_at, knowledge_time)`
`UNIQUE(game_id, player_id, role)`; indexes `(player_id, role, game_date_utc)`,
`(team_id, game_date_utc)`, `(player_id, is_starter, game_date_utc)`.

This table is the substrate for every Phase 1 rolling feature. Because each row
carries `game_date_utc` and `knowledge_time`, every aggregate is filterable to a
strict as-of cut.

### `lineups` — P1 (from final boxscore) / P2 (pregame confirmation)
`(id PK, game_id FK, team_id FK, player_id FK, batting_order int,
position text, is_confirmed bool, lineup_status text
CHECK (lineup_status IN ('CONFIRMED','PROJECTED','UNAVAILABLE')),
observed_at timestamptz, source_name, retrieved_at, knowledge_time)`
`UNIQUE(game_id, team_id, batting_order, knowledge_time)`.
Multiple snapshots per game are retained so a prediction made before lineup
confirmation can be evaluated against exactly the lineup state it saw.

### `pitches` — P2
`(id bigserial PK, game_id FK, at_bat_index int, pitch_number int,
pitcher_id FK, batter_id FK, inning int, is_top bool, balls, strikes, outs,
pitch_type text, release_speed numeric(5,2), spin_rate int,
pfx_x numeric(6,3), pfx_z numeric(6,3), plate_x numeric(6,3), plate_z numeric(6,3),
release_extension numeric(5,2), zone int, description text, call text,
source_name, retrieved_at, knowledge_time)`
Indexes `(game_id)`, `(pitcher_id, game_id)`, `(batter_id, game_id)`.

### `batted_ball_events` — P2
`(id bigserial PK, game_id FK, pitch_id FK NULL, batter_id FK, pitcher_id FK,
launch_speed numeric(5,2), launch_angle numeric(5,2), hit_distance numeric(6,2),
is_barrel bool, is_hard_hit bool, bb_type text, estimated_woba numeric(5,4),
estimated_ba numeric(5,4), outcome text,
source_name, retrieved_at, knowledge_time)`

---

## 3. Pregame state

### `starting_pitcher_projections` — P1
The as-of view of who is expected to start and what we know about them.
`(id PK, game_id FK, team_id FK, pitcher_id FK NULL,
status text CHECK (status IN ('CONFIRMED','PROBABLE','PROJECTED','UNKNOWN')),
expected_innings numeric(4,2) NULL, expected_pitch_count int NULL,
days_rest int NULL, is_estimated bool NOT NULL DEFAULT true,
as_of timestamptz NOT NULL, source_name, retrieved_at, knowledge_time)`
`UNIQUE(game_id, team_id, as_of)`.

### `bullpen_availability` — P2
`(id PK, game_id FK, team_id FK, pitcher_id FK, as_of timestamptz,
availability text CHECK (availability IN ('AVAILABLE','LIMITED','UNAVAILABLE','UNKNOWN')),
pitches_last_1d, pitches_last_2d, pitches_last_3d int,
appearances_last_3d, appearances_last_7d int,
consecutive_days_pitched int, is_closer bool, is_setup bool, throws char(1),
expected_role text, source_name, retrieved_at, knowledge_time)`

### `injuries` — P2
`(id PK, player_id FK, team_id FK, status text, description text,
body_part text, effective_from timestamptz, effective_to timestamptz NULL,
expected_return date NULL, source_name, retrieved_at, knowledge_time)`

### `weather` — P1 (observed, when the source supplies it) / P2 (forecast)
`(id PK, game_id FK, venue_id FK, observation_type text
CHECK (observation_type IN ('FORECAST','OBSERVED')),
valid_at timestamptz, temperature_f numeric(5,2), feels_like_f numeric(5,2),
wind_speed_mph numeric(5,2), wind_direction_deg int NULL,
wind_direction_text text NULL, wind_field_relative text NULL,
humidity_pct numeric(5,2), pressure_mb numeric(6,2),
precipitation_prob numeric(5,2), precipitation_mm numeric(6,2),
condition text, air_density_kg_m3 numeric(6,4) NULL,
roof_status text NULL, is_estimated bool,
source_name, retrieved_at, knowledge_time)`
`wind_field_relative` is derived from `wind_direction_deg` and the ballpark
`azimuth_angle` (out to LF / in from CF / L-to-R …).

### `odds_snapshots` — P3
`(id bigserial PK, game_id FK, book text, market text, snapshot_at timestamptz,
home_price int, away_price int, home_implied_prob numeric(6,5),
away_implied_prob numeric(6,5), home_novig_prob numeric(6,5),
away_novig_prob numeric(6,5), total_line numeric(4,1) NULL,
is_closing bool, source_name, retrieved_at, knowledge_time)`
Index `(game_id, snapshot_at)`. Backtests may only read snapshots with
`snapshot_at <= prediction.as_of` (closing-line value is computed separately and
never fed to a model).

---

## 4. Modeling

### `model_features` — P1 (feature store)
One immutable row per `(game_id, team_side, as_of, feature_set_version)`.
`(id bigserial PK, game_id FK, team_side char(1) CHECK (team_side IN ('H','A')),
as_of timestamptz NOT NULL, feature_set_version text NOT NULL,
features JSONB NOT NULL, sample_sizes JSONB NOT NULL,
estimated_flags JSONB NOT NULL, missing_features text[] NOT NULL DEFAULT '{}',
completeness numeric(5,4) NOT NULL, computed_at timestamptz NOT NULL)`
`UNIQUE(game_id, team_side, as_of, feature_set_version)`;
GIN index on `features`.

`features` holds the numeric vector; `sample_sizes` holds the n behind each
split so the UI can show it; `estimated_flags` marks values that were shrunk or
projected rather than observed.

### `model_versions` — P1
`(id PK, name text, version text, algorithm text, feature_set_version text,
trained_at timestamptz, train_start_date date, train_end_date date,
train_rows int, hyperparameters JSONB, calibration_method text,
calibration_params JSONB, metrics JSONB, artifact_path text,
artifact_sha256 text, git_sha text, is_active bool, notes text)`
`UNIQUE(name, version)`. Exactly one row per `name` may have `is_active = true`
(enforced by a partial unique index).

### `predictions` — P1 (immutable)
`(id bigserial PK, game_id FK, model_version_id FK, as_of timestamptz NOT NULL,
created_at timestamptz NOT NULL DEFAULT now(),
home_win_prob numeric(6,5) NOT NULL, away_win_prob numeric(6,5) NOT NULL,
home_win_prob_uncalibrated numeric(6,5),
projected_home_runs numeric(5,2) NULL, projected_away_runs numeric(5,2) NULL,
projected_home_runs_low/high, projected_away_runs_low/high numeric(5,2) NULL,
fair_home_moneyline int NULL, fair_away_moneyline int NULL,
market_home_prob numeric(6,5) NULL, market_edge numeric(6,5) NULL,
confidence_score numeric(5,4) NOT NULL, confidence_label text NOT NULL,
recommendation text NOT NULL, model_agreement numeric(5,4) NULL,
data_completeness numeric(5,4) NOT NULL,
missing_data text[] NOT NULL DEFAULT '{}',
warnings JSONB NOT NULL DEFAULT '[]',
feature_snapshot JSONB NOT NULL, component_probs JSONB NOT NULL DEFAULT '{}',
superseded_by bigint NULL FK → predictions.id,
is_latest bool NOT NULL DEFAULT true)`

Constraints: `CHECK (abs(home_win_prob + away_win_prob - 1) < 1e-6)`,
`UNIQUE(game_id, model_version_id, as_of)`.
Rows are **never** updated except to set `superseded_by` / `is_latest = false`
when a newer snapshot lands. Partial unique index guarantees one `is_latest`
row per `(game_id, model_version_id)`.

### `prediction_explanations` — P1
`(id bigserial PK, prediction_id FK, rank int, feature_key text,
display_name text, category text, favors char(1) CHECK (favors IN ('H','A')),
contribution_pp numeric(6,3), feature_value numeric, feature_display text,
sample_size int NULL, is_estimated bool, narrative text)`
`contribution_pp` is the effect in **probability points**, so the UI never has to
translate log-odds.

### `simulation_results` — P3
`(id PK, prediction_id FK, n_simulations int, home_win_pct, away_win_pct numeric,
mean_home_runs, mean_away_runs numeric, run_distribution JSONB,
score_distribution JSONB, extra_innings_prob, one_run_prob, upset_prob numeric,
seed bigint, created_at)`

### `backtest_results` — P1
Two grains, distinguished by `slice_type`:
`(id bigserial PK, run_id uuid, model_version_id FK, slice_type text,
slice_key text, season int NULL, start_date date, end_date date,
n_games int, accuracy, log_loss, brier_score, calibration_error, roc_auc numeric,
roi numeric NULL, clv numeric NULL, extra JSONB, created_at)`
`slice_type ∈ {overall, season, month, probability_band, favorite_underdog,
home_away, starter_quality, lineup_confirmed, ablation, model_comparison}`.

### `backtest_predictions` — P1
Row-level backtest output, retained so any slice can be recomputed without
re-running the walk-forward.
`(id bigserial PK, run_id uuid, game_id FK, as_of timestamptz,
predicted_home_win_prob numeric(6,5), actual_home_win bool,
train_end_date date, n_train_rows int, features JSONB)`

---

## 5. Operations

### `data_source_status` — P1
`(id PK, source_name text, category text, status text
CHECK (status IN ('OK','DEGRADED','UNAVAILABLE')),
freshness text CHECK (freshness IN ('FRESH','AGING','STALE','UNAVAILABLE')),
last_success_at timestamptz NULL, last_failure_at timestamptz NULL,
last_error text NULL, consecutive_failures int NOT NULL DEFAULT 0,
records_last_run int NULL, updated_at timestamptz NOT NULL)`
`UNIQUE(source_name, category)`.

### `raw_source_payloads` — P1
`(id bigserial PK, source_name text, endpoint text, request_params JSONB,
payload JSONB, content_hash text, retrieved_at timestamptz,
knowledge_time timestamptz)`
`UNIQUE(source_name, endpoint, content_hash)`; index `(retrieved_at)`.

### `job_runs` — P1
`(id bigserial PK, job_name text, started_at, finished_at timestamptz,
status text CHECK (status IN ('RUNNING','SUCCESS','FAILED')),
rows_written int, error text NULL, details JSONB)`
Index `(job_name, started_at DESC)`.

---

## 6. Schema-wide invariants

1. **Append-only where it matters.** `predictions`, `model_features`,
   `odds_snapshots`, `lineups` and `raw_source_payloads` are append-only. A
   correction is a new row with a later `as_of`/`knowledge_time`.
2. **Probability integrity.** A DB `CHECK` enforces that win probabilities sum
   to 1 within 1e-6; the API re-validates before serving.
3. **Label integrity.** `games.home_win` is `NULL` unless the game is final with
   both scores present. Training filters `home_win IS NOT NULL` and
   `game_type = 'R'` by default.
4. **Every fact row is attributable.** `source_name` is `NOT NULL` on every
   externally-sourced table.
5. **Time filtering uses `knowledge_time`.** `retrieved_at` exists for
   operational debugging only and must never appear in a feature query.
