#!/usr/bin/env python3
"""Trace a single product through the Keto Goal/Diet pipeline."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
load_dotenv(_ROOT / ".env")

from search_v2.config.settings import SETTINGS
from search_v2.goal_diet.goal_only_retrieval import is_goal_only_retrieval
from search_v2.goal_diet.registry_loader import get_compiled_registry
from search_v2.query_processing.canonical_produce import load_produce_alias_map, PRODUCE_SYNONYMS_PATH
from search_v2.query_processing.product_intent_extractor import (
    ProductIntentExtractor,
    load_product_type_lexicon,
    PRODUCT_TYPE_LEXICON_PATH,
)
from search_v2.query_processing.query_pipeline import process_search_request
from search_v2.query_processing.typo_correction import VocabularyCorrector
from search_v2.query_processing.vocabulary_builder import VOCABULARY_PATH, load_vocabulary, seed_vocabulary
from search_v2.retrieval.filters import SearchFilters, build_filter_clauses
from search_v2.retrieval.lexical_query_builder import build_query
from search_v2.retrieval.opensearch_client import OpenSearchClient

PRODUCT_ID = "01K1B1BPZNB9PH1XDQ3PFABHK9"


def nested(d: dict, path: str):
    cur = d
    for p in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def nutri(src: dict, key: str):
    v = nested(src, f"category_data.nutritional.nutri_breakdown_updated.{key}")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def contains_phrase(text: str, phrase: str) -> bool:
    norm = re.sub(r"\s+", " ", (text or "").lower())
    needle = re.sub(r"\s+", " ", phrase.lower()).strip()
    return needle in norm


def eval_clause(src: dict, clause: dict):
    if "range" in clause:
        details = []
        all_ok = True
        for field, bounds in clause["range"].items():
            if field.startswith("category_data.nutritional"):
                val = nutri(src, field.split(".")[-1])
            else:
                val = nested(src, field)
            row = {"field": field, "indexed_value": val, "bounds": bounds}
            if val is None:
                row["result"] = "SKIPPED (field missing)"
                all_ok = False
            else:
                ok = True
                checks = []
                for op, bound in bounds.items():
                    passed = {
                        "gte": val >= bound,
                        "gt": val > bound,
                        "lte": val <= bound,
                        "lt": val < bound,
                    }[op]
                    checks.append(f"{val} {op} {bound} -> {'PASS' if passed else 'FAIL'}")
                    if not passed:
                        ok = False
                row["checks"] = checks
                row["result"] = "PASS" if ok else "FAIL"
                if not ok:
                    all_ok = False
            details.append(row)
        return all_ok, details

    if "bool" in clause:
        b = clause["bool"]
        if "should" in b:
            branches = []
            any_ok = False
            for i, child in enumerate(b["should"], 1):
                ok, det = eval_clause(src, child)
                branches.append({"branch": i, "pass": ok, "detail": det})
                any_ok = any_ok or ok
            return any_ok, {"type": "any_of", "branches": branches}
        if "must" in b:
            branches = []
            all_ok = True
            for i, child in enumerate(b["must"], 1):
                ok, det = eval_clause(src, child)
                branches.append({"branch": i, "pass": ok, "detail": det})
                all_ok = all_ok and ok
            return all_ok, {"type": "all_of", "branches": branches}
        if "must_not" in b:
            branches = []
            excluded = False
            for i, child in enumerate(b["must_not"], 1):
                ok, det = eval_clause(src, child)
                branches.append({"branch": i, "exclude_triggered": ok, "detail": det})
                if ok:
                    excluded = True
            return not excluded, {"type": "must_not", "branches": branches}

    if "match_phrase" in clause:
        details = []
        all_ok = True
        for field, phrase in clause["match_phrase"].items():
            text = str(nested(src, field) or "")
            ok = contains_phrase(text, str(phrase))
            details.append({"field": field, "phrase": phrase, "result": "PASS (excluded)" if ok else "FAIL (not excluded)"})
            all_ok = all_ok and ok
        return all_ok, details

    return False, {"unsupported": list(clause.keys())}


def main() -> None:
    client = OpenSearchClient(settings=SETTINGS)
    resp = client.search({"size": 1, "query": {"ids": {"values": [PRODUCT_ID]}}})
    hits = resp.get("hits", {}).get("hits", [])
    if not hits:
        print("Product not found")
        return

    src = hits[0]["_source"]
    print("=== PRODUCT ===")
    print(json.dumps({"id": PRODUCT_ID, "name": src.get("name")}, indent=2))

    nutrients = {k: nutri(src, k) for k in ("energy_kcal", "protein_g", "fat_g", "carbs_g", "fiber_g", "sugar_g")}
    print("\n=== INDEXED NUTRIENTS ===")
    print(json.dumps(nutrients, indent=2))

    carbs, fiber, fat, energy = nutrients["carbs_g"], nutrients["fiber_g"], nutrients["fat_g"], nutrients["energy_kcal"]
    pdf = {}
    if all(v is not None for v in (carbs, fiber, fat, energy)) and energy:
        pdf["net_carbs_computed"] = carbs - fiber
        pdf["fat_cal_pct_computed"] = round((fat * 9 / energy) * 100, 2)
        pdf["pdf_net_carbs_rule"] = f"<= 15 -> {'FAIL' if pdf['net_carbs_computed'] > 15 else 'PASS'}"
        pdf["pdf_fat_cal_pct_rule"] = f">= 40 -> {'FAIL' if pdf['fat_cal_pct_computed'] < 40 else 'PASS'}"
    print("\n=== PDF CRITERIA (not indexed — computed offline) ===")
    print(json.dumps(pdf, indent=2))

    percentiles = {
        "carbs_penalty": nested(src, "stats.carbs_penalty_percentiles.subcategory_percentile"),
        "sugar_penalty": nested(src, "stats.sugar_penalty_percentiles.subcategory_percentile"),
        "healthy_fat": nested(src, "stats.healthy_fat_percentiles.subcategory_percentile"),
    }
    print("\n=== INDEXED PERCENTILES ===")
    print(json.dumps(percentiles, indent=2))

    blocked = {
        "goal_metrics.net_carbs": nested(src, "goal_metrics.net_carbs"),
        "goal_metrics.fat_cal_pct": nested(src, "goal_metrics.fat_cal_pct"),
        "added_sugar_g": nutri(src, "added_sugar_g"),
    }
    print("\n=== PDF FIELDS (blocked / not indexed) ===")
    print(json.dumps(blocked, indent=2))

    registry = get_compiled_registry(force_reload=True)
    keto = registry.definitions["keto"]

    print("\n=== INCLUSION RULE EVALUATION (3 AND groups) ===")
    group_labels = [
        "Group 1: net_carbs <= 15 (derived_metric)",
        "Group 2: sugar_g <= 2 (added-sugar proxy)",
        "Group 3: fat_cal_pct >= 40 (derived_metric)",
    ]
    for label, clause in zip(group_labels, keto.include_clauses):
        ok, det = eval_clause(src, clause)
        print(f"\n{label}: {'QUALIFIES' if ok else 'FAILS'}")
        print(json.dumps(det, indent=2, default=str))

    print("\n=== EXCLUSION RULE EVALUATION ===")
    for i, clause in enumerate(keto.exclude_clauses, 1):
        ok, det = eval_clause(src, clause)
        print(f"Exclude group {i}: product excluded = {ok is False}")
        print(json.dumps(det, indent=2, default=str))

    overall = all(eval_clause(src, c)[0] for c in keto.include_clauses) and all(
        eval_clause(src, c)[0] for c in keto.exclude_clauses
    )
    print(f"\n=== OVERALL KETO TILE ELIGIBILITY ===\n{overall}")

    print("\n=== OPENSEARCH FILTER (SearchFilters(goal_diet_ids=['keto'])) ===")
    clauses = build_filter_clauses(SearchFilters(goal_diet_ids=["keto"]))
    es_query = {"bool": {"filter": clauses.filter_clauses, "must_not": clauses.must_not_clauses}}
    print(json.dumps(es_query, indent=2))

    match = client.search(
        {
            "size": 1,
            "query": {
                "bool": {
                    "filter": [*clauses.filter_clauses, {"ids": {"values": [PRODUCT_ID]}}],
                    "must_not": clauses.must_not_clauses,
                }
            },
        }
    )
    print(f"\nProduct matches compiled keto filter: {bool(match.get('hits', {}).get('hits'))}")

    vocab = load_vocabulary(VOCABULARY_PATH) or seed_vocabulary()
    req = process_search_request(
        "keto foods",
        corrector=VocabularyCorrector(vocab),
        product_intent_extractor=ProductIntentExtractor(
            load_product_type_lexicon(PRODUCT_TYPE_LEXICON_PATH),
            settings=SETTINGS,
            produce_aliases=load_produce_alias_map(PRODUCE_SYNONYMS_PATH),
        ),
        settings=SETTINGS,
    )
    print("\n=== QUERY: keto foods ===")
    print(
        json.dumps(
            {
                "goal_diet_ids": req.filters.goal_diet_ids,
                "dietary_labels": req.filters.dietary_labels,
                "remaining_text": req.processed_query.primary_text(),
                "is_goal_only": is_goal_only_retrieval(req.processed_query, req.filters, req.routing_context),
            },
            indent=2,
        )
    )
    body = build_query(req.processed_query, req.filters, size=100)
    body["_source"] = ["id", "name"]
    foods = client.search(body)
    ids = [h["_source"].get("id") for h in foods.get("hits", {}).get("hits", [])]
    print(f"Product in keto foods top-100: {PRODUCT_ID in ids}")
    if PRODUCT_ID in ids:
        print(f"Rank: {ids.index(PRODUCT_ID) + 1}")


if __name__ == "__main__":
    main()
