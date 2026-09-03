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


def test_dispersion_is_fitted_once_per_slate_at_its_earliest_moment(monkeypatch):
    """Two games on one card share one fit, taken before the earlier first pitch."""
    import app.modeling.serving as serving

    calls: list[datetime] = []

    def fake_dispersion_asof(store, as_of, **kwargs):
        calls.append(as_of)
        return SimpleNamespace(size=3.0 + len(calls))

    monkeypatch.setattr(serving, "dispersion_asof", fake_dispersion_asof)
    frame = pd.DataFrame(
        {
            "game_id": [1, 2, 3],
            "official_date": ["2025-06-01", "2025-06-01", "2025-06-02"],
            "as_of": [
                pd.Timestamp("2025-06-01 20:00", tz="UTC"),
                pd.Timestamp("2025-06-01 16:00", tz="UTC"),
                pd.Timestamp("2025-06-02 22:00", tz="UTC"),
            ],
        }
    )
    sizes = simulation._asof_sizes(object(), frame, None)
    assert len(calls) == 2
    assert calls[0] == datetime(2025, 6, 1, 16, 0, tzinfo=UTC)  # the card's earliest, not each game's
    assert sizes[1] == sizes[2] == 4.0
    assert sizes[3] == 5.0


def test_a_slate_without_a_fit_is_served_logistic_only(monkeypatch):
    import app.modeling.serving as serving

    monkeypatch.setattr(serving, "dispersion_asof", lambda store, as_of, **k: None)
    frame = pd.DataFrame(
        {"game_id": [1, 2], "official_date": ["2025-04-01"] * 2,
         "as_of": [pd.Timestamp("2025-04-01 20:00", tz="UTC")] * 2}
    )
    assert simulation._asof_sizes(object(), frame, None) == {1: None, 2: None}
    # A measurement may still keep such a slate on the training-side value.
    assert simulation._asof_sizes(object(), frame, 3.5) == {1: 3.5, 2: 3.5}


def test_simulate_slate_declines_a_game_whose_slate_has_no_fit(store, monkeypatch):
    _stub(monkeypatch, USABLE_BASE, USABLE_PROJECTION)
    game = store.games.iloc[20]
    frame = pd.DataFrame(
        {"game_id": [int(game["id"])], "as_of": [pd.Timestamp(game["game_date_utc"]) - pd.Timedelta(hours=3)]}
    )
    sims = simulate_slate(
        store, object(), frame, 3.5, 500, models=(BASE, PROJECTED),
        size_by_game={int(game["id"]): None},
    )
    row = sims.iloc[0]
    assert row["sim_prob"] is None or row["sim_prob"] != row["sim_prob"]
    assert bool(row["dispersion_unavailable"]) is True


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
