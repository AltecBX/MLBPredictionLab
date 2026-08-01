# FEATURE_DICTIONARY — Jerry MLB Prediction Lab

Every feature the platform can compute, with its definition, source, window,
shrinkage rule, minimum sample and phase. **P1** features are computed and
served in this delivery. **P2/P3** features are specified here and registered in
the feature registry with `available=False`; they report `UNAVAILABLE` in the UI
until their provider is enabled. Nothing on this list is ever filled with an
invented value.

Conventions used throughout:

* `as_of` — the timestamp the prediction is made for. Every window ends
  **strictly before** `as_of` (see [LEAKAGE_PREVENTION](LEAKAGE_PREVENTION.md)).
* Windows: `w7`, `w14`, `w30`, `w60` (calendar days), `season`, `prev_season`,
  `career3` (trailing three seasons).
* All team-level features are computed for both sides and enter the model as a
  **home-minus-away difference** unless noted, which makes the model's sign
  convention unambiguous: positive → favors home.

---

## 1. Shrinkage and sample size

Small samples are the dominant failure mode in baseball modeling. Every rate
feature is regressed toward a stated prior:

```
shrunk = (observed_events + prior_rate × k) / (observed_denominator + k)
```

`k` is the **stabilization constant** — the denominator at which the observed
rate carries equal weight with the prior.

| Rate | Denominator | `k` | Prior |
|---|---|---|---|
| K% (batter) | PA | 60 | league K% for the season-to-date |
| BB% (batter) | PA | 120 | league BB% |
| wOBA / xwOBA (batter) | PA | 300 | league wOBA |
| ISO | AB | 320 | league ISO |
| Hard-hit% / Barrel% | batted balls | 50 / 80 | league rate |
| K% (pitcher) | BF | 70 | league K% |
| BB% (pitcher) | BF | 170 | league BB% |
| GB% (pitcher) | balls in play | 70 | league GB% |
| HR/FB (pitcher) | fly balls | 320 | league HR/FB |
| Runs allowed / 9 (team) | games | 25 | league RA/9 |
| Runs scored / game (team) | games | 25 | league R/G |

Rules:

1. A feature whose denominator is below its **minimum sample** is not dropped —
   it is shrunk harder and flagged `is_estimated = true`, and the sample size is
   surfaced next to it in the UI.
2. Prior-season and three-year baselines are themselves shrunk toward league
   average before being used as a prior for the current season.
3. Recent-form windows never *replace* long-term ability. They enter the model
   as an explicit **delta from the stabilized baseline**, so a hot 7-day stretch
   can move a prediction but cannot erase a season of evidence.

---

## 2. Starting pitcher features

Computed for each side's expected starter from that pitcher's own game log,
strictly before `as_of`.

| Key | Definition | Window | Source | Phase |
|---|---|---|---|---|
| `sp_identified` | Starter known (confirmed or probable) | — | Schedule | **P1** |
| `sp_status` | CONFIRMED / PROBABLE / PROJECTED / UNKNOWN | — | Schedule | **P1** |
| `sp_era` | 9 × ER / IP | season, w30, career3 | Boxscore game logs | **P1** |
| `sp_fip` | `(13·HR + 3·(BB+HBP) − 2·K)/IP + cFIP` | season, career3 | Game logs | **P1** |
| `sp_whip` | (BB + H) / IP | season, w30 | Game logs | **P1** |
| `sp_k_pct` | K / BF | season, w30, career3 | Game logs | **P1** |
| `sp_bb_pct` | BB / BF | season, w30, career3 | Game logs | **P1** |
| `sp_k_minus_bb_pct` | K% − BB% | season, career3 | Derived | **P1** |
| `sp_hr_per_9` | 9 × HR / IP | season, career3 | Game logs | **P1** |
| `sp_gb_pct` | GO / (GO + AO) as a proxy until Statcast | season | Game logs | **P1** |
| `sp_fb_pct` | AO / (GO + AO) | season | Game logs | **P1** |
| `sp_ip_per_start` | IP / GS | season, w30 | Game logs | **P1** |
| `sp_pitches_per_start` | pitches / GS | season, w30 | Game logs | **P1** |
| `sp_last_pitch_count` | pitches in most recent start | — | Game logs | **P1** |
| `sp_days_rest` | days between previous start and this game | — | Game logs + schedule | **P1** |
| `sp_short_rest` | `days_rest < 4` | — | Derived | **P1** |
| `sp_starts_count` | starts in window (sample size) | season, career3 | Game logs | **P1** |
| `sp_home_away_split` | ERA/FIP split by venue side | season, career3 | Game logs | **P1** |
| `sp_season_experience` | career starts before `as_of` | career | Game logs | **P1** |
| `sp_hand` | L / R | — | Player master | **P1** |
| `sp_xfip` | FIP with HR normalized to league HR/FB | season | Statcast | P2 |
| `sp_siera` | SIERA | season | Statcast | P2 |
| `sp_xera` | Expected ERA from contact quality | season | Statcast | P2 |
| `sp_xwoba_allowed` | xwOBA against | season, w30 | Statcast | P2 |
| `sp_avg_exit_velo_allowed` | mean EV allowed | season, w30 | Statcast | P2 |
| `sp_hard_hit_pct_allowed` | EV ≥ 95 mph rate | season | Statcast | P2 |
| `sp_barrel_pct_allowed` | barrels / BBE | season | Statcast | P2 |
| `sp_velocity` / `sp_velocity_delta_30d` | fastball velo and 30-day change | w30 | Statcast | P2 |
| `sp_spin_rate`, `sp_pitch_movement` | per pitch type | season | Statcast | P2 |
| `sp_pitch_usage` | usage share per pitch type | season, w30 | Statcast | P2 |
| `sp_tto_penalty` | wOBA delta 1st→3rd time through order | career3 | Play-by-play | P2 |
| `sp_vs_lhb` / `sp_vs_rhb` | platoon splits | season, career3 | Play-by-play | P2 |
| `sp_arsenal_vs_lineup` | arsenal-vs-lineup pitch-type matchup score | — | Statcast | P3 |
| `sp_catcher_pairing` | framing value of expected catcher | season | Statcast | P2 |
| `sp_workload_restriction` | announced innings/pitch limit | — | Injury feed | P2 |

**Missing-starter policy (P1).** If a starter is unknown, `sp_*` features are
`NULL`, `sp_identified = 0`, the model uses a *replacement-level starter prior*
that is explicitly labelled `is_estimated`, completeness drops, and the UI shows
"Starting pitcher unconfirmed".

---

## 3. Offensive features

| Key | Definition | Window | Source | Phase |
|---|---|---|---|---|
| `off_runs_per_game` | R / G, opponent-adjusted variant `off_runs_per_game_adj` | w7, w14, w30, w60, season, prev_season | Team game logs | **P1** |
| `off_woba_proxy` | linear-weights wOBA from boxscore events | w30, season | Team game logs | **P1** |
| `off_ops` | OBP + SLG | w30, season | Team game logs | **P1** |
| `off_iso` | SLG − AVG | w30, season | Team game logs | **P1** |
| `off_k_pct` | K / PA | w30, season | Team game logs | **P1** |
| `off_bb_pct` | BB / PA | w30, season | Team game logs | **P1** |
| `off_hr_per_pa` | HR / PA | w30, season | Team game logs | **P1** |
| `off_form_delta_w14` | w14 runs/game minus stabilized season baseline | w14 | Derived | **P1** |
| `off_form_delta_w30` | w30 runs/game minus stabilized baseline | w30 | Derived | **P1** |
| `off_vs_hand` | team offense vs LHP / RHP, from game logs keyed on opposing starter hand | season, prev_season | Game logs + player master | **P1** |
| `off_games_sample` | games in window (sample size) | all | Derived | **P1** |
| `lineup_status` | CONFIRMED / PROJECTED / UNAVAILABLE | — | Lineup feed | **P1** (status only) |
| `lineup_wrc_plus_weighted` | PA-weighted wRC+ of projected lineup | season | Player stats | P2 |
| `lineup_xwoba_weighted` | PA-weighted xwOBA | season, w30 | Statcast | P2 |
| `lineup_platoon_advantage` | share of lineup with the platoon edge vs the starter | — | Lineup + handedness | P2 |
| `bat_contact_pct`, `bat_chase_pct` | plate-discipline rates | season | Statcast | P2 |
| `bat_hard_hit_pct`, `bat_barrel_pct`, `bat_avg_exit_velo` | contact quality | season, w30 | Statcast | P2 |
| `bat_vs_pitch_type` | performance by pitch type | career3 | Statcast | P3 |
| `bat_vs_velocity_band` | performance by velocity band | career3 | Statcast | P3 |
| `baserunning_value` | team baserunning runs | season | Play-by-play | P2 |
| `lineup_projected_pa_weights` | expected PA by batting-order slot | — | Derived | P2 |

Phase 1 measures offense at the **team** level from real per-game boxscore
lines. Player-level lineup weighting requires the lineup feed and Statcast, and
is explicitly reported as unavailable until Phase 2 rather than approximated.

---

## 4. Bullpen features

| Key | Definition | Window | Source | Phase |
|---|---|---|---|---|
| `bp_era` | 9 × ER / IP for non-starting pitchers | w30, season | Player game logs (role=pitcher, is_starter=false) | **P1** |
| `bp_fip` | FIP over relief innings | season | Player game logs | **P1** |
| `bp_k_minus_bb_pct` | K% − BB% (relief) | w30, season | Player game logs | **P1** |
| `bp_hr_per_9` | relief HR/9 | season | Player game logs | **P1** |
| `bp_ip_last_3d` | relief innings pitched, previous 3 days | 3 d | Player game logs | **P1** |
| `bp_ip_last_7d` | relief innings pitched, previous 7 days | 7 d | Player game logs | **P1** |
| `bp_pitches_last_3d` | relief pitches thrown, previous 3 days | 3 d | Player game logs | **P1** |
| `bp_fatigue_index` | normalized blend of 3-day and 7-day usage vs team's own baseline | 3/7 d | Derived | **P1** |
| `bp_relievers_used_last_3d` | distinct relievers used | 3 d | Player game logs | **P1** |
| `bp_extra_inning_prev_day` | previous game went extras | 1 d | Game log | **P1** |
| `bp_closer_available` | closer available | — | Availability feed | P2 |
| `bp_setup_available` | setup availability count | — | Availability feed | P2 |
| `bp_lhp_available`, `bp_rhp_available` | handedness availability | — | Availability feed | P2 |
| `bp_consecutive_days` | relievers on 2nd/3rd consecutive day | — | Availability feed | P2 |
| `bp_expected_quality` | availability-weighted reliever quality | — | Derived | P2 |
| `bp_depth` | count of above-replacement available arms | — | Derived | P2 |
| `bp_inherited_runner_pct` | inherited runners scored % | season | Game logs | **P1** |
| `bp_velocity_delta` | recent relief velocity change | w14 | Statcast | P2 |

Phase 1 derives real bullpen *usage and fatigue* from ingested game logs — that
is observed data. Per-pitcher *availability* (closer rested, arm unavailable) is
a distinct feed and stays `UNAVAILABLE` until Phase 2.

---

## 5. Defense and catching

| Key | Definition | Source | Phase |
|---|---|---|---|
| `def_errors_per_game` | team errors per game | Boxscore | **P1** |
| `def_efficiency_proxy` | (BIP − hits allowed on BIP) / BIP | Boxscore | **P1** |
| `def_drs` | Defensive Runs Saved | Licensed fielding source | P2 |
| `def_oaa` | Outs Above Average | Statcast | P2 |
| `catcher_framing_runs` | expected catcher framing value | Statcast | P2 |
| `catcher_throwing` | caught-stealing above average | Play-by-play | P2 |
| `def_expected_lineup_quality` | defensive quality of projected fielders | Lineup + fielding metrics | P2 |

---

## 6. Game environment

| Key | Definition | Source | Phase |
|---|---|---|---|
| `env_home_field` | constant home indicator; the model learns the coefficient rather than assuming a fixed 54% | — | **P1** |
| `env_venue_elevation_ft` | ballpark elevation | Venue master | **P1** |
| `env_park_dimensions` | LF/LC/CF/RC/RF fence distances | Venue master | **P1** |
| `env_roof_type` | Open / Dome / Retractable | Venue master | **P1** |
| `env_is_dome` | roof closed or fixed dome | Venue master | **P1** |
| `env_day_night` | day/night indicator | Schedule | **P1** |
| `env_park_run_factor` | multi-year regressed park run factor | Park factors | P2 |
| `env_park_hr_factor` | multi-year regressed park HR factor | Park factors | P2 |
| `env_park_factor_by_hand` | handedness-specific park factor | Park factors | P2 |
| `env_temperature_f` | forecast temperature at first pitch | Weather | P2 |
| `env_wind_speed_mph` | forecast wind speed | Weather | P2 |
| `env_wind_field_relative` | wind direction relative to the field, using venue azimuth | Weather + venue | P2 |
| `env_humidity_pct` | relative humidity | Weather | P2 |
| `env_air_density` | computed from temp, pressure, humidity, elevation | Derived | P2 |
| `env_precip_prob` | precipitation probability | Weather | P2 |
| `env_delay_risk` | postponement/delay risk score | Derived | P2 |
| `env_umpire_k_pct` | plate umpire strike-zone profile | Play-by-play + officials | P2 |

Phase 1 uses the ballpark's **physical** attributes, which are static and
genuinely available. Weather-dependent features stay `UNAVAILABLE`. Empirical
park factors require a multi-season regression pass and land in Phase 2 rather
than being guessed from a single season.

---

## 7. Scheduling, rest and travel

| Key | Definition | Source | Phase |
|---|---|---|---|
| `sched_days_rest` | days since the team's previous game | Schedule | **P1** |
| `sched_games_last_7d` | games played in previous 7 days | Schedule | **P1** |
| `sched_consecutive_games` | current streak of games on consecutive days | Schedule | **P1** |
| `sched_travel_km` | great-circle distance from previous game's venue | Venue coordinates | **P1** |
| `sched_timezone_shift` | hours of timezone change from previous venue | Venue timezone | **P1** |
| `sched_day_after_night` | day game following a night game | Schedule | **P1** |
| `sched_doubleheader` | doubleheader game 1 / 2 | Schedule | **P1** |
| `sched_extra_innings_prev_day` | previous game went extra innings | Game log | **P1** |
| `sched_road_trip_length` | consecutive road games including this one | Schedule | **P1** |
| `sched_home_stand_length` | consecutive home games including this one | Schedule | **P1** |
| `sched_prev_game_end_local` | local end time of previous game | Game log | **P1** |
| `sched_is_getaway_day` | final game of a series before travel | Schedule | **P1** |

---

## 8. Team strength and history

| Key | Definition | Window | Source | Phase |
|---|---|---|---|---|
| `elo_rating` | Elo updated after each completed game, carried over between seasons with regression to the mean | rolling | Game results | **P1** |
| `elo_diff` | home Elo − away Elo (+ home-field constant) | — | Derived | **P1** |
| `team_win_pct` | wins / games | season, w30, prev_season | Game results | **P1** |
| `team_run_diff_per_game` | (RS − RA) / G | season, w30 | Team game logs | **P1** |
| `team_pythag_win_pct` | `RS^1.83 / (RS^1.83 + RA^1.83)` | season | Team game logs | **P1** |
| `team_home_away_split` | win% at home vs away | season, prev_season | Game results | **P1** |
| `team_vs_hand_split` | win% vs LHP / RHP starters | season | Game results + starter hand | **P1** |
| `team_sos` | opponent-average strength faced | season | Derived | **P1** |
| `team_opp_adj_offense` | runs scored adjusted for opposing pitching faced | season | Derived | **P1** |
| `team_opp_adj_pitching` | runs allowed adjusted for opposing offenses faced | season | Derived | **P1** |
| `team_division_win_pct` | win% within division | season | Game results | **P1** |
| `h2h_season_series` | this season's series record, **shrunk hard** | season | Game results | **P1** |
| `h2h_recent` | prior-matchup results, **shrunk hard** | career3 | Game results | **P1** |
| `bvp_history` | batter-vs-pitcher history | career | Play-by-play | P3 |

### Small-sample discipline for history features

Head-to-head, batter-vs-pitcher and streak features are the classic
overfitting traps. The platform constrains them explicitly:

1. `h2h_*` features enter the model already shrunk with `k = 40` games — a
   6-game season series moves the feature ~13% of the way from the prior.
2. `bvp_history` requires **≥ 25 PA** to be non-null and is capped at ±0.5
   probability points of total contribution.
3. Streak features are not included as raw streak counts. Recent form enters
   only as `*_form_delta_*`, a bounded deviation from the stabilized baseline.
4. The ablation suite ([BACKTEST_PLAN](BACKTEST_PLAN.md) §6) explicitly tests
   whether `h2h_*` and `bvp_*` improve walk-forward log loss. If they do not,
   they are removed from the active feature set — this is a measured decision,
   not a preference.

---

## 9. Phase 1 active feature set (`fs_v1`)

The model version shipped in this delivery consumes these, all as
home-minus-away differences unless marked absolute:

```
elo_diff                          sp_k_minus_bb_pct_diff
team_win_pct_season_diff          sp_hr_per_9_diff
team_run_diff_per_game_diff       sp_ip_per_start_diff
team_pythag_win_pct_diff          sp_days_rest_diff
off_runs_per_game_w30_diff        sp_short_rest_diff
off_runs_per_game_season_diff     sp_identified_home        (absolute)
off_form_delta_w14_diff           sp_identified_away        (absolute)
off_woba_proxy_season_diff        sp_experience_diff
off_k_pct_season_diff             bp_era_w30_diff
off_vs_hand_diff                  bp_k_minus_bb_pct_season_diff
team_opp_adj_offense_diff         bp_fatigue_index_diff
team_opp_adj_pitching_diff        bp_ip_last_3d_diff
sched_days_rest_diff              def_errors_per_game_diff
sched_travel_km_diff              def_efficiency_proxy_diff
sched_timezone_shift_diff         env_home_field            (absolute, = 1)
sched_games_last_7d_diff          env_is_dome               (absolute)
sched_day_after_night_diff        env_venue_elevation_ft    (absolute, scaled)
sp_era_season_diff                team_home_away_split_diff
sp_fip_season_diff                h2h_season_series_shrunk_diff
sp_whip_season_diff               team_sos_diff
sp_k_pct_season_diff              off_games_sample_min      (absolute, gate)
sp_bb_pct_season_diff
```

Each is registered in `app/features/registry.py` with its display name,
category, unit, minimum sample, phase and narrative phrase, which is what drives
both the explanation text and the UI's sample-size annotations.

**Sign convention.** A positive value always favors the home team. Features
where a lower raw value is better — ERA, WHIP, walk rate, travel distance,
bullpen fatigue — are assembled as `away − home` so the convention holds
throughout, and their explanation text reports the edge as a magnitude rather
than a signed difference.

**Absolute features.** `env_home_field`, `env_is_dome`,
`env_venue_elevation_km`, `sp_identified_home` and `sp_identified_away` are not
differences. They are marked `is_absolute` in the registry so the explanation
layer describes them factually instead of phrasing a ballpark as though it were
choosing a side.

---

## 10. Feature contract

Every feature implementation must:

1. Declare `window`, `min_sample`, `prior`, `source_category` and `phase` in the
   registry — a feature that is not registered cannot enter a model.
2. Accept `as_of` and read only rows with `knowledge_time <= as_of` **and**
   `game_date_utc < as_of`.
3. Return `(value, sample_size, is_estimated)` — never a bare float.
4. Be deterministic: same inputs and same `as_of` → identical output. Verified
   by `tests/test_feature_determinism.py`.
5. Have a leakage test proving that a game's own result cannot influence its own
   feature value. Verified by `tests/test_leakage.py`.
