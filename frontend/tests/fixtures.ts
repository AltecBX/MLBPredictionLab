/** Explicitly synthetic fixtures for component tests. Never served to a user. */

import type { GameCard, MatchupBar, DriverSummary, CalibrationBin } from "@/lib/types";

export const driver = (over: Partial<DriverSummary> = {}): DriverSummary => ({
  feature_key: "sp_fip_season_diff",
  display_name: "Starter FIP edge",
  category: "starting_pitching",
  category_label: "Starting pitching",
  favors: "H",
  contribution_pp: 6.2,
  feature_display: "+0.41 FIP",
  sample_size: 18,
  is_estimated: false,
  narrative: "Home Club has the better fielding-independent starter.",
  ...over,
});

export const gameCard = (over: Partial<GameCard> = {}): GameCard => ({
  game_id: 1,
  season: 2026,
  game_type: "R",
  official_date: "2026-08-01",
  first_pitch_utc: "2026-08-01T23:05:00Z",
  status: "Preview",
  status_detail: "Scheduled",
  day_night: "night",
  doubleheader: "N",
  home: {
    id: 10, name: "Home Club", abbreviation: "HME", team_name: "Club",
    location_name: "Hometown", division_name: "Test East", wins: 55, losses: 45,
    home_record: { wins: 32, losses: 18, win_pct: 0.64 },
    away_record: { wins: 23, losses: 27, win_pct: 0.46 },
    streak: {
      kind: "W", length: 3, label: "W3",
      games: [
        { game_id: 1, date: "2026-07-29", opponent: "Visitors", opponent_id: 20,
          is_home: true, runs_for: 5, runs_against: 2 },
      ],
    },
    standing: {
      division_name: "Test East", division_rank: 2, games_behind: 3.5,
      league_name: "Test League", league_rank: 4,
      wildcard_rank: 1, wildcard_games_behind: -2.0, in_playoff_position: true,
      elimination_number: 58, clinched_division: false, eliminated: false,
    },
  },
  away: {
    id: 20, name: "Away Club", abbreviation: "AWY", team_name: "Visitors",
    location_name: "Awaytown", division_name: "Test West", wins: 48, losses: 52,
    home_record: { wins: 28, losses: 22, win_pct: 0.56 },
    away_record: { wins: 20, losses: 30, win_pct: 0.4 },
    streak: {
      kind: "L", length: 2, label: "L2",
      games: [
        { game_id: 2, date: "2026-07-30", opponent: "Club", opponent_id: 10,
          is_home: false, runs_for: 1, runs_against: 4 },
      ],
    },
    standing: {
      division_name: "Test West", division_rank: 4, games_behind: 9.0,
      league_name: "Test League", league_rank: 11,
      wildcard_rank: 7, wildcard_games_behind: 5.5, in_playoff_position: false,
      elimination_number: 41, clinched_division: false, eliminated: false,
    },
  },
  ballpark: {
    id: 1, name: "Test Park", city: "Testville", state: "TS", roof_type: "Open",
    elevation_ft: 30, lf_line: 330, center: 400, rf_line: 330, turf_type: "Grass",
    capacity: 42000, timezone: "America/New_York",
    latitude: 40.75, longitude: -73.85,
  },
  home_pitcher: { id: 101, full_name: "Home Starter", pitch_hand: "R", status: "PROBABLE" },
  away_pitcher: { id: 201, full_name: "Away Starter", pitch_hand: "L", status: "PROBABLE" },
  lineup_status: "UNAVAILABLE",
  lineup_status_reason: "Pregame lineups require the Phase 2 lineup poller.",
  weather_status: "UNAVAILABLE",
  weather_summary: null,
  bullpen_warning: null,
  home_score: null,
  away_score: null,
  is_final: false,
  prediction: {
    model_version_id: 1,
    model_name: "jerry_logistic",
    model_version: "v2",
    as_of: "2026-08-01T20:05:00Z",
    created_at: "2026-08-01T20:06:00Z",
    home_win_prob: 0.618,
    away_win_prob: 0.382,
    home_win_prob_uncalibrated: 0.63,
    predicted_winner: "HOME",
    predicted_winner_team_id: 10,
    confidence_score: 0.71,
    confidence_label: "MODERATE",
    recommendation: "STRONG_LEAN",
    model_agreement: 0.82,
    data_completeness: 0.94,
    missing_data: [],
    warnings: [
      { code: "LINEUP_UNCONFIRMED", severity: "medium", message: "Batting orders are not confirmed." },
    ],
    component_probs: { logistic_calibrated: 0.618, elo_reference: 0.6 },
    projected_score: {
      home_runs: 4.6, away_runs: 4.0, home_low: 3, home_high: 6,
      away_low: 2, away_high: 5, is_estimated: true,
      method: "odds_ratio_runs_v1", detail: "Derived, not simulated.",
    },
    market: {
      available: false,
      reason: "Market comparison requires a licensed odds provider.",
      market_home_prob: null, model_edge: null,
      fair_home_moneyline: -162, fair_away_moneyline: 162,
    },
    top_drivers: [driver(), driver({ feature_key: "elo_diff", display_name: "Elo rating edge", contribution_pp: 3.4 })],
  },
  prediction_unavailable: null,
  ...over,
});

export const matchupBars: MatchupBar[] = [
  { category: "starting_pitching", label: "Starting pitching", home_pp: 6.2, away_pp: 1.1, net_pp: 5.1, advantage: "HOME" },
  { category: "bullpen", label: "Bullpen", home_pp: 0.4, away_pp: 3.2, net_pp: -2.8, advantage: "AWAY" },
  { category: "offense", label: "Offense", home_pp: 1.0, away_pp: 1.02, net_pp: -0.02, advantage: "EVEN" },
];

export const calibrationBins: CalibrationBin[] = [
  { lower: 0.3, upper: 0.4, n: 120, mean_predicted: 0.36, observed_frequency: 0.34, wilson_low: 0.26, wilson_high: 0.43 },
  { lower: 0.4, upper: 0.5, n: 900, mean_predicted: 0.46, observed_frequency: 0.45, wilson_low: 0.42, wilson_high: 0.48 },
  { lower: 0.5, upper: 0.6, n: 1400, mean_predicted: 0.55, observed_frequency: 0.56, wilson_low: 0.53, wilson_high: 0.59 },
  { lower: 0.6, upper: 0.7, n: 400, mean_predicted: 0.63, observed_frequency: 0.61, wilson_low: 0.56, wilson_high: 0.66 },
];
