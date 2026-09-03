"""Two review findings on the projection group, pinned.

1. A `--seasons` measurement used to load a store cut to exactly the seasons
   being scored, so the earliest of them had no prior seasons to project from
   and the candidate was measured on a store production never runs. The store
   now carries a lookback and the dataset builder cuts the scored rows back.
2. The run-model verdict text hard-coded "three refinements"; adding a fourth
   would have understated the multiplicity in every report.
"""

from __future__ import annotations

from app.backtest.feature_set_compare import PairedDelta
from app.features import projections as pj
from app.features.asof import LOOKBACK_SEASONS, seasons_to_load
from app.modeling.dataset import build_dataset
from app.modeling.run_model_compare import VariantResult, _judge_variants
from tests.conftest import SEASON


def test_the_lookback_covers_every_prior_season_a_projection_reads():
    assert len(pj.PITCHER_SEASON_WEIGHTS) <= LOOKBACK_SEASONS
    assert len(pj.TEAM_SEASON_WEIGHTS) <= LOOKBACK_SEASONS


def test_seasons_to_load_widens_backwards_only():
    assert seasons_to_load([2024, 2025]) == [2021, 2022, 2023, 2024, 2025]
    assert seasons_to_load([2025], lookback=1) == [2024, 2025]
    assert seasons_to_load(None) is None
    assert seasons_to_load([]) is None


def test_the_dataset_scores_only_the_requested_seasons(store):
    """The store may carry more history than the rows that are scored."""
    everything = build_dataset(None, store=store)
    assert len(everything.labelled) > 0
    same = build_dataset(None, seasons=[SEASON], store=store)
    assert len(same.labelled) == len(everything.labelled)
    assert set(same.labelled["season"]) == {SEASON}
    # Asking for a season the store has no games in scores nothing, while the
    # store itself is untouched — it is the lookback, not the target.
    earlier = build_dataset(None, seasons=[SEASON - 1], store=store)
    assert len(earlier.labelled) == 0
    assert len(store.games) > 0


def _variant(name: str, verdict: str) -> VariantResult:
    delta = None if verdict == "INCUMBENT" else PairedDelta(0.001, 0.0005, 0.0015)
    return VariantResult(name=name, metrics={}, vs_base=delta, blended={},
                         blend_vs_logistic=delta, verdict=verdict)


def test_the_verdict_counts_the_refinements_it_actually_judged():
    results = [
        _variant("base", "INCUMBENT"),
        _variant("park", "NO_EFFECT"),
        _variant("pitching", "NO_EFFECT"),
        _variant("park+pitching", "NO_EFFECT"),
        _variant("projected", "IMPROVES"),
    ]
    verdict, reading = _judge_variants(results, {})
    assert verdict == "IMPROVES"
    assert "4 refinements were tried" in reading
    assert "19%" in reading  # 1 - 0.95**4

    three = results[:4]
    three[1] = _variant("park", "IMPROVES")
    _, reading = _judge_variants(three, {})
    assert "3 refinements were tried" in reading
    assert "14%" in reading  # 1 - 0.95**3, the old one-in-seven
