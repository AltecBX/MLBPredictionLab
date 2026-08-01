"""Assembly of the game-list and game-detail view models.

The API never computes a prediction inside a GET — predictions are produced by
the prediction job and read back, so the served probability is identical to the
stored, auditable one (ARCHITECTURE.md §5).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    BacktestResult,
    BacktestRun,
    Ballpark,
    DataSourceStatus,
    Game,
    ModelFeature,
    ModelVersion,
    Player,
    Prediction,
    PredictionExplanation,
    Team,
)
from app.features.registry import CATEGORY_LABELS, deferred_by_source
from app.providers.base import DataCategory
from app.schemas.common import (
    BallparkRef,
    FreshnessEntry,
    PitcherRef,
    TeamRef,
    Unavailable,
    WarningEntry,
)
from app.schemas.games import (
    BacktestEvidence,
    DriverSummary,
    GameCard,
    GameDetail,
    MarketComparison,
    MatchupBar,
    PredictionChange,
    PredictionSummary,
    ProjectedScore,
    SideDetail,
)
from app.services.freshness import freshness_report
from app.services.prediction import diff_predictions, prediction_history

# A refresh within this window of the prediction is treated as concurrent.
STALENESS_GRACE = timedelta(minutes=5)

SORT_KEYS = {
    "game_time",
    "win_probability",
    "confidence",
    "closest",
    "completeness",
    "model_edge",
}

LINEUP_UNAVAILABLE_REASON = (
    "Pregame lineups require the Phase 2 lineup poller. Confirmed batting orders "
    "are ingested for completed games only."
)
WEATHER_UNAVAILABLE_REASON = (
    "No weather provider is configured. Set WEATHER_PROVIDER to enable forecast "
    "temperature, wind and humidity features."
)
SIMULATION_UNAVAILABLE_REASON = (
    "Monte Carlo simulation arrives in Phase 3 together with the run-scoring "
    "model. No simulated numbers are shown until it exists."
)
MARKET_UNAVAILABLE_REASON = (
    "Market comparison requires a licensed odds provider. Set ODDS_PROVIDER to "
    "enable implied probability, model edge and closing-line value."
)


def _team_ref(team: Team, wins: int | None, losses: int | None) -> TeamRef:
    return TeamRef(
        id=team.id,
        name=team.name,
        abbreviation=team.abbreviation,
        team_name=team.team_name,
        location_name=team.location_name,
        division_name=team.division_name,
        wins=wins,
        losses=losses,
    )


def _pitcher_ref(player: Player | None, confirmed: bool) -> PitcherRef:
    if player is None:
        return PitcherRef(status="UNKNOWN")
    return PitcherRef(
        id=player.id,
        full_name=player.full_name,
        pitch_hand=player.pitch_hand,
        status="CONFIRMED" if confirmed else "PROBABLE",
    )


def _ballpark_ref(park: Ballpark | None) -> BallparkRef:
    if park is None:
        return BallparkRef()
    return BallparkRef(
        id=park.id, name=park.name, city=park.city, state=park.state,
        roof_type=park.roof_type, elevation_ft=park.elevation_ft,
        lf_line=park.lf_line, center=park.center, rf_line=park.rf_line,
        turf_type=park.turf_type, capacity=park.capacity, timezone=park.timezone,
    )


def _weather_summary(game: Game) -> tuple[str, str | None]:
    """Observed weather exists only for played games from this source."""
    if game.weather_temp_f is not None or game.weather_condition:
        parts = [p for p in (
            game.weather_condition,
            f"{game.weather_temp_f}°F" if game.weather_temp_f is not None else None,
            game.weather_wind,
        ) if p]
        return "OBSERVED", ", ".join(parts) if parts else None
    return "UNAVAILABLE", None


def _projected_score(prediction: Prediction) -> ProjectedScore:
    meta = (prediction.feature_snapshot or {}).get("run_projection", {})
    return ProjectedScore(
        home_runs=_f(prediction.projected_home_runs),
        away_runs=_f(prediction.projected_away_runs),
        home_low=_i(prediction.projected_home_runs_low),
        home_high=_i(prediction.projected_home_runs_high),
        away_low=_i(prediction.projected_away_runs_low),
        away_high=_i(prediction.projected_away_runs_high),
        is_estimated=bool(meta.get("is_estimated", True)),
        method=meta.get("method"),
        detail=meta.get("detail"),
    )


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


def _i(value: Any) -> int | None:
    return None if value is None else int(value)


def _driver(row: PredictionExplanation) -> DriverSummary:
    return DriverSummary(
        feature_key=row.feature_key,
        display_name=row.display_name,
        category=row.category,
        category_label=CATEGORY_LABELS.get(row.category, row.category),
        favors=row.favors,
        contribution_pp=float(row.contribution_pp),
        feature_display=row.feature_display,
        sample_size=row.sample_size,
        is_estimated=row.is_estimated,
        narrative=row.narrative,
    )


def _prediction_summary(
    prediction: Prediction,
    version: ModelVersion | None,
    game: Game,
    drivers: list[PredictionExplanation],
) -> PredictionSummary:
    home_prob = float(prediction.home_win_prob)
    favored_home = home_prob >= 0.5
    return PredictionSummary(
        model_version_id=prediction.model_version_id,
        model_name=version.name if version else None,
        model_version=version.version if version else None,
        as_of=prediction.as_of,
        created_at=prediction.created_at,
        home_win_prob=home_prob,
        away_win_prob=float(prediction.away_win_prob),
        home_win_prob_uncalibrated=_f(prediction.home_win_prob_uncalibrated),
        predicted_winner="HOME" if favored_home else "AWAY",
        predicted_winner_team_id=game.home_team_id if favored_home else game.away_team_id,
        confidence_score=float(prediction.confidence_score),
        confidence_label=prediction.confidence_label,
        recommendation=prediction.recommendation,
        model_agreement=_f(prediction.model_agreement),
        data_completeness=float(prediction.data_completeness),
        missing_data=list(prediction.missing_data or []),
        warnings=[WarningEntry(**w) for w in (prediction.warnings or [])],
        component_probs={k: float(v) for k, v in (prediction.component_probs or {}).items()},
        projected_score=_projected_score(prediction),
        market=MarketComparison(
            available=prediction.market_home_prob is not None,
            reason=None if prediction.market_home_prob is not None
            else MARKET_UNAVAILABLE_REASON,
            market_home_prob=_f(prediction.market_home_prob),
            model_edge=_f(prediction.market_edge),
            fair_home_moneyline=prediction.fair_home_moneyline,
            fair_away_moneyline=prediction.fair_away_moneyline,
        ),
        top_drivers=[
            _driver(d)
            for d in drivers
            if d.favors == ("H" if favored_home else "A")
        ][:3],
    )


def load_games_for_date(session: Session, target: date) -> list[Game]:
    return list(
        session.scalars(
            select(Game)
            .where(Game.official_date == target)
            .order_by(Game.game_date_utc, Game.id)
        )
    )


# Categories whose refresh can materially change a prediction.
MATERIAL_CATEGORIES = (
    DataCategory.SCHEDULE,
    DataCategory.PROBABLE_PITCHERS,
    DataCategory.RESULTS,
    DataCategory.PLAYER_STATS,
    DataCategory.LINEUPS,
    DataCategory.WEATHER,
    DataCategory.INJURIES,
)


def _last_material_update(session: Session) -> datetime | None:
    """Newest successful refresh across the categories a prediction depends on."""
    return session.scalar(
        select(func.max(DataSourceStatus.last_success_at)).where(
            DataSourceStatus.category.in_([str(c) for c in MATERIAL_CATEGORIES])
        )
    )


def _staleness_warning(
    prediction: Prediction | None, last_update: datetime | None
) -> WarningEntry | None:
    """Flag a prediction that predates the most recent source refresh.

    Required by the product spec: the user must be told when a prediction has
    not been recalculated after a material update, rather than being shown a
    number that silently reflects older inputs.
    """
    if prediction is None or last_update is None:
        return None
    created = prediction.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    if last_update <= created + STALENESS_GRACE:
        return None
    minutes = int((last_update - created).total_seconds() // 60)
    return WarningEntry(
        code="PREDICTION_PREDATES_SOURCE_REFRESH",
        severity="medium",
        message=(
            f"Source data refreshed {minutes} minutes after this prediction was "
            f"generated. It has not been recalculated since."
        ),
    )


def build_game_cards(
    session: Session, games: list[Game], predictions: dict[int, Prediction]
) -> list[GameCard]:
    if not games:
        return []

    team_ids = {g.home_team_id for g in games} | {g.away_team_id for g in games}
    teams = {
        t.id: t for t in session.scalars(select(Team).where(Team.id.in_(team_ids)))
    }
    venue_ids = {g.venue_id for g in games if g.venue_id}
    parks = {
        p.id: p for p in session.scalars(select(Ballpark).where(Ballpark.id.in_(venue_ids)))
    } if venue_ids else {}
    pitcher_ids = {
        pid
        for g in games
        for pid in (g.home_probable_pitcher_id, g.away_probable_pitcher_id)
        if pid
    }
    pitchers = {
        p.id: p for p in session.scalars(select(Player).where(Player.id.in_(pitcher_ids)))
    } if pitcher_ids else {}

    prediction_ids = [p.id for p in predictions.values()]
    explanations: dict[int, list[PredictionExplanation]] = {}
    if prediction_ids:
        for row in session.scalars(
            select(PredictionExplanation)
            .where(PredictionExplanation.prediction_id.in_(prediction_ids))
            .order_by(PredictionExplanation.rank)
        ):
            explanations.setdefault(row.prediction_id, []).append(row)

    version_ids = {p.model_version_id for p in predictions.values()}
    versions = {
        v.id: v
        for v in session.scalars(select(ModelVersion).where(ModelVersion.id.in_(version_ids)))
    } if version_ids else {}

    last_update = _last_material_update(session)

    cards: list[GameCard] = []
    for game in games:
        prediction = predictions.get(game.id)
        weather_status, weather_summary = _weather_summary(game)
        card = GameCard(
            game_id=game.id,
            season=game.season,
            game_type=game.game_type,
            official_date=game.official_date,
            first_pitch_utc=game.game_date_utc,
            status=game.status_abstract or "Preview",
            status_detail=game.status_detailed,
            day_night=game.day_night,
            doubleheader=game.doubleheader,
            home=_team_ref(teams[game.home_team_id], game.home_record_wins,
                           game.home_record_losses),
            away=_team_ref(teams[game.away_team_id], game.away_record_wins,
                           game.away_record_losses),
            ballpark=_ballpark_ref(parks.get(game.venue_id) if game.venue_id else None),
            home_pitcher=_pitcher_ref(
                pitchers.get(game.home_probable_pitcher_id),
                game.probable_pitchers_confirmed,
            ),
            away_pitcher=_pitcher_ref(
                pitchers.get(game.away_probable_pitcher_id),
                game.probable_pitchers_confirmed,
            ),
            lineup_status="UNAVAILABLE",
            lineup_status_reason=LINEUP_UNAVAILABLE_REASON,
            weather_status=weather_status,
            weather_summary=weather_summary,
            home_score=game.home_score,
            away_score=game.away_score,
            is_final=game.is_final,
            prediction=(
                _prediction_summary(
                    prediction, versions.get(prediction.model_version_id), game,
                    explanations.get(prediction.id, []),
                )
                if prediction
                else None
            ),
            prediction_unavailable=(
                None
                if prediction
                else Unavailable(
                    reason="No prediction has been generated for this game yet. Either "
                           "the model has not run, or both teams lack enough as-of game "
                           "history to support one.",
                    required_source="run `make predict`",
                )
            ),
        )
        card.bullpen_warning = _bullpen_warning(prediction)
        stale = _staleness_warning(prediction, last_update)
        if stale is not None and card.prediction is not None:
            card.prediction.warnings = [stale, *card.prediction.warnings]
        cards.append(card)
    return cards


def _bullpen_warning(prediction: Prediction | None) -> str | None:
    if prediction is None:
        return None
    features = (prediction.feature_snapshot or {}).get("features", {}) or {}
    value = features.get("bp_fatigue_index_diff")
    if value is None:
        return None
    # Positive favors home, meaning the AWAY pen is more taxed.
    if value >= 0.35:
        return "Away bullpen is carrying a heavy recent workload."
    if value <= -0.35:
        return "Home bullpen is carrying a heavy recent workload."
    return None


def sort_cards(cards: list[GameCard], sort: str) -> list[GameCard]:
    if sort == "win_probability":
        return sorted(
            cards,
            key=lambda c: max(c.prediction.home_win_prob, c.prediction.away_win_prob)
            if c.prediction else -1,
            reverse=True,
        )
    if sort == "confidence":
        return sorted(
            cards,
            key=lambda c: c.prediction.confidence_score if c.prediction else -1,
            reverse=True,
        )
    if sort == "closest":
        return sorted(
            cards,
            key=lambda c: abs(c.prediction.home_win_prob - 0.5) if c.prediction else 99,
        )
    if sort == "completeness":
        return sorted(
            cards,
            key=lambda c: c.prediction.data_completeness if c.prediction else -1,
            reverse=True,
        )
    if sort == "model_edge":
        # Model edge requires a licensed odds provider; without one this falls
        # back to game time rather than inventing an ordering.
        return sorted(cards, key=lambda c: c.first_pitch_utc)
    return sorted(cards, key=lambda c: (c.first_pitch_utc, c.game_id))


def build_game_detail(
    session: Session, game: Game, prediction: Prediction | None
) -> GameDetail:
    cards = build_game_cards(session, [game], {game.id: prediction} if prediction else {})
    card = cards[0]

    drivers: list[PredictionExplanation] = []
    if prediction is not None:
        drivers = list(
            session.scalars(
                select(PredictionExplanation)
                .where(PredictionExplanation.prediction_id == prediction.id)
                .order_by(PredictionExplanation.rank)
            )
        )

    favored = "H" if (prediction and float(prediction.home_win_prob) >= 0.5) else "A"
    for_side = [_driver(d) for d in drivers if d.favors == favored][:5]
    against = [_driver(d) for d in drivers if d.favors != favored][:5]

    history = prediction_history(session, game.id, limit=20)
    previous = history[1] if len(history) > 1 else None

    return GameDetail(
        card=card,
        drivers_for=for_side,
        drivers_against=against,
        all_drivers=[_driver(d) for d in drivers],
        matchup_bars=_matchup_bars(drivers),
        home_detail=_side_detail(session, game, prediction, side="H"),
        away_detail=_side_detail(session, game, prediction, side="A"),
        matchup_history=_matchup_history(prediction),
        environment=_environment(card, prediction),
        simulation=Unavailable(reason=SIMULATION_UNAVAILABLE_REASON, phase=3),
        market=card.prediction.market
        if card.prediction
        else MarketComparison(available=False, reason=MARKET_UNAVAILABLE_REASON),
        backtest_evidence=_backtest_evidence(session, prediction),
        change_since_previous=PredictionChange(
            **diff_predictions(prediction, previous)
        ) if prediction else PredictionChange(has_previous=False,
                                              message="No prediction issued yet."),
        prediction_history=[
            {
                "as_of": p.as_of,
                "created_at": p.created_at,
                "home_win_prob": float(p.home_win_prob),
                "confidence_score": float(p.confidence_score),
                "data_completeness": float(p.data_completeness),
                "recommendation": p.recommendation,
                "is_latest": p.is_latest,
            }
            for p in history
        ],
        freshness=[FreshnessEntry(**row) for row in freshness_report(session)],
        deferred_features={
            source: [
                {
                    "key": item.key,
                    "display_name": item.display_name,
                    "category": str(item.category),
                    "description": item.description,
                    "phase": item.phase,
                }
                for item in items
            ]
            for source, items in deferred_by_source().items()
        },
    )


def _matchup_bars(drivers: list[PredictionExplanation]) -> list[MatchupBar]:
    totals: dict[str, dict[str, float]] = {}
    for row in drivers:
        entry = totals.setdefault(row.category, {"home": 0.0, "away": 0.0})
        if row.favors == "H":
            entry["home"] += float(row.contribution_pp)
        else:
            entry["away"] += float(row.contribution_pp)

    bars = []
    for category, entry in totals.items():
        net = entry["home"] - entry["away"]
        bars.append(
            MatchupBar(
                category=category,
                label=CATEGORY_LABELS.get(category, category),
                home_pp=round(entry["home"], 3),
                away_pp=round(entry["away"], 3),
                net_pp=round(net, 3),
                advantage="HOME" if net > 0.05 else "AWAY" if net < -0.05 else "EVEN",
            )
        )
    return sorted(bars, key=lambda b: abs(b.net_pp), reverse=True)


def _side_detail(
    session: Session, game: Game, prediction: Prediction | None, side: str
) -> SideDetail:
    team_id = game.home_team_id if side == "H" else game.away_team_id
    team = session.get(Team, team_id)
    pitcher_id = (
        game.home_probable_pitcher_id if side == "H" else game.away_probable_pitcher_id
    )
    pitcher = session.get(Player, pitcher_id) if pitcher_id else None

    row = None
    if prediction is not None:
        row = session.scalar(
            select(ModelFeature).where(
                ModelFeature.game_id == game.id,
                ModelFeature.team_side == side,
                ModelFeature.as_of == prediction.as_of,
            )
        )

    features = (row.features if row else {}) or {}
    samples = (row.sample_sizes if row else {}) or {}
    estimated = (row.estimated_flags if row else {}) or {}

    def pick(prefixes: tuple[str, ...]) -> dict[str, Any]:
        return {
            key: {
                "value": value,
                "sample_size": samples.get(key),
                "is_estimated": estimated.get(key, True),
            }
            for key, value in features.items()
            if key.startswith(prefixes)
        }

    return SideDetail(
        team=_team_ref(
            team,
            game.home_record_wins if side == "H" else game.away_record_wins,
            game.home_record_losses if side == "H" else game.away_record_losses,
        ),
        starter=_pitcher_ref(pitcher, game.probable_pitchers_confirmed),
        starter_stats=pick(("sp_",)),
        offense=pick(("off_",)),
        bullpen=pick(("bp_",)),
        defense=pick(("def_",)),
        schedule=pick(("sched_",)),
        team_strength=pick(("team_", "elo")),
    )


def _matchup_history(prediction: Prediction | None) -> dict[str, Any]:
    if prediction is None:
        return {"available": False, "reason": "No prediction available for this game."}
    features = (prediction.feature_snapshot or {}).get("features", {}) or {}
    samples = (prediction.feature_snapshot or {}).get("sample_sizes", {}) or {}
    value = features.get("h2h_season_series_shrunk_diff")
    return {
        "available": value is not None,
        "reason": None
        if value is not None
        else "The teams have not met yet this season, so no series record exists.",
        "season_series_shrunk_diff": value,
        "sample_size": samples.get("h2h_season_series_shrunk_diff"),
        "note": "Head-to-head is shrunk with k=40 games so a short series cannot "
                "outweigh season-long team quality (FEATURE_DICTIONARY.md §8).",
        "batter_vs_pitcher": {
            "available": False,
            "reason": "Batter-versus-pitcher history requires Phase 3 play-by-play "
                      "ingestion and is gated at 25 plate appearances.",
        },
    }


def _environment(card: GameCard, prediction: Prediction | None) -> dict[str, Any]:
    features = (prediction.feature_snapshot or {}).get("features", {}) if prediction else {}
    return {
        "ballpark": card.ballpark.model_dump(),
        "day_night": card.day_night,
        "is_dome": features.get("env_is_dome"),
        "elevation_km": features.get("env_venue_elevation_km"),
        "weather": {
            "status": card.weather_status,
            "summary": card.weather_summary,
            "reason": None if card.weather_status != "UNAVAILABLE"
            else WEATHER_UNAVAILABLE_REASON,
        },
        "park_factors": {
            "available": False,
            "reason": "Empirical park factors require the Phase 2 multi-season "
                      "regression. Physical ballpark attributes are shown instead.",
        },
        "umpire": {
            "available": False,
            "reason": "Umpire strike-zone profiles require Phase 2 play-by-play data.",
        },
    }


def _backtest_evidence(session: Session, prediction: Prediction | None) -> BacktestEvidence:
    if prediction is None:
        return BacktestEvidence(available=False, reason="No prediction to evaluate.")

    detail = (prediction.confidence_components or {}).get(
        "historical_calibration_detail", {}
    ) or {}
    run = session.scalar(select(BacktestRun).order_by(BacktestRun.created_at.desc()))
    if run is None:
        return BacktestEvidence(
            available=False,
            reason="No backtest has been run yet. Run `make backtest` to populate "
                   "historical reliability.",
        )
    overall = session.scalar(
        select(BacktestResult).where(
            BacktestResult.run_id == run.id, BacktestResult.slice_type == "overall"
        )
    )
    return BacktestEvidence(
        available="band" in detail and detail.get("n") is not None,
        reason=detail.get("reason"),
        band=detail.get("band"),
        n=detail.get("n"),
        observed=detail.get("observed"),
        predicted=detail.get("predicted"),
        run_id=str(run.id),
        overall_log_loss=_f(overall.log_loss) if overall else None,
        overall_brier=_f(overall.brier_score) if overall else None,
        overall_calibration_error=_f(overall.calibration_error) if overall else None,
        overall_n=overall.n_games if overall else None,
    )
