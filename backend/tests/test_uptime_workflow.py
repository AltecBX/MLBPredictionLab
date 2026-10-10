"""The uptime watchdog clears a stuck publish before it calls an outage.

On 6 October a Publish run's deploy job entered "waiting" on the
github-pages environment and never left — a GitHub-side stall, with no
reviewer and no wait timer configured. Publish runs one at a time, so every
later build queued behind it and was replaced by the next; the site froze for
four days while refresh and pregame went green, and the watchdog emailed every
hour about a problem it could see and not touch.

These run the workflow's own step scripts in bash, with a fake `gh` that
records what it is asked to do, so the decisions — cancel this run, force that
one, dispatch a build, fail or pass — are executed rather than described.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "uptime.yml"

pytestmark = pytest.mark.skipif(
    not (shutil.which("bash") and shutil.which("jq")), reason="needs bash and jq"
)

FAKE_GH = """#!{python}
import json, os, re, sys
state_path, log_path = os.environ["FAKE_GH_STATE"], os.environ["FAKE_GH_LOG"]
state = json.load(open(state_path))
args = sys.argv[1:]
with open(log_path, "a") as log:
    log.write(" ".join(args) + "\\n")
if state.get("fail_listing") and args[:1] == ["api"] and "workflows/pages.yml/runs" in args[-1]:
    sys.exit(1)
if args[:1] == ["workflow"]:
    sys.exit(1 if state.get("fail_dispatch") else 0)
if args[:3] == ["api", "-X", "POST"]:
    m = re.search(r"runs/(\\d+)/(cancel|force-cancel)$", args[3])
    run_id, verb = int(m.group(1)), m.group(2)
    if verb == "force-cancel" or state.get("cancel_takes", True):
        for run in state["runs"]:
            if run["id"] == run_id:
                run["status"] = "completed"
    json.dump(state, open(state_path, "w"))
    print("{{}}")
    sys.exit(0)
path = args[-1]
if "workflows/pages.yml/runs" in path:
    print(json.dumps({{"workflow_runs": state["runs"]}}))
    sys.exit(0)
m = re.search(r"actions/runs/(\\d+)$", path)
if m:
    run = next(r for r in state["runs"] if r["id"] == int(m.group(1)))
    print(json.dumps({{"status": run["status"]}}))
    sys.exit(0)
sys.exit(2)
"""


@pytest.fixture(scope="module")
def steps() -> dict[str, dict]:
    spec = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = spec["jobs"]["check"]
    by_name = {s.get("id") or s["name"]: s for s in job["steps"]}
    for step in job["steps"]:
        # The scripts are run here exactly as written, so they must not carry
        # an expression GitHub would substitute first.
        assert "${{" not in (step.get("run") or "")
    return {"job_env": job["env"], **by_name}


def _ago(minutes: float) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


class Harness:
    def __init__(self, tmp_path: Path, steps: dict):
        self.tmp = tmp_path
        self.steps = steps
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(FAKE_GH.format(python=sys.executable))
        gh.chmod(0o755)
        self.state = tmp_path / "state.json"
        self.log = tmp_path / "gh.log"
        self.log.write_text("")
        self.output = tmp_path / "output"
        self.summary = tmp_path / "summary"
        self.site = tmp_path / "site"
        self.site.mkdir()
        self.path = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"

    def given(self, runs: list[dict], **flags) -> None:
        self.state.write_text(json.dumps({"runs": runs, **flags}))

    def stamp(self, hours_ago: float) -> str:
        built = (datetime.now(UTC) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
        (self.site / "build.json").write_text(json.dumps({"built_at": built}))
        return built

    def run(self, step: str, **env: str) -> subprocess.CompletedProcess:
        self.output.write_text("")
        full = {
            **{k: str(v) for k, v in self.steps["job_env"].items() if "${{" not in str(v)},
            "PATH": self.path,
            "HOME": str(self.tmp),
            "GITHUB_REPOSITORY": "AltecBX/MLBPredictionLab",
            "GITHUB_OUTPUT": str(self.output),
            "GITHUB_STEP_SUMMARY": str(self.summary),
            "FAKE_GH_STATE": str(self.state),
            "FAKE_GH_LOG": str(self.log),
            "SITE_URL": f"file://{self.site}",
            "PUBLISH_REF": "claude/jerry-mlb-prediction-lab-f22lok",
            "CANCEL_SETTLE_SECONDS": "0",
            **env,
        }
        return subprocess.run(
            ["bash", "-c", self.steps[step]["run"]], env=full, capture_output=True, text=True,
            timeout=120,
        )

    def outputs(self) -> dict[str, str]:
        return dict(line.split("=", 1) for line in self.output.read_text().splitlines() if "=" in line)

    def calls(self) -> list[str]:
        return [line for line in self.log.read_text().splitlines() if line]

    def cancelled(self) -> list[str]:
        return [c.split("/")[-2] for c in self.calls() if c.endswith("/cancel")]

    def forced(self) -> list[str]:
        return [c.split("/")[-2] for c in self.calls() if c.endswith("/force-cancel")]

    def dispatched(self) -> list[str]:
        return [c for c in self.calls() if c.startswith("workflow run pages.yml")]


@pytest.fixture
def h(tmp_path, steps) -> Harness:
    return Harness(tmp_path, steps)


# --- the heal --------------------------------------------------------------


def test_the_october_stall_is_cleared_and_the_queued_build_left_alone(h):
    """A deploy waiting for days, a fresh build queued behind it."""
    h.given([
        {"id": 37438346907, "status": "waiting", "created_at": _ago(4 * 24 * 60)},
        {"id": 38070609436, "status": "pending", "created_at": _ago(5)},
    ])
    result = h.run("heal", AGE_HOURS="102")
    assert result.returncode == 0, result.stderr
    assert h.cancelled() == ["37438346907"]
    assert h.forced() == []
    # The queued run is what should run next; nothing else is dispatched.
    assert h.dispatched() == []
    assert h.outputs()["acted"] == "true"
    assert "37438346907" in h.summary.read_text()


def test_a_build_queued_for_hours_behind_the_stall_is_never_cancelled(h):
    """Pending means queued behind the slot: it is what runs next, however long it waited."""
    h.given([
        {"id": 10, "status": "waiting", "created_at": _ago(6 * 60)},
        {"id": 11, "status": "pending", "created_at": _ago(3 * 60)},
    ])
    h.run("heal", AGE_HOURS="8")
    assert h.cancelled() == ["10"]


def test_a_deploy_waiting_its_usual_second_is_not_touched(h):
    h.given([{"id": 1, "status": "waiting", "created_at": _ago(2)}])
    result = h.run("heal", AGE_HOURS="3")
    assert result.returncode == 0, result.stderr
    assert h.cancelled() == [] and h.dispatched() == []
    assert h.outputs()["acted"] == "false"


def test_a_run_hung_past_an_hour_is_cancelled(h):
    h.given([{"id": 2, "status": "in_progress", "created_at": _ago(90)}])
    h.run("heal", AGE_HOURS="5")
    assert h.cancelled() == ["2"]
    # Nothing was left queued behind it, so a build is asked for.
    assert len(h.dispatched()) == 1


def test_a_cancel_that_does_not_take_is_forced(h):
    h.given([{"id": 3, "status": "waiting", "created_at": _ago(120)}], cancel_takes=False)
    h.run("heal", AGE_HOURS="5")
    assert h.cancelled() == ["3"]
    assert h.forced() == ["3"]


def test_a_stale_site_with_nothing_on_the_way_is_republished(h):
    h.given([])
    result = h.run("heal", AGE_HOURS="25")
    assert result.returncode == 0, result.stderr
    assert h.dispatched() == [
        "workflow run pages.yml --repo AltecBX/MLBPredictionLab "
        "--ref claude/jerry-mlb-prediction-lab-f22lok"
    ]
    assert h.outputs()["acted"] == "true"


def test_a_stale_site_with_a_build_already_running_waits_for_it(h):
    h.given([{"id": 4, "status": "in_progress", "created_at": _ago(3)}])
    h.run("heal", AGE_HOURS="25")
    assert h.dispatched() == [] and h.cancelled() == []


def test_a_site_inside_its_normal_cycle_is_left_alone(h):
    h.given([])
    h.run("heal", AGE_HOURS="12")
    assert h.calls() and h.dispatched() == []
    assert h.outputs()["acted"] == "false"


def test_a_missing_stamp_asks_for_a_build(h):
    h.given([])
    h.run("heal", AGE_HOURS="-1")
    assert len(h.dispatched()) == 1


def test_an_unreadable_runs_list_skips_the_heal_without_failing(h):
    h.given([], fail_listing=True)
    result = h.run("heal", AGE_HOURS="40")
    assert result.returncode == 0
    assert "Could not list Publish runs" in result.stdout
    assert h.outputs()["acted"] == "false"


def test_a_failed_dispatch_is_a_warning_not_the_verdict(h):
    h.given([], fail_dispatch=True)
    result = h.run("heal", AGE_HOURS="25")
    assert result.returncode == 0
    assert "Could not dispatch" in result.stdout
    assert h.outputs()["acted"] == "false"


# --- the stamp -------------------------------------------------------------


def test_the_stamp_is_recorded_not_judged(h):
    built = h.stamp(hours_ago=40)
    result = h.run("stamp")
    assert result.returncode == 0, result.stderr
    assert h.outputs() == {"built_at": built, "age_hours": "40"}


def test_a_missing_or_unreadable_stamp_is_minus_one(h):
    result = h.run("stamp")
    assert result.returncode == 0
    assert h.outputs()["age_hours"] == "-1"
    (h.site / "build.json").write_text("not json")
    result = h.run("stamp")
    assert result.returncode == 0
    assert h.outputs()["age_hours"] == "-1"


# --- the verdict -----------------------------------------------------------


def _judge(h: Harness, age: int, built: str, acted: bool, wait: int = 2):
    return h.run(
        "Judge freshness", AGE_HOURS=str(age), BUILT_AT=built, ACTED=str(acted).lower(),
        RECOVERY_WAIT_SECONDS=str(wait), POLL_SECONDS="1",
    )


def test_a_fresh_site_passes(h):
    built = h.stamp(hours_ago=4)
    assert _judge(h, 4, built, acted=False).returncode == 0


def test_a_stale_site_nobody_tried_to_fix_fails(h):
    built = h.stamp(hours_ago=40)
    result = _judge(h, 40, built, acted=False)
    assert result.returncode == 1
    assert "last built 40 hours ago" in result.stdout


def test_a_stale_site_that_recovers_after_the_heal_passes(h):
    """The email is for the outage that did not fix itself."""
    built = h.stamp(hours_ago=40)

    def publish_lands():
        time.sleep(1.5)
        h.stamp(hours_ago=0)

    threading.Thread(target=publish_lands).start()
    result = _judge(h, 40, built, acted=True, wait=30)
    assert result.returncode == 0, result.stdout
    assert "Recovered" in result.stdout


def test_a_stale_site_that_does_not_recover_still_fails(h):
    built = h.stamp(hours_ago=40)
    result = _judge(h, 40, built, acted=True, wait=2)
    assert result.returncode == 1
    assert "a republish did not bring it current" in result.stdout


def test_the_workflow_may_cancel_and_dispatch():
    spec = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert spec["permissions"]["actions"] == "write"
    job = spec["jobs"]["check"]
    # Long enough to wait out a build it asked for.
    assert job["timeout-minutes"] * 60 > int(job["env"]["RECOVERY_WAIT_SECONDS"]) + 300
