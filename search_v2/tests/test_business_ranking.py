from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from search_v2.ranking.business_ranking import apply_business_ranking, flean_nutrition_rule


@dataclass
class _Item:
    doc_id: str
    source: dict
    fused_score: float


def _source_with_leaf(leaf: str) -> dict:
    return {
        "category_hierarchies": [{"segments": ["f_and_b", "supplements", "protein", leaf]}],
        "stats": {
            "adjusted_score_percentiles": {"subcategory_percentile": 60},
            "protein_percentiles": {"subcategory_percentile": 50},
        },
    }


def test_business_ranking_uses_item_leaf_when_subcategory_not_provided():
    whey_item = _Item("whey", _source_with_leaf("whey_blend"), 1.0)
    generic_item = _Item("generic", _source_with_leaf("unknown_leaf"), 1.0)
    ranked = apply_business_ranking(
        [whey_item, generic_item],
        subcategory="_default",
        rules=[flean_nutrition_rule],
        resort=False,
    )
    assert ranked[0].business_multiplier > ranked[1].business_multiplier


def test_business_ranking_still_respects_explicit_subcategory():
    whey_item = _Item("whey", _source_with_leaf("whey_blend"), 1.0)
    generic_item = _Item("generic", _source_with_leaf("unknown_leaf"), 1.0)
    ranked = apply_business_ranking(
        [whey_item, generic_item],
        subcategory="energy_bars",
        rules=[flean_nutrition_rule],
        resort=False,
    )
    assert ranked[0].business_multiplier == ranked[1].business_multiplier
