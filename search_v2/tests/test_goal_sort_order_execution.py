from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from search_v2.retrieval.hybrid_search_orchestrator import (
    ResultItem,
    _apply_post_fusion_sort_order,
)
from search_v2.retrieval.sorting import build_sort_clauses_from_order


def test_build_sort_clauses_from_order_handles_derived_and_score_tie_breaker():
    clauses = build_sort_clauses_from_order(
        [
            {"field": "derived.protein_cal_pct", "order": "desc"},
            {"field": "category_data.nutritional.nutri_breakdown.protein g", "order": "desc"},
        ]
    )
    assert clauses is not None
    assert "_script" in clauses[0]
    assert "category_data.nutritional.nutri_breakdown.protein g" in clauses[1]
    assert clauses[-1] == {"_score": "desc"}


def test_apply_post_fusion_sort_order_applies_multi_key_and_derived_fields():
    items = [
        ResultItem(
            doc_id="a",
            fused_score=0.8,
            source={
                "category_data": {
                    "nutritional": {
                        "nutri_breakdown": {
                            "energy kcal": 120,
                            "protein g": 12,
                            "carbohydrate g": 10,
                            "fiber g": 2,
                        }
                    }
                }
            },
        ),
        ResultItem(
            doc_id="b",
            fused_score=0.9,
            source={
                "category_data": {
                    "nutritional": {
                        "nutri_breakdown": {
                            "energy kcal": 100,
                            "protein g": 8,
                            "carbohydrate g": 5,
                            "fiber g": 2,
                        }
                    }
                }
            },
        ),
        ResultItem(
            doc_id="c",
            fused_score=0.7,
            source={
                "category_data": {
                    "nutritional": {
                        "nutri_breakdown": {
                            "energy kcal": 120,
                            "protein g": 12,
                            "carbohydrate g": 7,
                            "fiber g": 5,
                        }
                    }
                }
            },
        ),
    ]

    sorted_items = _apply_post_fusion_sort_order(
        items,
        [
            {"field": "derived.protein_cal_pct", "order": "desc"},
            {"field": "derived.net_carbs", "order": "asc"},
        ],
    )

    assert [item.doc_id for item in sorted_items] == ["c", "a", "b"]
