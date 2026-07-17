from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from search_v2.retrieval.filters import SearchFilters, build_filter_clauses


def test_dietary_filter_matches_dietary_tags_only():
    sf = SearchFilters(dietary_labels=["pcos_friendly", "GLUTEN FREE"])
    clauses = build_filter_clauses(sf).filter_clauses

    dietary_clauses = [
        clause for clause in clauses if isinstance(clause, dict) and "bool" in clause and "should" in clause.get("bool", {})
    ]
    assert len(dietary_clauses) >= 2

    clause_text = str(dietary_clauses)
    assert "category_data.tags.dietary_tags" in clause_text
    assert "pcos_friendly" in clause_text
    assert "gluten_free" in clause_text


def test_flean_score_filter_uses_badge_threshold_script():
    sf = SearchFilters(min_flean_score=8.0)
    clauses = build_filter_clauses(sf).filter_clauses

    script_clauses = [c for c in clauses if isinstance(c, dict) and "script" in c]
    assert script_clauses, "Expected script clause for min_flean_score filter"

    params = script_clauses[0]["script"]["script"]["params"]
    source = script_clauses[0]["script"]["script"]["source"]
    assert params["min_badge"] == 8.0
    assert "flean_score.adjusted_score_label" in source
