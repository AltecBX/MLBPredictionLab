"""The Render blueprint must be internally consistent before it is applied.

Every deploy of this repository failed for forty minutes because
`dockerfilePath` pointed at a file that did not exist. Render resolves it
relative to `rootDir` when `rootDir` is set — the blueprint-spec page says
"relative to the repo root", which is true only when it is unset. Nothing in
the test suite could see that, because the file is only read by Render.

These checks are cheap and they close that gap.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
BLUEPRINT = REPO_ROOT / "render.yaml"

# From render.com/docs/blueprint-spec.
VALID_TYPES = {"web", "pserv", "worker", "cron", "keyvalue"}
# Render offers no free plan for these.
NO_FREE_PLAN = {"pserv", "worker", "cron", "keyvalue"}


@pytest.fixture(scope="module")
def spec() -> dict:
    if not BLUEPRINT.exists():
        pytest.skip("render.yaml is not present")
    return yaml.safe_load(BLUEPRINT.read_text())


def _resolve(svc: dict, key: str) -> Path | None:
    """Resolve a path the way Render does: under rootDir when one is set."""
    value = svc.get(key)
    if not value:
        return None
    root = svc.get("rootDir")
    return (REPO_ROOT / root / value) if root else (REPO_ROOT / value)


def test_every_dockerfile_the_blueprint_names_actually_exists(spec):
    missing = []
    for svc in spec.get("services", []):
        if svc.get("runtime") != "docker":
            continue
        resolved = _resolve(svc, "dockerfilePath")
        if resolved is None:
            missing.append(f"{svc['name']}: no dockerfilePath")
        elif not resolved.exists():
            missing.append(
                f"{svc['name']}: dockerfilePath={svc['dockerfilePath']!r} with "
                f"rootDir={svc.get('rootDir')!r} resolves to "
                f"{resolved.relative_to(REPO_ROOT)}, which does not exist"
            )
    assert missing == [], "\n".join(missing)


def test_docker_context_if_given_also_resolves(spec):
    missing = [
        f"{svc['name']}: dockerContext -> {_resolve(svc, 'dockerContext')}"
        for svc in spec.get("services", [])
        if svc.get("dockerContext") and not (_resolve(svc, "dockerContext") or Path("/x")).exists()
    ]
    assert missing == [], "\n".join(missing)


def test_service_types_and_plans_are_ones_render_accepts(spec):
    """A bad type or a free plan on a paid-only type fails the whole Apply."""
    problems = []
    for svc in spec.get("services", []):
        if svc["type"] not in VALID_TYPES:
            problems.append(f"{svc['name']}: type {svc['type']!r} is not valid")
        if svc.get("plan") == "free" and svc["type"] in NO_FREE_PLAN:
            problems.append(f"{svc['name']}: free is not offered for {svc['type']!r}")
    assert problems == [], "\n".join(problems)


def test_every_service_and_database_reference_resolves(spec):
    names = {s["name"] for s in spec.get("services", [])}
    databases = {d["name"] for d in spec.get("databases", [])}
    dangling = []
    for svc in spec.get("services", []):
        for var in svc.get("envVars", []):
            ref = var.get("fromService")
            if ref and ref["name"] not in names:
                dangling.append(f"{svc['name']}.{var['key']} -> service {ref['name']!r}")
            db = var.get("fromDatabase")
            if db and db["name"] not in databases:
                dangling.append(f"{svc['name']}.{var['key']} -> database {db['name']!r}")
    assert dangling == [], "\n".join(dangling)


def test_the_web_app_points_at_a_public_api_address(spec):
    """A free Render service cannot *receive* private network traffic.

    `hostport` is the private address and would be refused on every request;
    `host` is the public hostname.
    """
    web = next(s for s in spec["services"] if s["name"] == "jerry-web")
    api_ref = next(v for v in web["envVars"] if v["key"] == "API_BASE_URL")
    assert api_ref["fromService"]["property"] == "host", (
        "API_BASE_URL must use the API's public `host`; `hostport` is private "
        "and a free service cannot receive private network traffic."
    )
