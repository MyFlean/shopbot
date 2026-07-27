"""Tests for search_v2/retrieval/listing.py"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from search_v2.ranking.business_ranking import RankedItem, finalize_search_ranking

from search_v2.retrieval.listing import (
    LISTING_COLLAPSE,
    apply_flat_listing_defaults,
    apply_general_retrieval_rules,
    demote_below_flean_threshold,
    finalize_listing_cards,
    flean_score_on_10_scale,
    listing_visibility_filter_clause,
    pin_lab_tested_to_top,
)


def test_apply_flat_listing_defaults_adds_collapse_and_source_excludes():
    body = {"size": 20, "query": {"match_all": {}}}
    out = apply_flat_listing_defaults(body)
    assert out["collapse"] == LISTING_COLLAPSE
    assert out["_source"]["excludes"] == [
        "text_vector",
        "text_vector_source",
        "vernacular_synonyms",
    ]


def test_apply_flat_listing_defaults_preserves_existing_source_includes():
    body = {
        "size": 10,
        "query": {"match_all": {}},
        "_source": {"includes": ["id", "name"]},
    }
    out = apply_flat_listing_defaults(body)
    assert out["_source"]["includes"] == ["id", "name"]
    assert "text_vector" in out["_source"]["excludes"]


def test_listing_visibility_filter_clause():
    clause = listing_visibility_filter_clause()
    assert clause == {"terms": {"visibility": ["visible", "soft"]}}


def test_pin_lab_tested_to_top_preserves_relative_order():
    cards = [
        {"id": "a", "has_lab_report": False},
        {"id": "b", "has_lab_report": False},
        {"id": "lab", "has_lab_report": True},
        {"id": "c", "has_lab_report": False},
    ]
    out = pin_lab_tested_to_top(cards)
    assert [c["id"] for c in out] == ["lab", "a", "b", "c"]


def test_pin_lab_tested_to_top_noop_when_no_lab():
    cards = [{"id": "a", "has_lab_report": False}, {"id": "b", "has_lab_report": False}]
    assert pin_lab_tested_to_top(cards) is cards


def test_flean_score_on_10_scale_normalizes_0_100():
    assert flean_score_on_10_scale(85.0) == 8.5
    assert flean_score_on_10_scale(6.0) == 6.0


def test_apply_general_retrieval_rules_preserves_order_within_tiers():
    cards = [
        {"id": "high_a", "flean_score": 90.0},
        {"id": "low_a", "flean_score": 50.0},
        {"id": "high_b", "flean_score": 70.0},
        {"id": "low_b", "flean_score": 30.0},
    ]
    out = apply_general_retrieval_rules(cards)
    assert [c["id"] for c in out] == ["high_a", "high_b", "low_a", "low_b"]


def test_apply_general_retrieval_rules_noop_when_all_below():
    cards = [{"id": "a", "flean_score": 50.0}, {"id": "b", "flean_score": 40.0}]
    assert apply_general_retrieval_rules(cards) is cards


def test_demote_below_flean_threshold_is_alias():
    cards = [{"id": "high", "flean_score": 80.0}, {"id": "low", "flean_score": 50.0}]
    assert demote_below_flean_threshold(cards) == apply_general_retrieval_rules(cards)


def test_finalize_listing_cards_applies_flean_then_lab():
    cards = [
        {"id": "low", "flean_score": 50.0, "has_lab_report": False},
        {"id": "lab_low", "flean_score": 50.0, "has_lab_report": True},
        {"id": "high", "flean_score": 80.0, "has_lab_report": False},
    ]
    out = finalize_listing_cards(cards)
    assert [c["id"] for c in out] == ["lab_low", "high", "low"]


def test_finalize_search_ranking_uses_same_flean_tier_rule():
    def _item(doc_id: str, score: float) -> RankedItem:
        return RankedItem(
            doc_id=doc_id,
            source={"id": doc_id, "flean_score": {"adjusted_score": score}},
            relevance_score=1.0,
            business_multiplier=1.0,
            final_score=1.0,
        )

    ranked = [_item("low", 50.0), _item("high", 85.0), _item("mid_low", 55.0)]
    out = finalize_search_ranking(ranked, promote_lab=False)
    assert [item.doc_id for item in out] == ["high", "low", "mid_low"]


def _run_all():
    fns = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS: {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL: {fn.__name__}: {exc}")
    print(f"\n{len(fns) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    _run_all()
