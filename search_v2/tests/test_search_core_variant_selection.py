from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from search_v2.extension.search.core import _preferred_listing_source


def test_preferred_listing_source_uses_first_available_variant_from_inner_hits():
    source = {
        "id": "top-1",
        "name": "Top Product",
        "variants": [
            {"id": "sib-a", "availability": False},
            {"id": "sib-b", "availability": True},
            {"id": "sib-c", "availability": True},
        ],
        "_inner_hits": {
            "family_siblings": {
                "hits": {
                    "hits": [
                        {"_id": "sib-a", "_source": {"id": "sib-a", "name": "Sibling A", "price": 100}},
                        {"_id": "sib-b", "_source": {"id": "sib-b", "name": "Sibling B", "price": 120}},
                        {"_id": "sib-c", "_source": {"id": "sib-c", "name": "Sibling C", "price": 140}},
                    ]
                }
            }
        },
    }

    selected = _preferred_listing_source(source)
    assert selected["id"] == "sib-b"
    assert selected["name"] == "Sibling B"
    assert selected["price"] == 120


def test_preferred_listing_source_falls_back_when_no_available_variant():
    source = {
        "id": "top-1",
        "name": "Top Product",
        "variants": [
            {"id": "sib-a", "availability": False},
            {"id": "sib-b", "availability": False},
        ],
        "_inner_hits": {
            "family_siblings": {"hits": {"hits": [{"_id": "sib-a", "_source": {"id": "sib-a", "name": "Sibling A"}}]}}
        },
    }

    selected = _preferred_listing_source(source)
    assert selected["id"] == "top-1"
    assert selected["name"] == "Top Product"
    assert "_inner_hits" not in selected


def test_preferred_listing_source_falls_back_when_selected_sibling_missing():
    source = {
        "id": "top-1",
        "name": "Top Product",
        "variants": [
            {"id": "sib-z", "availability": True},
        ],
        "_inner_hits": {
            "family_siblings": {"hits": {"hits": []}}
        },
    }

    selected = _preferred_listing_source(source)
    assert selected["id"] == "top-1"
    assert selected["name"] == "Top Product"
    assert "_inner_hits" not in selected
