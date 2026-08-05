from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shopping_bot.data_fetchers.dynamic_search_filters import parse_dynamic_filters_from_aggs
from search_v2.retrieval.filters import SearchFilters, build_filter_clauses


def test_parse_dynamic_filters_from_aggs_includes_flavour_group():
    aggregations = {
        "flavour_options": {
            "buckets": [
                {"key": "chocolate", "doc_count": 4},
                {"key": "vanilla", "doc_count": 2},
            ]
        }
    }

    groups = parse_dynamic_filters_from_aggs(aggregations)
    flavour_group = next((group for group in groups if group.get("id") == "filter_flavour"), None)
    assert flavour_group is not None
    assert flavour_group["titleKey"] == "flavour"
    assert flavour_group["items"] == [
        {
            "id": "flavour_chocolate",
            "labelKey": "chocolate",
            "label": "Chocolate",
            "value": "chocolate",
            "count": 4,
            "isPreSelected": False,
        },
        {
            "id": "flavour_vanilla",
            "labelKey": "vanilla",
            "label": "Vanilla",
            "value": "vanilla",
            "count": 2,
            "isPreSelected": False,
        },
    ]


def test_build_filter_clauses_applies_flavour_filters():
    sf = SearchFilters.from_dict({"flavour": ["chocolate", "vanilla"]})
    clauses = build_filter_clauses(sf).filter_clauses
    flavour_clauses = [
        clause
        for clause in clauses
        if isinstance(clause, dict)
        and isinstance(clause.get("bool"), dict)
        and isinstance(clause["bool"].get("should"), list)
        and any("flavour" in str(item) for item in clause["bool"]["should"])
    ]
    assert len(flavour_clauses) == 2
    first_should = flavour_clauses[0]["bool"]["should"]
    assert {
        "term": {
            "flavour.keyword": {
                "value": "chocolate",
                "case_insensitive": True,
            }
        }
    } in first_should
    assert {"match_phrase": {"flavour": {"query": "chocolate"}}} in first_should


def test_build_filter_clauses_normalizes_flavour_slug_underscores():
    sf = SearchFilters.from_dict({"flavour": ["cafe_mocha"]})
    clauses = build_filter_clauses(sf).filter_clauses
    flavour_clauses = [
        clause
        for clause in clauses
        if isinstance(clause, dict)
        and "flavour" in str(clause)
    ]
    assert len(flavour_clauses) == 1
    should = flavour_clauses[0]["bool"]["should"]
    assert {
        "term": {
            "flavour.keyword": {
                "value": "cafe mocha",
                "case_insensitive": True,
            }
        }
    } in should
    assert {"match_phrase": {"flavour": {"query": "cafe mocha"}}} in should
