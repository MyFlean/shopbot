from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from search_v2.query_processing.query_pipeline import ProcessedQuery, QueryVariant
from search_v2.retrieval.lexical_query_builder import (
    build_query, _field_match_clauses, _core_text,
    _minimum_should_match_for, _bool_prefix_minimum_should_match_for,
    build_derivative_demotion_negative_query,
)
from search_v2.config.settings import SETTINGS


def _pq(text: str) -> ProcessedQuery:
    return ProcessedQuery(
        raw_query=text,
        normalized_query=text,
        variants=[QueryVariant(text=text, is_correction=False, confidence=1.0)],
    )


def _multi_match(clauses, predicate):
    return next(c["multi_match"] for c in clauses if "multi_match" in c and predicate(c["multi_match"]))


def test_core_text_removes_matched_phrase_words():
    assert _core_text("heart healthy foods", ("heart healthy",)) == "foods"


def test_core_text_empty_when_query_is_entirely_descriptor():
    assert _core_text("heart healthy", ("heart healthy",)) == ""


def test_core_text_no_op_without_matched_phrases():
    assert _core_text("heart healthy foods", ()) == "heart healthy foods"


def test_core_text_preserves_pre_workout_head_term():
    assert _core_text("pre workout", ("pre workout",)) == "pre workout"


def test_field_match_clauses_unchanged_without_health_intent():
    with_empty = _field_match_clauses("heart healthy foods", SETTINGS, health_intent_matched_phrases=())
    baseline = _field_match_clauses("heart healthy foods", SETTINGS)
    assert with_empty == baseline


def test_main_multi_match_untouched_by_health_intent():
    baseline = _field_match_clauses("heart healthy foods", SETTINGS)
    with_health = _field_match_clauses(
        "heart healthy foods", SETTINGS, health_intent_matched_phrases=("heart healthy",)
    )
    baseline_mm = _multi_match(baseline, lambda m: m.get("type") == "best_fields" and any(f.startswith("name^") for f in m["fields"]))
    health_mm = _multi_match(with_health, lambda m: m.get("type") == "best_fields" and any(f.startswith("name^") for f in m["fields"]))
    assert baseline_mm == health_mm
    assert health_mm["query"] == "heart healthy foods"


def test_bool_prefix_boost_untouched_by_health_intent():
    baseline = _field_match_clauses("heart healthy foods", SETTINGS)
    with_health = _field_match_clauses(
        "heart healthy foods", SETTINGS, health_intent_matched_phrases=("heart healthy",)
    )
    baseline_bp = _multi_match(baseline, lambda m: m.get("type") == "bool_prefix")
    health_bp = _multi_match(with_health, lambda m: m.get("type") == "bool_prefix")
    assert baseline_bp == health_bp


def test_bool_prefix_has_minimum_should_match():
    clauses = _field_match_clauses("heart healthy foods", SETTINGS)
    bool_prefix = _multi_match(clauses, lambda m: m.get("type") == "bool_prefix")
    assert bool_prefix.get("minimum_should_match") == "50%"


def test_bool_prefix_minimum_should_match_never_reuses_the_percentages_that_zero_out_hits():
    for n_words in (2, 3, 4, 5, 8):
        text = " ".join(["word"] * n_words)
        assert _bool_prefix_minimum_should_match_for(text) != _minimum_should_match_for(text)
        assert _bool_prefix_minimum_should_match_for(text) == "50%"


def test_match_phrase_uses_core_text_when_health_intent_present():
    clauses = _field_match_clauses(
        "heart healthy foods", SETTINGS, health_intent_matched_phrases=("heart healthy",)
    )
    match_phrase = [c["match_phrase"]["name"] for c in clauses if "match_phrase" in c]
    assert match_phrase == []


def test_match_phrase_uses_full_text_without_health_intent():
    clauses = _field_match_clauses("heart healthy foods", SETTINGS)
    match_phrase = next(c["match_phrase"]["name"] for c in clauses if "match_phrase" in c)
    assert match_phrase["query"] == "heart healthy foods"
    assert match_phrase["boost"] == 4.0


def test_match_phrase_fires_at_full_boost_when_two_core_words_remain():
    clauses = _field_match_clauses(
        "heart healthy greek yogurt", SETTINGS, health_intent_matched_phrases=("heart healthy",)
    )
    match_phrase = next(c["match_phrase"]["name"] for c in clauses if "match_phrase" in c)
    assert match_phrase["query"] == "greek yogurt"
    assert match_phrase["boost"] == 4.0


def test_exact_term_uses_core_text_when_health_intent_present():
    clauses = _field_match_clauses(
        "heart healthy foods", SETTINGS, health_intent_matched_phrases=("heart healthy",)
    )
    exact_term = next(c["term"]["name_phonetic.keyword"] for c in clauses if "term" in c)
    assert exact_term["value"] == "foods"


def test_exact_term_omitted_when_core_text_empty():
    clauses = _field_match_clauses(
        "heart healthy", SETTINGS, health_intent_matched_phrases=("heart healthy",)
    )
    exact_terms = [c["term"]["name.exact_normalized"] for c in clauses if "term" in c and "name.exact_normalized" in c["term"]]
    assert exact_terms == []


def test_build_query_accepts_health_intent_matched_phrases():
    body = build_query(
        _pq("heart healthy foods"), filters=None, size=10, settings=SETTINGS,
        health_intent_matched_phrases=("heart healthy",),
    )
    assert "query" in body


def test_build_query_byte_identical_without_health_intent():
    body_with_default = build_query(_pq("chips"), filters=None, size=10, settings=SETTINGS)
    body_with_empty = build_query(
        _pq("chips"), filters=None, size=10, settings=SETTINGS, health_intent_matched_phrases=()
    )
    assert body_with_default == body_with_empty


def test_specific_product_queries_unaffected_by_health_intent_path():
    for query in ("almonds", "dragon fruit", "greek yogurt", "protein powder", "protein bar"):
        baseline = build_query(_pq(query), filters=None, size=10, settings=SETTINGS)
        with_empty = build_query(_pq(query), filters=None, size=10, settings=SETTINGS, health_intent_matched_phrases=())
        assert baseline == with_empty


def test_protein_query_adds_supplement_hierarchy_boost_clause():
    clauses = _field_match_clauses("protein", SETTINGS)
    supplement_clause = next(
        c["nested"] for c in clauses
        if "nested" in c
        and c["nested"].get("query", {}).get("bool", {}).get("filter") == [
            {"term": {"category_hierarchies.segments": "supplements"}}
        ]
    )
    should_terms = supplement_clause["query"]["bool"]["should"]
    values = {term["term"]["category_hierarchies.segments"]["value"] for term in should_terms}
    assert "supplements" in values
    assert "protein" in values
    assert "pre_post_workout" in values


def test_non_targeted_queries_do_not_get_supplement_hierarchy_boost_clause():
    clauses = _field_match_clauses("protein bar", SETTINGS)
    matched = [
        c for c in clauses
        if "nested" in c
        and c["nested"].get("query", {}).get("bool", {}).get("filter") == [
            {"term": {"category_hierarchies.segments": "supplements"}}
        ]
    ]
    assert matched == []


def test_preworkout_query_adds_workout_hierarchy_boost_clause():
    clauses = _field_match_clauses("preworkout", SETTINGS)
    supplement_clause = next(
        c["nested"] for c in clauses
        if "nested" in c
        and c["nested"].get("query", {}).get("bool", {}).get("filter") == [
            {"term": {"category_hierarchies.segments": "supplements"}}
        ]
        and any(
            term.get("term", {}).get("category_hierarchies.segments", {}).get("value") == "pre_workout"
            for term in c["nested"].get("query", {}).get("bool", {}).get("should", [])
        )
    )
    should_terms = supplement_clause["query"]["bool"]["should"]
    values = {term["term"]["category_hierarchies.segments"]["value"] for term in should_terms}
    assert "supplements" in values
    assert "pre_post_workout" in values
    assert "pre_workout" in values


def test_derivative_demotion_skips_powder_for_protein_query():
    negative = build_derivative_demotion_negative_query(_pq("protein"))
    text = negative["match"]["name"]["query"]
    assert "powder" not in text.split()


def test_derivative_demotion_keeps_powder_for_non_protein_query():
    negative = build_derivative_demotion_negative_query(_pq("apple"))
    text = negative["match"]["name"]["query"]
    assert "powder" in text.split()
