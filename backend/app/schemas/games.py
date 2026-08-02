"""Game list and game detail DTOs."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.schemas.common import (
    ApiModel,
    BallparkRef,
    FreshnessEntry,
    PitcherRef,
    TeamRef,
    Unavailable,
    WarningEntry,
)


class DriverSummary(ApiModel):
    feature_key: str
    display_name: str
    category: str
    category_label: str
    favors: str
    contribution_pp: float
    feature_display: str | None = None
    sample_size: int | None = None
    is_estimated: bool = False
    narrative: str | None = None


class ProjectedScore(ApiModel):
    home_runs: float | None = None
    away_runs: float | None = None
    home_low: int | None = None
    home_high: int | None = None
    away_low: int | None = None
    away_high: int | None = None
    is_estimated: bool = True
    method: str | None = None
    detail: str | None = None


class MarketComparison(ApiModel):
    available: bool = False
    reason: str | None = None
    market_home_prob: float | None = None
    model_edge: float | None = None
    fair_home_moneyline: int | None = None
    fair_away_moneyline: int | None = None


class PredictionSummary(ApiModel):
    model_version_id: int
    model_name: str | None = None
    model_version: str | None = None
    as_of: datetime
    created_at: datetime
    home_win_prob: float
    away_win_prob: float
    home_win_prob_uncalibrated: float | None = None
    predicted_winner: str          # 'HOME' | 'AWAY'
    predicted_winner_team_id: int
    confidence_score: float
    confidence_label: str
    recommendation: str
    model_agreement: float | None = None
    data_completeness: float
    missing_data: list[str] = []
    warnings: list[WarningEntry] = []
    component_probs: dict[str, float] = {}
    projected_score: ProjectedScore
    market: MarketComparison
    top_drivers: list[DriverSummary] = []


class GameCard(ApiModel):
    game_id: int
    season: int
    game_type: str
    official_date: date
    first_pitch_utc: datetime
    status: str
    status_detail: str | None = None
    day_night: str | None = None
    doubleheader: str | None = None
    home: TeamRef
    away: TeamRef
    ballpark: BallparkRef
    home_pitcher: PitcherRef
    away_pitcher: PitcherRef
    lineup_status: str = "UNAVAILABLE"
    lineup_status_reason: str | None = None
    weather_status: str = "UNAVAILABLE"
    weather_summary: str | None = None
    bullpen_warning: str | None = None
    home_score: int | None = None
    away_score: int | None = None
    is_final: bool = False
    prediction: PredictionSummary | None = None
    prediction_unavailable: Unavailable | None = None


class GameListResponse(ApiModel):
    date: date
    count: int
    generated_at: datetime
    model_version: str | None = None
    freshness: list[FreshnessEntry] = []
    games: list[GameCard] = []


class MatchupBar(ApiModel):
    category: str
    label: str
    home_pp: float
    away_pp: float
    net_pp: float
    advantage: str  # 'HOME' | 'AWAY' | 'EVEN'


class MatchupSummaryRow(ApiModel):
    """One row of the fixed nine-row five-second read.

    `advantage` is one of HOME / AWAY / EVEN / UNAVAILABLE. EVEN means measured
    and level, which is a finding; UNAVAILABLE means not measured, which is not
    the same thing and is never rendered as EVEN.
    """

    key: str
    label: str
    advantage: str
    team: str | None = None
    value: str | None = None
    magnitude_pp: float | None = None
    detail: str | None = None
    available: bool = True
    required_source: str | None = None
    # True when the row describes rather than contributes probability.
    is_context: bool = False


class SideDetail(ApiModel):
    team: TeamRef
    starter: PitcherRef
    starter_stats: dict[str, Any] = {}
    offense: dict[str, Any] = {}
    bullpen: dict[str, Any] = {}
    defense: dict[str, Any] = {}
    schedule: dict[str, Any] = {}
    team_strength: dict[str, Any] = {}


class PredictionChange(ApiModel):
    has_previous: bool
    message: str | None = None
    previous_as_of: datetime | None = None
    current_as_of: datetime | None = None
    home_win_prob_previous: float | None = None
    home_win_prob_current: float | None = None
    home_win_prob_delta_pp: float | None = None
    confidence_previous: float | None = None
    confidence_current: float | None = None
    completeness_previous: float | None = None
    completeness_current: float | None = None
    n_changed_features: int | None = None
    changed_features: list[dict[str, Any]] = []
    #: Exact decomposition of the move: which features moved it, and how much
    #: belongs to the calibrator and to the run simulation rather than to any
    #: feature. Absent when the model that issued both snapshots is unavailable.
    attribution: dict[str, Any] | None = None


class BacktestEvidence(ApiModel):
    available: bool = False
    reason: str | None = None
    band: str | None = None
    n: int | None = None
    observed: float | None = None
    predicted: float | None = None
    run_id: str | None = None
    overall_log_loss: float | None = None
    overall_brier: float | None = None
    overall_calibration_error: float | None = None
    overall_n: int | None = None


class SimulationScore(ApiModel):
    away: int
    home: int
    probability: float


class SimulationDetail(ApiModel):
    """A real Monte Carlo result. Only ever built from a persisted simulation.

    `available` is True here by construction — the alternative is `Unavailable`,
    which carries a reason. A simulation that did not run is never a
    `SimulationDetail` full of zeros.
    """

    available: bool = True
    n_simulations: int
    home_win_pct: float
    away_win_pct: float
    mean_home_runs: float | None = None
    mean_away_runs: float | None = None
    #: P(exactly n runs), index 0..max_reported_runs; the last entry is "or more".
    home_run_distribution: list[float] = []
    away_run_distribution: list[float] = []
    max_reported_runs: int | None = None
    likely_scores: list[SimulationScore] = []
    #: Share of outcomes the listed scores account for. The rest is a long tail.
    likely_scores_covered: float | None = None
    extra_innings_prob: float | None = None
    one_run_prob: float | None = None
    upset_prob: float | None = None
    seed: int | None = None
    #: How the served probability was formed from this simulation.
    blend_weight: float | None = None
    blended_with_logistic: bool = False


class GameDetail(ApiModel):
    card: GameCard
    drivers_for: list[DriverSummary] = []
    drivers_against: list[DriverSummary] = []
    all_drivers: list[DriverSummary] = []
    matchup_bars: list[MatchupBar] = []
    matchup_summary: list[MatchupSummaryRow] = []
    home_detail: SideDetail
    away_detail: SideDetail
    matchup_history: dict[str, Any] = {}
    environment: dict[str, Any] = {}
    simulation: SimulationDetail | Unavailable
    market: MarketComparison
    backtest_evidence: BacktestEvidence
    change_since_previous: PredictionChange
    prediction_history: list[dict[str, Any]] = []
    freshness: list[FreshnessEntry] = []
    deferred_features: dict[str, list[dict[str, Any]]] = {}
