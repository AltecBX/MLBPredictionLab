"""The database is a release asset, and every workflow must treat it as one.

A free hosted Postgres expired thirty days after it was created. Every refresh
and every publish then failed for three days before anyone could tell, because
the site is static and kept serving the last build. The replacement has no
hosted database: each job runs a Postgres service container, restores the
latest dump from the `data` release, and — if it writes — saves a new dump
back. These assertions are the shape of that contract, so a workflow cannot
quietly reacquire a `DATABASE_URL` secret, restore without a service to
restore into, or write without saving.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2] / ".github"
WORKFLOWS = ROOT / "workflows"

#: Workflows that change the database, and must therefore save it.
WRITERS = ("refresh.yml", "pregame.yml", "seed.yml", "statcast.yml")
#: Workflows that only read it.
READERS = ("pages.yml",)

RESTORE = "./.github/actions/db-restore"
SAVE = "./.github/actions/db-save"


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _job(spec: dict) -> dict:
    """The job that touches the database: the one with a Postgres service."""
    with_service = [j for j in spec["jobs"].values() if "postgres" in (j.get("services") or {})]
    assert len(with_service) == 1, "exactly one job per workflow runs the database"
    return with_service[0]


def _uses(job: dict, action: str) -> list[int]:
    return [i for i, s in enumerate(job["steps"]) if s.get("uses") == action]


@pytest.mark.parametrize("name", WRITERS + READERS)
def test_no_workflow_needs_a_database_secret(name):
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    assert "secrets.DATABASE_URL" not in text, (
        f"{name} reads a DATABASE_URL secret. The database is the `data` release; "
        "a hosted one expires."
    )
    job = _job(_load(name))
    assert job["env"]["DATABASE_URL"].startswith("postgresql+psycopg://jerry:jerry@localhost")


@pytest.mark.parametrize("name", WRITERS + READERS)
def test_every_database_job_runs_a_service_and_restores_first(name):
    job = _job(_load(name))
    service = job["services"]["postgres"]
    assert service["image"].startswith("postgres:16"), "the dump is taken by a 16 client"
    assert "pg_isready" in service["options"]
    restores = _uses(job, RESTORE)
    assert len(restores) == 1, f"{name} must restore exactly once"
    runs_before = [
        s.get("run", "") for s in job["steps"][: restores[0]] if s.get("run")
    ]
    assert not any("app.cli" in r or "alembic" in r for r in runs_before), (
        f"{name} touches the database before restoring it"
    )


@pytest.mark.parametrize("name", WRITERS)
def test_writers_save_after_their_last_write_and_may_replace_the_asset(name):
    spec = _load(name)
    job = _job(spec)
    saves = _uses(job, SAVE)
    assert saves, f"{name} writes to the database and never saves it"
    last_write = max(
        i for i, s in enumerate(job["steps"])
        if "app.cli" in (s.get("run") or "") and "check-sources" not in s["run"]
    )
    assert max(saves) > last_write, f"{name} saves before its last write"
    assert spec.get("permissions", {}).get("contents") == "write", (
        f"{name} needs contents: write to replace the release asset"
    )
    assert spec["concurrency"]["group"] == "jerry-data", (
        "every writer serialises on the same group, or two restore-write-save "
        "cycles overlap and one of them is lost"
    )


@pytest.mark.parametrize("name", READERS)
def test_readers_never_save(name):
    job = _job(_load(name))
    assert not _uses(job, SAVE), f"{name} is read-only and must not replace the dump"


def test_the_seed_starts_from_nothing_and_saves_history_before_training():
    job = _job(_load("seed.yml"))
    restore = job["steps"][_uses(job, RESTORE)[0]]
    assert str(restore.get("with", {}).get("required")) == "false", (
        "the first seed has no dump to restore; it must be allowed to start empty"
    )
    saves = _uses(job, SAVE)
    train = next(i for i, s in enumerate(job["steps"]) if "app.cli train" in (s.get("run") or ""))
    assert min(saves) < train, (
        "history is saved before training so a training failure does not cost the ingest"
    )


def test_the_publish_builds_after_every_writer_that_changes_the_slate():
    spec = _load("pages.yml")
    triggers = spec[True]["workflow_run"]["workflows"]  # `on` parses as True
    for name in ("Daily refresh", "Pregame refresh", "Seed the database"):
        assert name in triggers


def test_the_composite_actions_exist_and_use_the_same_release():
    restore = yaml.safe_load((ROOT / "actions/db-restore/action.yml").read_text(encoding="utf-8"))
    save = yaml.safe_load((ROOT / "actions/db-save/action.yml").read_text(encoding="utf-8"))
    assert restore["inputs"]["tag"]["default"] == save["inputs"]["tag"]["default"] == "data"
    assert restore["inputs"]["asset"]["default"] == save["inputs"]["asset"]["default"]
    for action in (restore, save):
        assert action["runs"]["using"] == "composite"
        script = action["runs"]["steps"][0]["run"]
        assert "set -euo pipefail" in script
        assert "--no-owner" in script
    # The restore must be able to say "nothing there yet" without failing,
    # or the seeder can never run for the first time.
    assert "restored=false" in restore["runs"]["steps"][0]["run"]


def test_the_publish_stamps_the_build_and_uptime_reads_the_stamp():
    """The failure this guards is silence: a static site that stopped updating.

    Every refresh failed for three days and the site kept serving its last
    build. pages.yml now writes build.json and uptime.yml fails when that stamp
    is older than a daily cycle, so the next freeze is a red workflow within a
    day rather than a discovery.
    """
    pages = (WORKFLOWS / "pages.yml").read_text(encoding="utf-8")
    assert "out/build.json" in pages and "built_at" in pages
    uptime = _load("uptime.yml")
    job = next(iter(uptime["jobs"].values()))
    script = "\n".join(s.get("run") or "" for s in job["steps"])
    assert "build.json" in script and "built_at" in script
    assert "MAX_AGE_HOURS" in script
    assert "onrender.com" not in (WORKFLOWS / "uptime.yml").read_text(encoding="utf-8"), (
        "Render is not in the read path; the uptime check watches the Pages site"
    )


def test_the_save_never_deletes_the_primary_dump_before_its_replacement_is_up():
    """`gh release upload --clobber` deletes the existing asset before it
    uploads, and a failed upload then loses it — for the primary dump that
    would take every restore down until someone repaired the release by hand.
    So the new dump goes up under a staging name and the names are swapped
    afterwards; the only `--clobber` is on the staging asset."""
    script = yaml.safe_load(
        (ROOT / "actions/db-save/action.yml").read_text(encoding="utf-8")
    )["runs"]["steps"][0]["run"]
    clobbers = [
        line for line in script.splitlines()
        if "--clobber" in line and not line.strip().startswith("#")
    ]
    assert clobbers, "the staging upload must clobber a stale staging asset"
    for line in clobbers:
        assert "$staging" in line, f"--clobber on something other than the staging asset: {line}"
    # The swap is two metadata edits on the assets, not a delete-and-reupload.
    assert 'name="$previous"' in script and 'name="$ASSET"' in script
    assert "releases/assets/" in script
    # And the upload is retried before anything is given up on.
    assert "for attempt in 1 2 3" in script
