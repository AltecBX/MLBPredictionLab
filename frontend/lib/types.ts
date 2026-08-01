/**
 * API contract types, mirroring the FastAPI Pydantic schemas.
 * Kept hand-written and narrow so a backend change surfaces as a type error.
 */

export type Freshness = "FRESH" | "AGING" | "STALE" | "UNAVAILABLE";
export type SourceStatus = "OK" | "DEGRADED" | "UNAVAILABLE";

export interface FreshnessEntry {
  category: string;
  label: string;
  status: SourceStatus;
  freshness: Freshness;
  last_success_at: string | null;
  age_seconds: number | null;
  provider: string | null;
  detail: string | null;
  records_last_run?: number | null;
}

export interface TeamRef {
  id: number;
  name: string;
  abbreviation: string;
  team_name: string | null;
  location_name: string | null;
  division_name: string | null;
  wins: number | null;
  losses: number | null;
}

export interface BallparkRef {
  id: number | null;
  name: string | null;
  city: string | null;
  state: string | null;
  roof_type: string | null;
  elevation_ft: number | null;
  lf_line: number | null;
  center: number | null;
  rf_line: number | null;
  turf_type: string | null;
  capacity: number | null;
  timezone: string | null;
}

export interface PitcherRef {
  id: number | null;
  full_name: string | null;
  pitch_hand: string | null;
  status: "CONFIRMED" | "PROBABLE" | "PROJECTED" | "UNKNOWN";
}

export interface WarningEntry {
  code: string;
  severity: "high" | "medium" | "low";
  message: string;
}

export interface Unavailable {
  available: false;
  reason: string;
  required_source?: string | null;
  phase?: number | null;
}

export interface DriverSummary {
  feature_key: string;
  display_name: string;
  category: string;
  category_label: string;
  favors: "H" | "A";
  contribution_pp: number;
  feature_display: string | null;
  sample_size: number | null;
  is_estimated: boolean;
  narrative: string | null;
}

export interface ProjectedScore {
  home_runs: number | null;
  away_runs: number | null;
  home_low: number | null;
  home_high: number | null;
  away_low: number | null;
  away_high: number | null;
  is_estimated: boolean;
  method: string | null;
  detail: string | null;
}

export interface MarketComparison {
  available: boolean;
  reason: string | null;
  market_home_prob: number | null;
  model_edge: number | null;
  fair_home_moneyline: number | null;
  fair_away_moneyline: number | null;
}

export type ConfidenceLabel =
  | "HIGH"
  | "MODERATE"
  | "LOW"
  | "VERY_LOW"
  | "INSUFFICIENT_DATA";

export type Recommendation =
  | "STRONG_LEAN"
  | "MODERATE_LEAN"
  | "SMALL_LEAN"
  | "NO_MEANINGFUL_ADVANTAGE"
  | "INSUFFICIENT_DATA";

export interface PredictionSummary {
  model_version_id: number;
  model_name: string | null;
  model_version: string | null;
  as_of: string;
  created_at: string;
  home_win_prob: number;
  away_win_prob: number;
  home_win_prob_uncalibrated: number | null;
  predicted_winner: "HOME" | "AWAY";
  predicted_winner_team_id: number;
  confidence_score: number;
  confidence_label: ConfidenceLabel;
  recommendation: Recommendation;
  model_agreement: number | null;
  data_completeness: number;
  missing_data: string[];
  warnings: WarningEntry[];
  component_probs: Record<string, number>;
  projected_score: ProjectedScore;
  market: MarketComparison;
  top_drivers: DriverSummary[];
}

export interface GameCard {
  game_id: number;
  season: number;
  game_type: string;
  official_date: string;
  first_pitch_utc: string;
  status: string;
  status_detail: string | null;
  day_night: string | null;
  doubleheader: string | null;
  home: TeamRef;
  away: TeamRef;
  ballpark: BallparkRef;
  home_pitcher: PitcherRef;
  away_pitcher: PitcherRef;
  lineup_status: string;
  lineup_status_reason: string | null;
  weather_status: string;
  weather_summary: string | null;
  bullpen_warning: string | null;
  home_score: number | null;
  away_score: number | null;
  is_final: boolean;
  prediction: PredictionSummary | null;
  prediction_unavailable: Unavailable | null;
}

export interface GameListResponse {
  date: string;
  count: number;
  generated_at: string;
  model_version: string | null;
  freshness: FreshnessEntry[];
  games: GameCard[];
}

export interface MatchupBar {
  category: string;
  label: string;
  home_pp: number;
  away_pp: number;
  net_pp: number;
  advantage: "HOME" | "AWAY" | "EVEN";
}

export interface FeatureCell {
  value: number | null;
  sample_size: number | null;
  is_estimated: boolean;
}

export interface SideDetail {
  team: TeamRef;
  starter: PitcherRef;
  starter_stats: Record<string, FeatureCell>;
  offense: Record<string, FeatureCell>;
  bullpen: Record<string, FeatureCell>;
  defense: Record<string, FeatureCell>;
  schedule: Record<string, FeatureCell>;
  team_strength: Record<string, FeatureCell>;
}

export interface PredictionChange {
  has_previous: boolean;
  message: string | null;
  previous_as_of: string | null;
  current_as_of: string | null;
  home_win_prob_previous: number | null;
  home_win_prob_current: number | null;
  home_win_prob_delta_pp: number | null;
  confidence_previous: number | null;
  confidence_current: number | null;
  completeness_previous: number | null;
  completeness_current: number | null;
  n_changed_features: number | null;
  changed_features: Array<{
    feature_key: string;
    previous: number | null;
    current: number | null;
    delta: number | null;
  }>;
}

export interface BacktestEvidence {
  available: boolean;
  reason: string | null;
  band: string | null;
  n: number | null;
  observed: number | null;
  predicted: number | null;
  run_id: string | null;
  overall_log_loss: number | null;
  overall_brier: number | null;
  overall_calibration_error: number | null;
  overall_n: number | null;
}

export interface GameDetail {
  card: GameCard;
  drivers_for: DriverSummary[];
  drivers_against: DriverSummary[];
  all_drivers: DriverSummary[];
  matchup_bars: MatchupBar[];
  home_detail: SideDetail;
  away_detail: SideDetail;
  matchup_history: Record<string, unknown>;
  environment: Record<string, unknown>;
  simulation: Unavailable;
  market: MarketComparison;
  backtest_evidence: BacktestEvidence;
  change_since_previous: PredictionChange;
  prediction_history: Array<{
    as_of: string;
    created_at: string;
    home_win_prob: number;
    confidence_score: number;
    data_completeness: number;
    recommendation: string;
    is_latest: boolean;
  }>;
  freshness: FreshnessEntry[];
  deferred_features: Record<
    string,
    Array<{
      key: string;
      display_name: string;
      category: string;
      description: string;
      phase: number;
    }>
  >;
}

export interface CalibrationBin {
  lower: number;
  upper: number;
  n: number;
  mean_predicted: number | null;
  observed_frequency: number | null;
  wilson_low: number | null;
  wilson_high: number | null;
}

export interface BacktestSlice {
  slice_type: string;
  slice_key: string;
  n_games: number;
  accuracy: number | null;
  log_loss: number | null;
  brier_score: number | null;
  calibration_error: number | null;
  max_calibration_error: number | null;
  roc_auc: number | null;
  roi: number | null;
  clv: number | null;
  extra: Record<string, unknown>;
}

export interface SanityFlag {
  code: string;
  gate: string;
  value: number;
  threshold: number;
  detail: string;
}

export interface BacktestReport {
  run_id: string;
  model_name: string;
  algorithm: string;
  feature_set_version: string;
  as_of_policy: string;
  start_date: string;
  end_date: string;
  step_days: number;
  validation_days: number;
  min_train_rows: number;
  seed: number;
  git_sha: string | null;
  n_games: number;
  n_steps: number;
  n_steps_skipped: number;
  created_at: string;
  sanity_flags: SanityFlag[];
  config: Record<string, unknown>;
  baseline_log_loss: number;
  overall: BacktestSlice | null;
  calibration_bins: CalibrationBin[];
  slices: Record<string, BacktestSlice[]>;
  odds_dependent_metrics: { available: boolean; reason: string };
}

export interface FeatureSpec {
  key: string;
  display_name: string;
  category: string;
  category_label: string;
  description: string;
  unit: string;
  window: string | null;
  min_sample: number;
  phase: number;
  available: boolean;
  source_category: string;
}

export interface DiagnosticsSnapshot {
  generated_at: string;
  environment: string;
  database: { reachable: boolean; server_version?: string; detail?: string };
  cache: { configured: boolean; reachable: boolean; version?: string; detail?: string };
  sources: Array<{
    source_name: string;
    category: string;
    status: SourceStatus;
    freshness: Freshness;
    last_success_at: string | null;
    last_failure_at: string | null;
    consecutive_failures: number;
    last_error: string | null;
    records_last_run: number | null;
    configured_provider: string | null;
    detail: string | null;
  }>;
  freshness: FreshnessEntry[];
  jobs: Array<{
    id: number;
    job_name: string;
    status: string;
    started_at: string;
    finished_at: string | null;
    duration_ms: number | null;
    rows_written: number | null;
    error: string | null;
  }>;
  failed_jobs: Array<{ id: number; job_name: string; error: string | null; started_at: string }>;
  missing_data: Record<string, number>;
  model: {
    active: Record<string, unknown> | null;
    unavailable_reason: string | null;
    history: Array<{
      id: number;
      version: string;
      trained_at: string;
      train_rows: number | null;
      is_active: boolean;
      log_loss: number | null;
    }>;
  };
  predictions: Record<string, unknown>;
  backtest: Record<string, unknown>;
  api_usage: Record<string, unknown>;
  feature_set: Record<string, unknown>;
  drift: { available: boolean; reason: string };
}
