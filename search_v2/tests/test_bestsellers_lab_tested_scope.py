"""Tests that lab-tested prepend in best_selling is scoped to category_paths."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from search_v2.extension.bestsellers import bestsellers as bestsellers_mod
from search_v2.extension.bestsellers.bestsellers import (
    best_selling,
    fetch_lab_tested_candidates,
)


def _filters_from_body(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(((body.get("query") or {}).get("bool") or {}).get("filter") or [])


def _category_terms_values(filters: List[Dict[str, Any]]) -> Optional[List[str]]:
    for clause in filters:
        terms = clause.get("terms") or {}
        if "category_paths" in terms:
            return list(terms["category_paths"])
    return None


def test_fetch_lab_tested_candidates_scopes_to_category_paths():
    captured: List[Dict[str, Any]] = []
    mock_client = MagicMock()
    mock_client.search.side_effect = lambda body: (
        captured.append(body) or {"hits": {"hits": []}}
    )

    paths = [
        "f_and_b/supplements/protein/whey_blend",
        "f_and_b/supplements/pre_post_workout/creatine",
    ]
    with patch.object(bestsellers_mod, "_get_client", return_value=mock_client):
        fetch_lab_tested_candidates(category_paths=paths)

    assert len(captured) == 1
    filters = _filters_from_body(captured[0])
    assert {"exists": {"field": "category_data.lab_reports.url"}} in filters
    assert _category_terms_values(filters) == paths


def test_fetch_lab_tested_candidates_best_selling_paths_scoped():
    captured: List[Dict[str, Any]] = []
    mock_client = MagicMock()
    mock_client.search.side_effect = lambda body: (
        captured.append(body) or {"hits": {"hits": []}}
    )

    paths = [
        "f_and_b/food/dairy_and_bakery/bread_and_buns",
        "f_and_b/food/biscuits_and_crackers",
        "f_and_b/food/breakfast_essentials/muesli_and_oats",
    ]
    with patch.object(bestsellers_mod, "_get_client", return_value=mock_client):
        fetch_lab_tested_candidates(category_paths=paths)

    assert _category_terms_values(_filters_from_body(captured[0])) == paths


def test_fetch_lab_tested_candidates_without_paths_stays_global():
    captured: List[Dict[str, Any]] = []
    mock_client = MagicMock()
    mock_client.search.side_effect = lambda body: (
        captured.append(body) or {"hits": {"hits": []}}
    )

    with patch.object(bestsellers_mod, "_get_client", return_value=mock_client):
        fetch_lab_tested_candidates()

    assert _category_terms_values(_filters_from_body(captured[0])) is None


def test_best_selling_passes_category_paths_to_lab_fetch():
    paths = ["f_and_b/supplements/protein/whey_blend"]
    captured_lab_paths: List[Optional[List[str]]] = []

    def fake_lab(*, limit=5, category_paths=None):
        captured_lab_paths.append(category_paths)
        return []

    with patch.object(bestsellers_mod, "fetch_lab_tested_candidates", side_effect=fake_lab), patch.object(
        bestsellers_mod,
        "fetch_candidates_by_category",
        return_value={
            paths[0]: [
                {
                    "id": "supp-1",
                    "parent_id": "fam-supp-1",
                    "name": "Whey",
                    "flean_score": {"adjusted_score": 8.0},
                }
            ]
        },
    ):
        cards = best_selling(paths, per_category=1, total_products=3, fetch_buffer=0)

    assert captured_lab_paths == [paths]
    assert [c.get("id") for c in cards] == ["supp-1"]


def test_best_selling_empty_lab_does_not_prepend_out_of_path():
    paths = ["f_and_b/supplements/protein/whey_blend"]

    with patch.object(bestsellers_mod, "fetch_lab_tested_candidates", return_value=[]), patch.object(
        bestsellers_mod,
        "fetch_candidates_by_category",
        return_value={
            paths[0]: [
                {
                    "id": "supp-1",
                    "parent_id": "fam-supp-1",
                    "name": "Whey",
                    "flean_score": {"adjusted_score": 8.0},
                }
            ]
        },
    ):
        cards = best_selling(paths, per_category=1, total_products=3, fetch_buffer=0)

    assert [c.get("id") for c in cards] == ["supp-1"]
    assert "01KXDD92YTPN4YF8VS2EBCAK25" not in {c.get("id") for c in cards}
