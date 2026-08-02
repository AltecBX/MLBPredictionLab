"""A category that is being ingested must not report UNAVAILABLE.

The deployed Diagnostics screen listed lineups and weather as
"not configured — UNAVAILABLE" on the same page whose job table showed
`poll_lineups` writing 180 rows and `ingest_weather` writing 15 minutes earlier.

Two independent causes, and the first fix I attempted addressed neither, so both
are pinned here.

**The ingest never said it had run.** `data_source_status` rows are written by
`record_source_status`. The categories that report correctly -- schedule,
results, reference, player stats -- all reach it through
`apply_provider_result`. The three that build rows from a client directly rather
than through a `ProviderResult` never called it, so their rows kept whatever
`seed_source_status` wrote at bootstrap, which is UNAVAILABLE, for ever.
`refresh_freshness` does not rescue them: it recomputes the freshness *class*
from `last_success_at` and touches neither `status` nor `detail`.

**The renderer did not know the provider existed.** `freshness_report` resolves
a category's provider from `configured_categories()` at request time, in the
process serving the page. Setting those variables in a GitHub workflow cannot
affect a page rendered on Render, so they have to be in `render.yaml`.

UNAVAILABLE means "not measured". Claiming it for something measured is the same
lie as the reverse.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

BACKEND = Path(__file__).resolve().parents[1]
BLUEPRINT = BACKEND.parent / "render.yaml"

#: Ingest module -> the category its rows land under. Every one of these writes
#: real rows on a normal day, so every one must be able to say so.
INGESTS_THAT_MUST_REPORT = {
    "lineup_poller.py": "LINEUPS",
    "weather.py": "WEATHER",
    "injuries.py": "INJURIES",
}


@pytest.mark.parametrize(("module", "category"), sorted(INGESTS_THAT_MUST_REPORT.items()))
def test_every_ingesting_module_records_a_source_result(module: str, category: str):
    source = (BACKEND / "app" / "ingestion" / module).read_text(encoding="utf-8")
    tree = ast.parse(source)

    called = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"record_source_status", "apply_provider_result"}
        for node in ast.walk(tree)
    )
    assert called, (
        f"{module} writes rows but never records a source result, so its "
        f"Diagnostics row keeps the UNAVAILABLE that bootstrap wrote and the "
        f"site reports a working feed as missing."
    )
    assert f"DataCategory.{category}" in source, (
        f"{module} must record against DataCategory.{category}."
    )


def test_the_api_service_names_the_providers_it_actually_uses():
    """`freshness_report` reads these in the process that renders the page.

    Setting them in a workflow cannot help: the workflow is not what serves
    Diagnostics.
    """
    spec = yaml.safe_load(BLUEPRINT.read_text(encoding="utf-8"))
    api = next(s for s in spec["services"] if s["name"] == "jerry-api")
    env = {v["key"]: v for v in api["envVars"]}

    for key, expected in (
        ("LINEUP_PROVIDER", "mlb_statsapi"),
        ("INJURY_PROVIDER", "mlb_statsapi"),
        ("WEATHER_PROVIDER", "open_meteo"),
    ):
        assert env[key].get("value") == expected, (
            f"{key} must be set on jerry-api, or Diagnostics calls a category "
            f"that is ingested hourly 'not configured'."
        )


def test_categories_nothing_fetches_stay_unset():
    """The rule cuts both ways, and this is the half that keeps it honest.

    Nothing ingests Statcast, park factors or odds on the deployment, so those
    must keep reporting UNAVAILABLE and naming the variable that would enable
    them. Setting one to make a screen look complete is the failure this
    repository rules out first.
    """
    spec = yaml.safe_load(BLUEPRINT.read_text(encoding="utf-8"))
    api = next(s for s in spec["services"] if s["name"] == "jerry-api")
    env = {v["key"]: v for v in api["envVars"]}

    for key in ("STATCAST_PROVIDER", "PARK_FACTOR_PROVIDER", "ODDS_PROVIDER"):
        assert "value" not in env[key], (
            f"{key} has no ingest behind it on the deployment; giving it a "
            f"value would make the UI claim data that is not there."
        )
