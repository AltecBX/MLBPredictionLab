"""The projected run-model variant: uses the multi-season means when it has
them and reproduces the base model exactly when it does not."""

from __future__ import annotations

import pytest

from app.features.builder import FeatureBuilder
from app.modeling.run_inputs import BASE, PROJECTED, VARIANTS, RunComponents
from app.modeling.simulation import expected_runs, projected_runs, run_components


def _components(**kwargs):
    base = {"home": 4.6, "away": 4.2, "league": 4.5, "home_games": 80, "away_games": 80}
    return RunComponents(**(base | kwargs))


def test_projected_variant_is_in_the_ablation():
    assert PROJECTED in VARIANTS
    assert PROJECTED.projected is True and BASE.projected is False


def test_projected_means_replace_the_base_means_when_present():
    c = _components(home_projected=5.1, away_projected=3.9)
    assert c.projected_measured is True
    assert c.means(PROJECTED) == (5.1, 3.9)
    # The base model is untouched by the projection's presence.
    assert c.means(BASE) == (4.6, 4.2)


def test_without_a_projection_the_variant_is_exactly_the_base_model():
    c = _components()
    assert c.projected_measured is False
    assert c.means(PROJECTED) == c.means(BASE)


def test_projected_runs_come_from_the_store_and_agree_on_the_league(store, target_game):
    builder = FeatureBuilder(store)
    as_of = target_game.as_of()
    base = expected_runs(store, builder, target_game, as_of)
    projected = projected_runs(builder, target_game, as_of)
    assert base is not None and projected is not None
    assert projected.league == pytest.approx(base.league)
    assert projected.home > 0 and projected.away > 0
    components = run_components(store, builder, target_game, as_of)
    assert components is not None
    assert components.projected_measured is True
    assert components.means(PROJECTED) == pytest.approx((projected.home, projected.away))
