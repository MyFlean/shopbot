#!/usr/bin/env python3
"""One-off E2E validation: evaluate Goal/Diet rules against live index products."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
load_dotenv(_ROOT / ".env")

from search_v2.config.settings import SETTINGS
from search_v2.goal_diet.registry_loader import get_compiled_registry
from search_v2.goal_diet.merge import merge_goal_diet_plans
from search_v2.retrieval.filters import build_filter_clauses, SearchFilters
from search_v2.retrieval.opensearch_client import OpenSearchClient, extract_hits


def _nested(source: dict, path: str) -> Any:
    cur: Any = source
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _nutri(source: dict, key: str) -> Optional[float]:
    val = _nested(source, f"category_data.nutritional.nutri_breakdown_updated.{key}")
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _tags(source: dict, kind: str) -> List[str]:
    raw = _nested(source, f"category_data.tags.{kind}")
    if isinstance(raw, list):
        return [str(x).lower() for x in raw]
    return []


def _ingredient_text(source: dict) -> str:
    return str(_nested(source, "ingredients.raw_text") or "")


def _contains_phrase(text: str, phrase: str) -> bool:
    """Approximate match_phrase: case-insensitive substring on normalized whitespace."""
    norm = re.sub(r"\s+", " ", text.lower())
    needle = re.sub(r"\s+", " ", phrase.lower()).strip()
    return needle in norm


def _eval_clause(source: dict, clause: dict) -> Tuple[bool, str]:
    if "range" in clause:
        for field, bounds in clause["range"].items():
            val = _nested(source, field.replace("category_data.nutritional.nutri_breakdown_updated.", "category_data.nutritional.nutri_breakdown_updated."))
            if field.startswith("category_data.nutritional"):
                key = field.split(".")[-1]
                val = _nutri(source, key)
            elif field.startswith("flean_score."):
                val = _nested(source, field)
            elif field.startswith("stats."):
                val = _nested(source, field)
            else:
                val = _nested(source, field)
            if val is None:
                return False, f"range {field}: missing"
            for op, bound in bounds.items():
                if op == "gte" and not (val >= bound):
                    return False, f"range {field} {val} not gte {bound}"
                if op == "gt" and not (val > bound):
                    return False, f"range {field} {val} not gt {bound}"
                if op == "lte" and not (val <= bound):
                    return False, f"range {field} {val} not lte {bound}"
                if op == "lt" and not (val < bound):
                    return False, f"range {field} {val} not lt {bound}"
        return True, f"range matched"

    if "term" in clause:
        for field, expected in clause["term"].items():
            actual = _nested(source, field)
            if str(actual).lower() != str(expected).lower():
                return False, f"term {field}={actual!r} != {expected!r}"
        return True, "term matched"

    if "exists" in clause:
        field = clause["exists"]["field"]
        val = _nested(source, field)
        if val is None:
            return False, f"exists {field}: missing"
        return True, f"exists {field}"

    if "match_phrase" in clause:
        for field, phrase in clause["match_phrase"].items():
            text = str(_nested(source, field) or "")
            if not _contains_phrase(text, str(phrase)):
                return False, f"match_phrase {phrase!r} not in {field}"
        return True, f"match_phrase {clause['match_phrase']}"

    if "bool" in clause:
        b = clause["bool"]
        if "should" in b:
            reasons = []
            for child in b["should"]:
                ok, why = _eval_clause(source, child)
                if ok:
                    return True, f"any_of: {why}"
                reasons.append(why)
            return False, "any_of failed: " + "; ".join(reasons[:3])

        if "must" in b:
            parts = []
            for child in b["must"]:
                ok, why = _eval_clause(source, child)
                if not ok:
                    return False, f"all_of failed: {why}"
                parts.append(why)
            return True, "all_of: " + ", ".join(parts[:2])

        if "must_not" in b:
            for child in b["must_not"]:
                ok, why = _eval_clause(source, child)
                if ok:
                    return False, f"must_not hit: {why}"
            return True, "must_not passed"

    return False, f"unsupported clause {list(clause.keys())}"


def evaluate_product(source: dict, goal_id: str) -> dict:
    reg = get_compiled_registry()
    defn = reg.definitions[goal_id]
    inclusion_hits = []
    inclusion_fail = None
    for clause in defn.include_clauses:
        ok, why = _eval_clause(source, clause)
        if ok:
            inclusion_hits.append(why)
        else:
            inclusion_fail = why
            break

    eligible = inclusion_fail is None and len(inclusion_hits) == len(defn.include_clauses)

    exclusion_hit = None
    for clause in defn.exclude_clauses:
        ok, why = _eval_clause(source, clause)
        if ok:
            exclusion_hit = why
            eligible = False
            break

    return {
        "goal_id": goal_id,
        "eligible": eligible,
        "inclusion_matched": inclusion_hits,
        "inclusion_failed": inclusion_fail,
        "exclusion_matched": exclusion_hit,
    }


def fetch_products(client: OpenSearchClient, body: dict, n: int = 5) -> List[dict]:
    body = {**body, "size": n}
    resp = client.search(body)
    return [src for _, _, src in extract_hits(resp)]


def product_in_goal_search(client: OpenSearchClient, goal_id: str, product_id: str) -> bool:
    sf = SearchFilters(goal_diet_ids=[goal_id])
    clauses = build_filter_clauses(sf)
    body = {
        "query": {
            "bool": {
                "filter": clauses.filter_clauses,
                "must_not": clauses.must_not_clauses,
                "must": [{"term": {"id.keyword": product_id}}] if False else [{"ids": {"values": [product_id]}}],
            }
        }
    }
    # ids query is simpler
    body = {
        "query": {
            "bool": {
                "filter": [
                    *clauses.filter_clauses,
                    {"ids": {"values": [product_id]}},
                ],
                "must_not": clauses.must_not_clauses,
            }
        }
    }
    resp = client.search(body)
    return bool(extract_hits(resp))


def summarize(source: dict) -> dict:
    return {
        "name": source.get("name"),
        "id": source.get("id"),
        "protein_g": _nutri(source, "protein_g"),
        "sugar_g": _nutri(source, "sugar_g"),
        "fiber_g": _nutri(source, "fiber_g"),
        "energy_kcal": _nutri(source, "energy_kcal"),
        "carbs_g": _nutri(source, "carbs_g"),
        "fat_g": _nutri(source, "fat_g"),
        "sodium_mg": _nutri(source, "sodium_mg"),
        "adjusted_score": _nested(source, "flean_score.adjusted_score"),
        "dietary_tags": _tags(source, "dietary_tags"),
        "ingredient_tags": _tags(source, "ingredient_tags"),
        "dietary_label": _nested(source, "category_data.dietary_label"),
        "ingredients_snippet": _ingredient_text(source)[:180],
    }


def main() -> None:
    get_compiled_registry(force_reload=True)
    client = OpenSearchClient(settings=SETTINGS)

    scenarios: List[dict] = []

    # Scenario 1: high protein via macros, no marketing tag
    candidates = fetch_products(client, {
        "query": {
            "bool": {
                "filter": [
                    {"range": {"category_data.nutritional.nutri_breakdown_updated.protein_g": {"gte": 12}}},
                ],
                "must_not": [
                    {"term": {"category_data.tags.ingredient_tags.keyword": "high_protein_density"}},
                ],
            }
        }
    }, n=20)
    for src in candidates:
        ev = evaluate_product(src, "high_protein")
        if ev["eligible"]:
            scenarios.append({"scenario": 1, "description": "No tag, qualifies via protein_g", **summarize(src), **ev})
            break

    # Scenario 2: nut_free tag + passes rules
    for src in fetch_products(client, {"query": {"term": {"category_data.tags.dietary_tags.keyword": "nut_free"}}}, n=15):
        ev = evaluate_product(src, "nut_free")
        if ev["eligible"] and "nut_free" in _tags(src, "dietary_tags"):
            scenarios.append({"scenario": 2, "description": "Has nut_free tag and passes rules", **summarize(src), **ev})
            break

    # Scenario 3: has tag-like signal but fails exclusion (high sugar for low_sugar goal)
    for src in fetch_products(client, {
        "query": {"range": {"category_data.nutritional.nutri_breakdown_updated.sugar_g": {"gte": 20}}}
    }, n=30):
        ev = evaluate_product(src, "low_sugar")
        if not ev["eligible"]:
            scenarios.append({"scenario": 3, "description": "High sugar fails low_sugar rules", **summarize(src), **ev})
            break

    # Scenario 4: vegan via ingredient-text fallback (veg label, no vegan tag)
    for src in fetch_products(client, {
        "query": {
            "bool": {
                "filter": [{"term": {"category_data.dietary_label": "veg"}}],
                "must_not": [{"term": {"category_data.tags.dietary_tags.keyword": "vegan"}}],
            }
        }
    }, n=40):
        raw = _ingredient_text(src).lower()
        if any(x in raw for x in ("milk", "ghee", "whey", "honey", "egg")):
            continue
        ev = evaluate_product(src, "vegan")
        if ev["eligible"]:
            scenarios.append({"scenario": 4, "description": "Vegan via dietary_label + ingredient fallback", **summarize(src), **ev})
            break

    # Scenario 5: exclusion fires (high_protein score floor)
    for src in fetch_products(client, {
        "query": {"range": {"flean_score.adjusted_score": {"lt": 50}}}
    }, n=40):
        if (_nutri(src, "protein_g") or 0) >= 10:
            ev = evaluate_product(src, "high_protein")
            if ev["exclusion_matched"]:
                scenarios.append({"scenario": 5, "description": "Excluded by score floor", **summarize(src), **ev})
                break

    # Ingredient text robustness: MILK vs milk, punctuation
    punct_tests = [
        ("MILK Solids, Whey", "milk", True),
        ("contains 2% MILK fat.", "milk", True),
        ("almond beverage (no dairy)", "milk", False),
    ]
    punct_results = []
    for text, phrase, expected in punct_tests:
        punct_results.append({"text": text, "phrase": phrase, "expected": expected, "actual": _contains_phrase(text, phrase)})

    print(json.dumps({"scenarios": scenarios, "ingredient_text_tests": punct_results}, indent=2, default=str))

    # ES confirmation for scenario 1 if found
    if scenarios:
        s1 = next((s for s in scenarios if s["scenario"] == 1), None)
        if s1 and s1.get("id"):
            in_search = product_in_goal_search(client, "high_protein", s1["id"])
            print(f"\nES filter confirmation scenario 1 in high_protein search: {in_search}")


if __name__ == "__main__":
    main()
