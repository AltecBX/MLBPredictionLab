"""Operational entry point for every job.

    python -m app.cli <command> [options]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta

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


def cmd_daily(args: argparse.Namespace) -> int:
    from app.db.session import session_scope
    from app.ingestion.runner import daily_refresh
    from app.services.prediction import generate_predictions_for_date

    with session_scope() as session:
        counts = daily_refresh(session)
    with session_scope() as session:
        target = args.date or utcnow().date()
        generated = generate_predictions_for_date(session, target)
    print(json.dumps({**counts, "predictions": generated}))
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
        )
    print(json.dumps(summary, default=str, indent=2))
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

    p = sub.add_parser("daily", help="Refresh schedule + results, then predict")
    p.add_argument("--date", type=_parse_date, default=None)
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
    p.set_defaults(func=cmd_backtest)

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
