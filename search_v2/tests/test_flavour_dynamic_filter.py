from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shopping_bot.data_fetchers.dynamic_search_filters import (
    FLAVOUR_FILTER_FIELD,
    FLAVOUR_FILTER_FIELD_FALLBACK,
    parse_dynamic_filters_from_aggs,
)
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


def test_parse_dynamic_filters_falls_back_to_legacy_flavour_buckets():
    aggregations = {
        "flavour_options": {"buckets": []},
        "flavour_options_legacy": {
            "buckets": [{"key": "Belgian Chocolate", "doc_count": 6}]
        },
    }
    groups = parse_dynamic_filters_from_aggs(aggregations)
    flavour_group = next((group for group in groups if group.get("id") == "filter_flavour"), None)
    assert flavour_group is not None
    assert flavour_group["items"][0]["value"] == "belgian chocolate"


def test_build_filter_clauses_applies_flavour_filters():
    sf = SearchFilters.from_dict({"flavour": ["chocolate", "vanilla"]})
    clauses = build_filter_clauses(sf).filter_clauses
    flavour_clauses = [
        clause
        for clause in clauses
        if isinstance(clause, dict)
        and isinstance(clause.get("bool"), dict)
        and isinstance(clause["bool"].get("should"), list)
        and any(
            FLAVOUR_FILTER_FIELD in str(item) or FLAVOUR_FILTER_FIELD_FALLBACK in str(item)
            for item in clause["bool"]["should"]
        )
    ]
    assert len(flavour_clauses) == 1
    should = flavour_clauses[0]["bool"]["should"]
    assert flavour_clauses[0]["bool"]["minimum_should_match"] == 1
    assert {
        "bool": {
            "should": [
                {
                    "term": {
                        FLAVOUR_FILTER_FIELD: {
                            "value": "chocolate",
                            "case_insensitive": True,
                        }
                    }
                },
                {
                    "term": {
                        FLAVOUR_FILTER_FIELD_FALLBACK: {
                            "value": "chocolate",
                            "case_insensitive": True,
                        }
                    }
                },
            ],
            "minimum_should_match": 1,
        }
    } in should


def test_build_filter_clauses_normalizes_flavour_slug_underscores():
    sf = SearchFilters.from_dict({"flavour": ["cafe_mocha"]})
    clauses = build_filter_clauses(sf).filter_clauses
    flavour_clauses = [
        clause
        for clause in clauses
        if isinstance(clause, dict)
        and (
            FLAVOUR_FILTER_FIELD in str(clause)
            or FLAVOUR_FILTER_FIELD_FALLBACK in str(clause)
        )
    ]
    assert len(flavour_clauses) == 1
    should = flavour_clauses[0]["bool"]["should"]
    assert {
        "bool": {
            "should": [
                {
                    "term": {
                        FLAVOUR_FILTER_FIELD: {
                            "value": "cafe mocha",
                            "case_insensitive": True,
                        }
                    }
                },
                {
                    "term": {
                        FLAVOUR_FILTER_FIELD_FALLBACK: {
                            "value": "cafe mocha",
                            "case_insensitive": True,
                        }
                    }
                },
            ],
            "minimum_should_match": 1,
        }
    } in should
