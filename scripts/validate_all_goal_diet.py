#!/usr/bin/env python3
"""Validate every Goal/Diet tile against the live index after YAML rewrite."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
load_dotenv(_ROOT / ".env")

from search_v2.config.settings import SETTINGS
from search_v2.goal_diet.registry_loader import get_compiled_registry
from search_v2.retrieval.filters import SearchFilters, build_filter_clauses
from search_v2.retrieval.opensearch_client import OpenSearchClient, extract_hits

QUAKER_OATS_ID = "01K1B1BPZNB9PH1XDQ3PFABHK9"

# Products that should NOT qualify for specific tiles (known PDF violations).
NEGATIVE_FIXTURES: Dict[str, List[Dict[str, str]]] = {
    "keto": [
        {
            "id": QUAKER_OATS_ID,
            "name": "Quaker Oats",
            "reason": "net_carbs=58.5, fat_cal_pct=12.2% — fails PDF keto macros",
        }
    ],
}

# Spot-check IDs known to represent valid tile members (from prior E2E runs).
POSITIVE_SPOT_CHECKS: Dict[str, str] = {
    "high_protein": "protein_g >= 10",
    "vegetarian": "dietary_label veg",
    "low_sodium": "sodium_mg <= 120",
}


def _pool_size(client: OpenSearchClient, goal_id: str) -> int:
    clauses = build_filter_clauses(SearchFilters(goal_diet_ids=[goal_id]))
    body = {
        "size": 0,
        "track_total_hits": True,
        "query": {"bool": {"filter": clauses.filter_clauses, "must_not": clauses.must_not_clauses}},
    }
    return int(client.search(body).get("hits", {}).get("total", {}).get("value", 0))


def _product_in_pool(client: OpenSearchClient, goal_id: str, product_id: str) -> bool:
    clauses = build_filter_clauses(SearchFilters(goal_diet_ids=[goal_id]))
    body = {
        "size": 1,
        "query": {
            "bool": {
                "filter": [*clauses.filter_clauses, {"ids": {"values": [product_id]}}],
                "must_not": clauses.must_not_clauses,
            }
        },
    }
    return bool(extract_hits(client.search(body)))


def _sample_products(client: OpenSearchClient, goal_id: str, n: int = 3) -> List[dict]:
    clauses = build_filter_clauses(SearchFilters(goal_diet_ids=[goal_id]))
    body = {
        "size": n,
        "query": {"bool": {"filter": clauses.filter_clauses, "must_not": clauses.must_not_clauses}},
        "_source": ["id", "name"],
    }
    return [src for _, _, src in extract_hits(client.search(body))]


def main() -> None:
    get_compiled_registry(force_reload=True)
    registry = get_compiled_registry()
    client = OpenSearchClient(settings=SETTINGS)

    results: List[Dict[str, Any]] = []
    incorrect_admissions: List[Dict[str, Any]] = []

    for goal_id in sorted(registry.definitions.keys()):
        pool = _pool_size(client, goal_id)
        samples = _sample_products(client, goal_id)
        negatives = NEGATIVE_FIXTURES.get(goal_id, [])
        neg_results = []
        for neg in negatives:
            admitted = _product_in_pool(client, goal_id, neg["id"])
            if admitted:
                incorrect_admissions.append({**neg, "goal_id": goal_id})
            neg_results.append({**neg, "admitted": admitted})

        results.append(
            {
                "goal_id": goal_id,
                "pool_size": pool,
                "samples": [{"id": s.get("id"), "name": s.get("name")} for s in samples],
                "negative_checks": neg_results,
            }
        )

    print(json.dumps({"tiles": results, "incorrect_admissions": incorrect_admissions}, indent=2))

    if incorrect_admissions:
        print(f"\nFAIL: {len(incorrect_admissions)} incorrect admission(s)", file=sys.stderr)
        sys.exit(1)
    print("\nOK: no known incorrect admissions detected")


if __name__ == "__main__":
    main()
