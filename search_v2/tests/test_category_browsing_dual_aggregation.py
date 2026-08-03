from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from search_v2.retrieval.filters import SearchFilters

browse_module = importlib.import_module("search_v2.extension.category_browsing.browse")


def test_category_browse_subcategory_list_uses_relaxed_dual_aggregation(monkeypatch):
    requests: list[dict] = []

    class _DummyClient:
        def search(self, body):
            requests.append(body)
            call_idx = len(requests)
            if call_idx == 1:
                return {
                    "hits": {
                        "hits": [{"_source": {"id": "prod-1"}, "_score": 1.0}],
                        "total": {"value": 1},
                    }
                }
            if call_idx == 2:
                return {
                    "aggregations": {
                        "price_min": {"value": 10.0},
                        "price_max": {"value": 100.0},
                    }
                }
            if call_idx == 3:
                return {
                    "aggregations": {
                        "category_hierarchy_nested": {
                            "category_scope": {
                                "subcategory_candidates": {
                                    "segment3_values": {
                                        "value": ["bread_and_buns"],
                                    }
                                }
                            }
                        },
                        "subcategory_scope_global": {
                            "doc_count": 999,
                            "subcategory_scope_filter": {
                                "doc_count": 555,
                                "category_hierarchy_nested": {
                                    "category_scope": {
                                        "subcategory_candidates": {
                                            "segment3_values": {
                                                "value": [
                                                    "bread_and_buns",
                                                    "milk",
                                                    "eggs",
                                                ]
                                            }
                                        }
                                    }
                                },
                            },
                        },
                    }
                }
            raise AssertionError(f"Unexpected OpenSearch search call: {call_idx}")

    monkeypatch.setattr(browse_module, "_client", _DummyClient())
    monkeypatch.setattr(
        browse_module,
        "to_product_card",
        lambda source, rank, score: {"id": source.get("id"), "rank": rank, "_score": score},
    )
    monkeypatch.setattr(browse_module, "finalize_listing_cards", lambda products: products)
    monkeypatch.setattr(
        browse_module,
        "parse_dynamic_filters_from_aggs",
        lambda _aggs: [{"id": "filter_price"}],
    )
    monkeypatch.setattr(
        browse_module,
        "sync_subcategory_metadata_with_app_config",
        lambda category, es_subcategory_ids: [
            {"id": subcategory_id, "image": "", "name": subcategory_id}
            for subcategory_id in es_subcategory_ids
        ],
    )

    result = browse_module.browse_by_category_segment(
        category_segment_l2="dairy_and_bakery",
        page=0,
        size=20,
        sort_by="relevance",
        filters=SearchFilters(subcategory_segment_l3="bread_and_buns"),
    )

    assert len(requests) == 3
    assert [entry["id"] for entry in result["subcategories"]] == [
        "bread_and_buns",
        "milk",
        "eggs",
    ]
    assert result["filters"] == [{"id": "filter_price"}]

    products_query = json.dumps(requests[0].get("query", {}), sort_keys=True)
    assert "bread_and_buns" in products_query

    facets_request = requests[2]
    assert "subcategory_scope_global" in facets_request.get("aggs", {})

    scoped_agg_payload = json.dumps(
        facets_request["aggs"]["subcategory_scope_global"],
        sort_keys=True,
    )
    assert "bread_and_buns" not in scoped_agg_payload


def test_department_browse_categories_list_uses_relaxed_dual_aggregation(monkeypatch):
    requests: list[dict] = []

    class _DummyClient:
        def search(self, body):
            requests.append(body)
            call_idx = len(requests)
            if call_idx == 1:
                return {
                    "hits": {
                        "hits": [{"_source": {"id": "prod-1"}, "_score": 1.0}],
                        "total": {"value": 1},
                    }
                }
            if call_idx == 2:
                return {
                    "aggregations": {
                        "price_min": {"value": 10.0},
                        "price_max": {"value": 100.0},
                    }
                }
            if call_idx == 3:
                return {
                    "aggregations": {
                        "department_hierarchy_nested": {
                            "department_scope": {
                                "category_candidates": {
                                    "segment2_values": {
                                        "value": ["biscuits_and_crackers"],
                                    }
                                }
                            }
                        },
                        "category_scope_global": {
                            "doc_count": 999,
                            "category_scope_filter": {
                                "doc_count": 555,
                                "department_hierarchy_nested": {
                                    "department_scope": {
                                        "category_candidates": {
                                            "segment2_values": {
                                                "value": [
                                                    "biscuits_and_crackers",
                                                    "light_bites",
                                                    "dairy_and_bakery",
                                                ]
                                            }
                                        }
                                    }
                                },
                            },
                        },
                    }
                }
            raise AssertionError(f"Unexpected OpenSearch search call: {call_idx}")

    monkeypatch.setattr(browse_module, "_client", _DummyClient())
    monkeypatch.setattr(
        browse_module,
        "to_product_card",
        lambda source, rank, score: {"id": source.get("id"), "rank": rank, "_score": score},
    )
    monkeypatch.setattr(browse_module, "finalize_listing_cards", lambda products: products)
    monkeypatch.setattr(
        browse_module,
        "parse_dynamic_filters_from_aggs",
        lambda _aggs: [{"id": "filter_price"}],
    )
    monkeypatch.setattr(
        browse_module,
        "sync_category_metadata_with_app_config",
        lambda department, es_category_ids: [
            {"id": category_id, "image": "", "name": category_id}
            for category_id in es_category_ids
        ],
    )

    result = browse_module.browse_by_department_segment(
        department_segment_l1="food",
        page=0,
        size=20,
        sort_by="relevance",
        filters=SearchFilters(
            category_segment_l2="biscuits_and_crackers",
            subcategory_segment_l3="cookies",
        ),
    )

    assert len(requests) == 3
    assert [entry["id"] for entry in result["categories"]] == [
        "biscuits_and_crackers",
        "light_bites",
        "dairy_and_bakery",
    ]
    assert result["filters"] == [{"id": "filter_price"}]
    assert result["meta"]["department_segment_l1"] == "food"

    products_query = json.dumps(requests[0].get("query", {}), sort_keys=True)
    assert "food" in products_query
    assert "biscuits_and_crackers" in products_query
    assert "cookies" in products_query

    facets_request = requests[2]
    assert "category_scope_global" in facets_request.get("aggs", {})
    scoped_agg_payload = json.dumps(
        facets_request["aggs"]["category_scope_global"],
        sort_keys=True,
    )
    assert "biscuits_and_crackers" not in scoped_agg_payload
    assert "cookies" not in scoped_agg_payload
