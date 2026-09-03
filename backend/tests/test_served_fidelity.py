"""The measured run model must be simulable exactly where serving simulates.

`serve_probability` tries the projected means first and the season-to-date
means second. The measured path used to require the season-to-date means
before it would consider anything, so a game the projection covered but the
base gate rejected was dropped from the measurement and served in the product.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pandas as pd
import pytest

import app.modeling.simulation as simulation
from app.modeling.run_inputs import BASE, PROJECTED, SERVED, RunComponents
from app.modeling.simulation import RunMeans, run_components, simulate_slate


def _ctx(game_id: int = 5) -> SimpleNamespace:
    return SimpleNamespace(
        game_id=game_id, season=2025, home_team_id=1, away_team_id=2, venue_id=1,
        home_starter_id=None, away_starter_id=None,
    )


AS_OF = datetime(2025, 4, 2, 20, 0, tzinfo=UTC)
USABLE_BASE = RunMeans(home=4.4, away=4.1, league=4.3, home_games=40, away_games=40)
THIN_BASE = RunMeans(home=4.4, away=4.1, league=4.3, home_games=3, away_games=2)
USABLE_PROJECTION = RunMeans(home=4.9, away=3.9, league=4.3, home_games=300, away_games=300)
THIN_PROJECTION = RunMeans(home=4.9, away=3.9, league=4.3, home_games=4, away_games=1)


def _stub(monkeypatch, base, projection):
    monkeypatch.setattr(simulation, "expected_runs", lambda *a, **k: base)
    monkeypatch.setattr(simulation, "projected_runs", lambda *a, **k: projection)
    monkeypatch.setattr(simulation, "pitching_split", lambda *a, **k: simulation.RunComponents.__dataclass_fields__["home_pitching"].default)


def test_a_projection_alone_is_enough_to_simulate_the_served_model(monkeypatch):
    _stub(monkeypatch, THIN_BASE, USABLE_PROJECTION)
    components = run_components(object(), object(), _ctx(), AS_OF)
    assert components is not None
    assert not components.base_measured
    assert components.projected_measured
    assert components.means(SERVED) == (4.9, 3.9)
    assert components.means(PROJECTED) == (4.9, 3.9)
    assert components.means(BASE) is None  # the base variant has no means here


def test_a_thin_projection_falls_back_to_the_base_means_as_serving_does(monkeypatch):
    _stub(monkeypatch, USABLE_BASE, THIN_PROJECTION)
    components = run_components(object(), object(), _ctx(), AS_OF)
    assert components is not None
    assert components.base_measured
    assert not components.projected_measured
    assert components.means(SERVED) == (4.4, 4.1)


def test_neither_means_is_no_simulation(monkeypatch):
    _stub(monkeypatch, THIN_BASE, THIN_PROJECTION)
    assert run_components(object(), object(), _ctx(), AS_OF) is None
    _stub(monkeypatch, None, None)
    assert run_components(object(), object(), _ctx(), AS_OF) is None


def test_means_are_none_only_for_variants_without_them():
    components = RunComponents(
        home=None, away=None, league=4.3, home_games=0, away_games=0,
        home_projected=4.9, away_projected=3.9,
    )
    assert components.means(BASE) is None
    assert components.means(PROJECTED) == (4.9, 3.9)
    both = RunComponents(home=4.4, away=4.1, league=4.3, home_games=40, away_games=40)
    assert both.means(PROJECTED) == (4.4, 4.1)  # falls back to the base means
    assert both.means(BASE) == (4.4, 4.1)


def test_simulate_slate_scores_the_served_variant_and_leaves_the_base_null(store, monkeypatch):
    """An opening-fortnight game: served through the projection, base absent."""
    _stub(monkeypatch, THIN_BASE, USABLE_PROJECTION)
    game = store.games.iloc[20]
    frame = pd.DataFrame(
        {"game_id": [int(game["id"])], "as_of": [pd.Timestamp(game["game_date_utc"]) - pd.Timedelta(hours=3)]}
    )
    sims = simulate_slate(store, object(), frame, 3.5, 500, models=(BASE, PROJECTED))
    row = sims.iloc[0]
    assert row["sim_base"] is None or row["sim_base"] != row["sim_base"]
    assert 0.0 < float(row["sim_projected"]) < 1.0
    assert row["sim_prob"] == pytest.approx(float(row["sim_projected"]))
    assert bool(row["projected_measured"]) is True
    assert bool(row["base_measured"]) is False
