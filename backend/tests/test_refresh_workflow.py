"""The daily refresh must not need a model in order to make one.

Every run of `refresh.yml` had failed, and the reason was an ordering deadlock
rather than anything wrong with the training:

    Refresh data  ->  python -m app.cli daily      # predicts. needs a model.
    Retrain       ->  python -m app.cli train      # makes a model. never reached.

`daily` predicted before the retrain, predicting requires a loadable artifact,
and on a fresh runner there was none — so the step that would have supplied one
was never reached. A comment in the workflow described that prediction as
harmlessly superseded by the explicit reissue below it. It was not harmless; it
was the only thing standing between the workflow and its own first success.

These are cheap structural assertions rather than a rerun of the workflow,
because the failure is in the order of two lines of YAML and nothing executed
locally would notice.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "refresh.yml"


@pytest.fixture(scope="module")
def steps() -> list[dict]:
    spec = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return spec["jobs"]["refresh"]["steps"]


def _index_of(steps: list[dict], fragment: str) -> int:
    for i, step in enumerate(steps):
        if fragment in (step.get("run") or ""):
            return i
    raise AssertionError(f"No step in refresh.yml runs {fragment!r}")


def test_the_ingest_does_not_predict_before_the_retrain(steps):
    """The deadlock, asserted directly.

    A bare `app.cli daily` here would predict, and predicting needs the model
    the next step has not built yet.
    """
    ingest = steps[_index_of(steps, "app.cli daily")]
    assert "--skip-predictions" in ingest["run"], (
        "refresh.yml retrains AFTER this step, so predicting here requires a "
        "model that may not exist and takes the retrain down with it."
    )


def test_predictions_are_issued_after_the_retrain(steps):
    """Ordering is the whole fix, so it is the thing to assert."""
    assert _index_of(steps, "app.cli train") < _index_of(steps, "app.cli predict")


def test_the_flag_actually_skips_predicting(monkeypatch):
    """A workflow flag that the CLI silently ignores would look identical."""
    import argparse

    import app.services.prediction as prediction
    from app import cli

    def _boom(*args, **kwargs):
        raise AssertionError("predictions must not be generated when skipped")

    monkeypatch.setattr(prediction, "generate_predictions_for_date", _boom)

    import app.ingestion.maintenance as maintenance
    import app.ingestion.runner as runner

    monkeypatch.setattr(runner, "daily_refresh", lambda session: {"games": 0})
    monkeypatch.setattr(maintenance, "prune_raw_payloads", lambda session: {"deleted": 0})

    class _NullSession:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(cli, "session_scope", _NullSession, raising=False)
    import app.db.session as db_session

    monkeypatch.setattr(db_session, "session_scope", _NullSession)

    args = argparse.Namespace(date=None, skip_predictions=True)
    assert cli.cmd_daily(args) == 0


def test_both_workflows_agree_on_which_providers_exist():
    """A category cannot be configured in one job and absent in the other.

    `check-sources` runs in `refresh.yml` and rewrites every row of the
    Diagnostics source table from `configured_categories()`, which reads these
    variables. `pregame.yml` sets them and `refresh.yml` did not, so the daily
    job overwrote working categories with "No provider configured": the
    deployed site reported lineups and weather as UNAVAILABLE on the same
    screen whose job log showed `poll_lineups` writing 180 rows and
    `ingest_weather` writing 15.

    UNAVAILABLE means "not measured". Claiming it for something measured is the
    same lie as the reverse, and it is the one this product rules out first.
    """
    root = WORKFLOW.parent
    keys = ("LINEUP_PROVIDER", "INJURY_PROVIDER", "WEATHER_PROVIDER")
    envs = {}
    for name in ("refresh.yml", "pregame.yml"):
        spec = yaml.safe_load((root / name).read_text(encoding="utf-8"))
        job = next(iter(spec["jobs"].values()))
        envs[name] = {k: v for k, v in (job.get("env") or {}).items() if k in keys}

    assert envs["refresh.yml"] == envs["pregame.yml"], (
        "refresh.yml runs check-sources, which recomputes every source row from "
        f"these variables. Disagreeing with pregame.yml makes the site report "
        f"working ingests as UNAVAILABLE. {envs}"
    )
    assert set(envs["refresh.yml"]) == set(keys), (
        f"Every provider the pollers actually use must be named: {envs['refresh.yml']}"
    )


def test_injuries_are_actually_ingested_somewhere(steps):
    """The table was empty because nothing fetched it, not because of a bug.

    "Injuries unavailable" was honest, and honest about the wrong thing: no
    workflow ran `ingest-injuries` at all.
    """
    _index_of(steps, "app.cli ingest-injuries")


def test_omitting_the_flag_still_predicts():
    """The default must not quietly become "never predict"."""
    import inspect

    from app import cli

    source = inspect.getsource(cli.cmd_daily)
    assert "generate_predictions_for_date" in source
    assert 'getattr(args, "skip_predictions", False)' in source
