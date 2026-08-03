"""Operational entry point for every job.

    python -m app.cli <command> [options]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from app.core.clock import utcnow
from app.core.config import settings
from app.core.logging import configure_logging, get_logger

log = get_logger("cli")


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def cmd_bootstrap(args: argparse.Namespace) -> int:
    from app.db.session import session_scope
    from app.ingestion.runner import bootstrap

    with session_scope() as session:
        bootstrap(session)
    print("Seeded data_source_status for every category.")
    return 0


def cmd_ingest_reference(args: argparse.Namespace) -> int:
    from app.db.session import session_scope
    from app.ingestion.reference import run_reference_ingest

    with session_scope() as session:
        counts = run_reference_ingest(session, args.season)
    print(json.dumps(counts))
    return 0


def cmd_ingest_schedule(args: argparse.Namespace) -> int:
    from app.db.session import session_scope
    from app.ingestion.schedule import ingest_schedule_range, run_schedule_ingest

    with session_scope() as session:
        if args.start and args.end:
            counts = ingest_schedule_range(session, args.start, args.end)
        else:
            counts = run_schedule_ingest(session, args.days_back, args.days_forward)
    print(json.dumps(counts))
    return 0


def cmd_ingest_results(args: argparse.Namespace) -> int:
    from app.db.session import session_scope
    from app.ingestion.results import run_results_ingest

    with session_scope() as session:
        counts = run_results_ingest(session, limit=args.limit)
    print(json.dumps(counts))
    return 0


def cmd_ingest_history(args: argparse.Namespace) -> int:
    from app.db.session import session_scope
    from app.ingestion.runner import bootstrap, ingest_season

    seasons = [int(s) for s in args.seasons.split(",")]
    totals: dict[str, dict[str, int]] = {}
    with session_scope() as session:
        bootstrap(session)
    for season in seasons:
        with session_scope() as session:
            totals[str(season)] = ingest_season(
                session, season, with_boxscores=not args.skip_boxscores
            )
        log.info("ingest.history.season_done", season=season, **totals[str(season)])
    print(json.dumps(totals))
    return 0



def cmd_ingest_weather(args: argparse.Namespace) -> int:
    """Forecasts at first pitch, live for upcoming games or archived for past ones."""
    from datetime import date as _date

    from app.db.session import session_scope
    from app.ingestion.weather import ingest_weather_for_dates

    if args.seasons:
        ranges = [
            (_date(int(s), 1, 1), _date(int(s), 12, 31))
            for s in args.seasons.split(",")
        ]
    else:
        if not args.start or not args.end:
            print(json.dumps({"error": "provide --seasons or both --start and --end"}))
            return 1
        ranges = [(args.start, args.end)]

    # A session per range, not one spanning them all. A four-season backfill in
    # a single transaction holds every row uncommitted for the whole run and
    # loses the lot if the last season fails.
    total = 0
    for start, end in ranges:
        with session_scope() as session:
            total += ingest_weather_for_dates(
                session, start, end, archived=not args.live
            )
    print(json.dumps({"rows_written": total}))
    return 0



def cmd_ingest_injuries(args: argparse.Namespace) -> int:
    """Injured-list moves from the transactions feed."""
    from datetime import date as _date

    from app.db.session import session_scope
    from app.ingestion.injuries import ingest_injuries

    if args.seasons:
        ranges = [(_date(int(s), 2, 1), _date(int(s), 11, 30)) for s in args.seasons.split(",")]
    elif args.start and args.end:
        ranges = [(args.start, args.end)]
    else:
        print(json.dumps({"error": "provide --seasons or both --start and --end"}))
        return 1

    total = 0
    for start, end in ranges:
        with session_scope() as session:
            total += ingest_injuries(session, start, end)
    print(json.dumps({"rows_written": total}))
    return 0



def cmd_poll_lineups(args: argparse.Namespace) -> int:
    """Capture posted lineups for games that have not started yet."""
    from app.db.session import session_scope
    from app.ingestion.lineup_poller import poll_lineups

    with session_scope() as session:
        rows = poll_lineups(session, args.date)
    print(json.dumps({"rows_written": rows}))
    return 0


def cmd_ingest_statcast(args: argparse.Namespace) -> int:
    """Backfill Statcast for a date range, or for whole seasons.

    The range is explicit on purpose. Statcast tracking starts in 2015 and the
    fields this system depends on arrive later still (bat speed and swing length
    only from 2024), so "everything" is not a meaningful instruction — a season
    with no rows to find would be refetched on every run forever.
    """
    from app.db.session import session_scope
    from app.ingestion.statcast import ingest_statcast_range, season_bounds_for_statcast

    if args.seasons:
        windows = [season_bounds_for_statcast(int(s)) for s in args.seasons.split(",")]
    elif args.start and args.end:
        windows = [(args.start, args.end)]
    else:
        print("Pass either --seasons or both --start and --end.", file=sys.stderr)
        return 2

    totals: list[dict] = []
    for start, end in windows:
        if start > end:
            log.info("statcast.window_skipped", start=str(start), end=str(end))
            continue
        with session_scope() as session:
            result = ingest_statcast_range(
                session,
                start,
                end,
                limit_dates=args.limit_dates,
                reconcile=not args.no_reconcile,
            )
        totals.append({"start": start.isoformat(), "end": end.isoformat(), **result.as_dict()})
        log.info("statcast.window_done", start=str(start), end=str(end), pitches=result.pitches)

    print(json.dumps(totals, indent=2))
    # A discrepancy is a finding, not a crash: the rows are stored and the
    # affected games are named so a feature query can exclude them. Exit
    # non-zero so an unattended workflow surfaces it rather than reporting green.
    return 1 if any(t["discrepancy_count"] for t in totals) else 0


def cmd_daily(args: argparse.Namespace) -> int:
    """Ingest, predict, prune.

    `--skip-predictions` exists because the two halves are not equally
    dependent. Ingesting needs a database; predicting needs a *trained model*,
    and the daily workflow retrains after this runs. A fresh runner therefore
    had no loadable model, this step raised before the retrain, and the retrain
    that would have supplied one was never reached — the whole refresh
    deadlocked on a prediction it was about to throw away.
    """
    from app.db.session import session_scope
    from app.ingestion.maintenance import prune_raw_payloads
    from app.ingestion.runner import daily_refresh
    from app.services.prediction import generate_predictions_for_date

    with session_scope() as session:
        counts = daily_refresh(session)
    generated = 0
    if not getattr(args, "skip_predictions", False):
        with session_scope() as session:
            target = args.date or utcnow().date()
            generated = generate_predictions_for_date(session, target)
    # Enforce the payload retention bound on the same pass, so the archive
    # cannot grow unbounded on an unattended deployment.
    with session_scope() as session:
        pruned = prune_raw_payloads(session)
    print(
        json.dumps(
            {**counts, "predictions": generated, "payloads_pruned": pruned["deleted"]}
        )
    )
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    from app.db.session import session_scope
    from app.ingestion.maintenance import prune_raw_payloads

    with session_scope() as session:
        result = prune_raw_payloads(session, older_than_days=args.older_than_days)
    print(json.dumps(result))
    return 0


def cmd_train(args: argparse.Namespace) -> int:
    from app.db.session import session_scope
    from app.modeling.train import train_model

    with session_scope() as session:
        version = train_model(
            session,
            seasons=[int(s) for s in args.seasons.split(",")] if args.seasons else None,
            activate=not args.no_activate,
        )
    print(json.dumps(version))
    return 0


def cmd_predict(args: argparse.Namespace) -> int:
    from app.db.session import session_scope
    from app.services.prediction import generate_predictions_for_date

    target = args.date or utcnow().date()
    with session_scope() as session:
        count = generate_predictions_for_date(session, target, force=args.force)
    print(json.dumps({"date": target.isoformat(), "predictions": count}))
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    from app.backtest.engine import run_backtest
    from app.db.session import session_scope

    with session_scope() as session:
        summary = run_backtest(
            session,
            start=args.start,
            end=args.end,
            step_days=args.step_days,
            ablation=not args.no_ablation,
            feature_set_version=args.feature_set,
        )
    print(json.dumps(summary, default=str, indent=2))
    return 0


def cmd_ensemble_check(args: argparse.Namespace) -> int:
    """Measure whether blending a GBDT into the logistic model helps.

    Prints the comparison and exits non-zero only on failure to run — a verdict
    of "no improvement" is a successful measurement, not an error.
    """
    from app.backtest.walkforward import make_steps
    from app.db.session import session_scope
    from app.modeling.dataset import build_dataset
    from app.modeling.ensemble import compare_walk_forward
    from app.modeling.registry import get_active_version

    with session_scope() as session:
        version = get_active_version(session)
        C = float((version.hyperparameters or {}).get("C", 0.001))
        dataset = build_dataset(session, seasons=None)
        steps = make_steps(dataset.labelled, step_days=args.step_days)
        comparison = compare_walk_forward(dataset, steps, C=C)

    if comparison is None:
        print(json.dumps({"error": "walk-forward produced no comparable games"}))
        return 1

    print(
        json.dumps(
            {
                "n_games": comparison.n_games,
                "logistic": comparison.logistic.metrics,
                "gbdt": comparison.gbdt.metrics,
                "blended": comparison.blended.metrics,
                "best_weight": comparison.best_weight,
                "weight_grid": {w: m["log_loss"] for w, m in comparison.weight_grid.items()},
                "improves": comparison.improves,
                "verdict": comparison.verdict,
            },
            indent=2,
            default=str,
        )
    )
    return 0


def cmd_simulate_check(args: argparse.Namespace) -> int:
    """Measure whether simulating runs beats predicting the winner directly.

    Prints the comparison and exits non-zero only on failure to run — a verdict
    of "no improvement" is a successful measurement, not an error.
    """
    from app.backtest.walkforward import make_steps
    from app.db.session import session_scope
    from app.features.asof import AsOfStore
    from app.modeling.dataset import build_dataset
    from app.modeling.registry import get_active_version
    from app.modeling.simulation import compare_walk_forward

    seasons = [int(s) for s in args.seasons.split(",")] if args.seasons else None
    with session_scope() as session:
        try:
            version = get_active_version(session)
            C = float((version.hyperparameters or {}).get("C", 0.001))
        except Exception:  # noqa: BLE001 - no active model is not fatal here
            C = 0.001
        store = AsOfStore.load(session, seasons)
        dataset = build_dataset(session, seasons=seasons, store=store)
        steps = make_steps(
            dataset.labelled, start=args.start, end=args.end, step_days=args.step_days
        )
        comparison = compare_walk_forward(
            store, dataset, steps, C=C, simulations=args.simulations,
            asof_dispersion=args.asof_dispersion,
        )

    if comparison is None:
        print(json.dumps({"error": "walk-forward produced no comparable games"}))
        return 1
    print(json.dumps(comparison.to_dict(), indent=2, default=str))
    return 0


def cmd_run_model_check(args: argparse.Namespace) -> int:
    """Ablate the run model's refinements against the crude version they refine.

    Exits 0 whatever the verdict. A refinement that does not earn its place is a
    measurement, and this repository keeps those.
    """
    from app.backtest.walkforward import make_steps
    from app.db.session import session_scope
    from app.features.asof import AsOfStore
    from app.modeling.dataset import build_dataset
    from app.modeling.registry import get_active_version
    from app.modeling.run_model_compare import compare_run_models

    seasons = [int(s) for s in args.seasons.split(",")] if args.seasons else None
    with session_scope() as session:
        try:
            version = get_active_version(session)
            C = float((version.hyperparameters or {}).get("C", 0.001))
        except Exception:  # noqa: BLE001 - no active model is not fatal here
            C = 0.001
        store = AsOfStore.load(session, seasons)
        dataset = build_dataset(session, seasons=seasons, store=store)
        steps = make_steps(
            dataset.labelled, start=args.start, end=args.end, step_days=args.step_days
        )
        comparison = compare_run_models(
            store, dataset, steps, C=C, simulations=args.simulations
        )

    if comparison is None:
        print(json.dumps({"error": "walk-forward produced no comparable games"}))
        return 1
    print(json.dumps(comparison.to_dict(), indent=2, default=str))
    return 0


def cmd_compare_feature_sets(args: argparse.Namespace) -> int:
    """Walk-forward comparison of two feature sets, on the same games.

    This is the gate a candidate feature group has to pass. Exits 0 whatever the
    verdict — a measured "no" is a successful measurement, and the repository
    keeps those on purpose.
    """
    from app.backtest.feature_set_compare import compare_feature_sets
    from app.db.session import session_scope

    seasons = [int(s) for s in args.seasons.split(",")] if args.seasons else None
    with session_scope() as session:
        comparison = compare_feature_sets(
            session,
            baseline_version=args.baseline,
            candidate_version=args.candidate,
            seasons=seasons,
            start=args.start,
            end=args.end,
            step_days=args.step_days,
            C=args.C,
        )

    if comparison is None:
        print(json.dumps({"error": "walk-forward produced no comparable games"}))
        return 1
    print(json.dumps(comparison.to_dict(), indent=2, default=str))
    return 0


def cmd_challenger_check(args: argparse.Namespace) -> int:
    """XGBoost, LightGBM, Elo and a stacked meta-model against the baseline.

    One walk-forward pass, every model on identical out-of-sample games, the
    promotion rule answered from the result. Exits 0 whatever the verdict —
    "the baseline stands" is a successful measurement, not an error.
    """
    from app.backtest.challenger_report import (
        paired_against_baseline,
        promotion_verdict,
        score_model,
        strip_private,
    )
    from app.backtest.walkforward import make_steps
    from app.db.session import session_scope
    from app.modeling.challenger import causal_calibrate, collect_oof, stacked_oof
    from app.modeling.dataset import build_dataset
    from app.modeling.registry import get_active_version

    seasons = [int(s) for s in args.seasons.split(",")] if args.seasons else None
    start = date.fromisoformat(args.start) if args.start else None

    with session_scope() as session:
        version = get_active_version(session)
        C = float((version.hyperparameters or {}).get("C", 0.001))
        dataset = build_dataset(session, seasons=seasons)
        steps = make_steps(dataset.labelled, start=start, step_days=args.step_days)
        oof, fold_meta = collect_oof(dataset, steps, C=C)

    if oof.empty:
        print(json.dumps({"error": "walk-forward produced no comparable games"}))
        return 1

    # Per-model calibration, selected prequentially on strictly-earlier OOF rows.
    calibrated = {}
    for name, raw in (
        ("logistic", "raw_logistic"),
        ("xgb", "raw_xgb"),
        ("lgbm", "raw_lgbm"),
        ("elo", "raw_elo"),
    ):
        stream = causal_calibrate(oof, raw)
        oof[f"cal_{name}"] = stream.calibrated
        calibrated[name] = {
            "selected_method": stream.selected_method,
            "pooled_candidates": stream.pooled,
            "method_by_step": stream.method_by_step,
        }

    stack_probs, stack_mask, stack_weights = stacked_oof(
        oof, input_columns=("cal_logistic", "cal_xgb", "cal_lgbm", "cal_elo")
    )
    oof["p_stack"] = stack_probs
    stack_stream = causal_calibrate(
        oof[stack_mask].reset_index(drop=True).assign(raw_stack=stack_probs[stack_mask]),
        "raw_stack",
    )
    stacked = oof[stack_mask].reset_index(drop=True)
    stacked["cal_stack"] = stack_stream.calibrated
    calibrated["stack"] = {
        "selected_method": stack_stream.selected_method,
        "pooled_candidates": stack_stream.pooled,
    }

    full_seasons = sorted(
        str(int(s)) for s, g in oof.groupby("season") if len(g) >= 1000
    )

    models = {
        "logistic_baseline": ("p_logistic", oof),
        "xgboost": ("cal_xgb", oof),
        "lightgbm": ("cal_lgbm", oof),
        "elo": ("cal_elo", oof),
        "stacked_ensemble": ("cal_stack", stacked),
    }
    report: dict[str, object] = {
        "n_games": int(len(oof)),
        "n_stack_games": int(len(stacked)),
        "baseline_C": C,
        "seasons": full_seasons,
        "calibration": {
            k: {kk: vv for kk, vv in v.items() if kk != "method_by_step"}
            for k, v in calibrated.items()
        },
        "folds": fold_meta["folds"],
        "stack_weights": stack_weights,
        "models": {},
        "paired_vs_baseline": {},
        "verdicts": {},
    }
    for label, (column, frame) in models.items():
        report["models"][label] = score_model(frame, column)  # type: ignore[index]
        if label == "logistic_baseline":
            continue
        paired = paired_against_baseline(frame, "p_logistic", column)
        report["paired_vs_baseline"][label] = strip_private(paired)  # type: ignore[index]
        report["verdicts"][label] = promotion_verdict(paired, full_seasons)  # type: ignore[index]

    out = strip_private(report)
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_check_sources(args: argparse.Namespace) -> int:
    from app.db.session import session_scope
    from app.services.diagnostics import refresh_freshness

    with session_scope() as session:
        rows = refresh_freshness(session)
    print(json.dumps(rows, default=str, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli", description=settings.app_name)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("bootstrap", help="Seed source status rows").set_defaults(
        func=cmd_bootstrap
    )

    p = sub.add_parser("ingest-reference", help="Teams, ballparks")
    p.add_argument("--season", type=int, default=utcnow().year)
    p.set_defaults(func=cmd_ingest_reference)

    p = sub.add_parser("ingest-schedule", help="Schedule window or explicit range")
    p.add_argument("--start", type=_parse_date)
    p.add_argument("--end", type=_parse_date)
    p.add_argument("--days-back", type=int, default=None)
    p.add_argument("--days-forward", type=int, default=None)
    p.set_defaults(func=cmd_ingest_schedule)

    p = sub.add_parser("ingest-results", help="Backfill missing boxscores")
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_ingest_results)

    p = sub.add_parser("ingest-history", help="Backfill whole seasons")
    p.add_argument("--seasons", required=True, help="e.g. 2023,2024,2025")
    p.add_argument("--skip-boxscores", action="store_true")
    p.set_defaults(func=cmd_ingest_history)

    p = sub.add_parser(
        "ingest-weather",
        help="Forecast weather at first pitch (Open-Meteo, no API key required)",
    )
    p.add_argument("--start", type=_parse_date)
    p.add_argument("--end", type=_parse_date)
    p.add_argument("--seasons", default=None, help="e.g. 2023,2024,2025")
    p.add_argument(
        "--live",
        action="store_true",
        help="Use the live forecast endpoint and stamp knowledge_time as now. "
             "Default is the archive, for backfilling past games.",
    )
    p.set_defaults(func=cmd_ingest_weather)

    p = sub.add_parser("ingest-injuries", help="Injured-list moves from MLB transactions")
    p.add_argument("--start", type=_parse_date)
    p.add_argument("--end", type=_parse_date)
    p.add_argument("--seasons", default=None, help="e.g. 2024,2025")
    p.set_defaults(func=cmd_ingest_injuries)

    p = sub.add_parser(
        "poll-lineups",
        help="Capture posted lineups pregame, stamped with the moment observed",
    )
    p.add_argument("--date", type=_parse_date, default=None)
    p.set_defaults(func=cmd_poll_lineups)

    p = sub.add_parser("ingest-statcast", help="Backfill Statcast pitches for a range")
    p.add_argument("--start", type=_parse_date)
    p.add_argument("--end", type=_parse_date)
    p.add_argument("--seasons", default=None, help="e.g. 2023,2024,2025 (overrides start/end)")
    p.add_argument("--limit-dates", type=int, default=None)
    p.add_argument("--no-reconcile", action="store_true")
    p.set_defaults(func=cmd_ingest_statcast)

    p = sub.add_parser("daily", help="Refresh schedule + results, then predict")
    p.add_argument("--date", type=_parse_date, default=None)
    # For a caller that retrains and reissues afterwards. Predicting here would
    # be superseded anyway, and doing it first makes the ingest depend on a
    # model existing — which is a deadlock when the step that creates one comes
    # later. See `cmd_daily`.
    p.add_argument("--skip-predictions", action="store_true")
    p.set_defaults(func=cmd_daily)

    p = sub.add_parser("train", help="Walk-forward fit and register a model version")
    p.add_argument("--seasons", default=None)
    p.add_argument("--no-activate", action="store_true")
    p.set_defaults(func=cmd_train)

    p = sub.add_parser("predict", help="Generate predictions for a date")
    p.add_argument("--date", type=_parse_date, default=None)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_predict)

    p = sub.add_parser("backtest", help="Walk-forward evaluation")
    p.add_argument("--start", type=_parse_date, default=None)
    p.add_argument("--end", type=_parse_date, default=None)
    p.add_argument("--step-days", type=int, default=30)
    p.add_argument("--no-ablation", action="store_true")
    p.add_argument(
        "--feature-set", default=None,
        help="Feature set to evaluate. Default: the configured active set.",
    )
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser(
        "prune", help="Delete stored raw payloads past the retention window"
    )
    p.add_argument(
        "--older-than-days",
        type=int,
        default=None,
        help="Override RAW_PAYLOAD_RETENTION_DAYS for this run",
    )
    p.set_defaults(func=cmd_prune)

    p = sub.add_parser(
        "ensemble-check",
        help="Walk-forward comparison of logistic vs GBDT vs blend",
    )
    p.add_argument("--step-days", type=int, default=45)
    p.set_defaults(func=cmd_ensemble_check)

    p = sub.add_parser(
        "compare-feature-sets",
        help="Walk-forward comparison of a candidate feature set against the active one",
    )
    p.add_argument("--baseline", default="fs_v1")
    p.add_argument("--candidate", default="fs_v2")
    p.add_argument("--seasons", default=None, help="e.g. 2024,2025")
    p.add_argument("--start", type=_parse_date, default=None)
    p.add_argument("--end", type=_parse_date, default=None)
    p.add_argument("--step-days", type=int, default=30)
    p.add_argument(
        "--C", type=float, default=None,
        help="Pin regularisation for both sets. Default: select each set's own, "
             "walk-forward, by the same rule the trainer uses.",
    )
    p.set_defaults(func=cmd_compare_feature_sets)

    p = sub.add_parser(
        "challenger-check",
        help="XGBoost, LightGBM, Elo and a stacked meta-model vs the baseline, walk-forward",
    )
    p.add_argument("--seasons", default=None, help="e.g. 2024,2025; default all")
    p.add_argument("--start", default=None, help="lower bound on test windows, YYYY-MM-DD")
    p.add_argument("--step-days", type=int, default=30)
    p.add_argument("--out", default=None, help="also write the JSON report here")
    p.set_defaults(func=cmd_challenger_check)

    p = sub.add_parser(
        "simulate-check",
        help="Walk-forward comparison of the logistic model against a run simulation",
    )
    p.add_argument("--seasons", default=None, help="e.g. 2024,2025")
    p.add_argument("--start", type=_parse_date, default=None)
    p.add_argument("--end", type=_parse_date, default=None)
    p.add_argument("--step-days", type=int, default=30)
    p.add_argument("--simulations", type=int, default=20000)
    p.add_argument(
        "--asof-dispersion",
        action="store_true",
        help="Fit dispersion per slate as the serving path does, not once on the "
             "training side. This is how the served blend is actually scored.",
    )
    p.set_defaults(func=cmd_simulate_check)

    p = sub.add_parser(
        "run-model-check",
        help="Ablate the run model's park and starting-pitcher refinements",
    )
    p.add_argument("--seasons", default=None, help="e.g. 2024,2025")
    p.add_argument("--start", type=_parse_date, default=None)
    p.add_argument("--end", type=_parse_date, default=None)
    p.add_argument("--step-days", type=int, default=30)
    p.add_argument("--simulations", type=int, default=20000)
    p.set_defaults(func=cmd_run_model_check)

    sub.add_parser("check-sources", help="Recompute freshness").set_defaults(
        func=cmd_check_sources
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
