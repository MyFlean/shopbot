"""Tests for search_v2/extension/suggestions/suggest.py fallback behavior."""
from __future__ import annotations

import importlib
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

suggest_module = importlib.import_module("search_v2.extension.suggestions.suggest")


class _FakeClient:
    def __init__(self, responses: List[Dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def search(self, body: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append(body)
        if not self._responses:
            raise AssertionError("No fake response left for search() call")
        return self._responses.pop(0)


@contextmanager
def _with_fake_client(fake_client: _FakeClient):
    original_client = suggest_module._client
    suggest_module._client = fake_client
    try:
        yield
    finally:
        suggest_module._client = original_client


def test_suggest_completion_hit_skips_fallback():
    fake = _FakeClient(
        responses=[
            {
                "suggest": {
                    "name_suggest": [
                        {
                            "options": [
                                {"text": "Whey Protein Isolate", "_source": {"id": "1", "brand": "A", "category_group": "f_and_b"}},
                                {"text": "Whey Protein Concentrate", "_source": {"id": "2", "brand": "B", "category_group": "f_and_b"}},
                                {"text": "Whey Protein Blend", "_source": {"id": "3", "brand": "C", "category_group": "f_and_b"}},
                            ]
                        }
                    ]
                }
            }
        ]
    )
    with _with_fake_client(fake):
        out = suggest_module.suggest("whey", size=3)

    assert len(fake.calls) == 1
    assert out["meta"]["fallback_used"] is False
    assert out["meta"]["fuzzy_fallback_used"] is False
    assert out["meta"]["prefix_fallback_used"] is False
    assert out["meta"]["returned"] == 3


def test_suggest_empty_completion_uses_fallback():
    fake = _FakeClient(
        responses=[
            {"suggest": {"name_suggest": [{"options": []}]}},
            {
                "hits": {
                    "hits": [
                        {"_source": {"name": "BCAA Energy Drink", "id": "10", "brand": "FitCo", "category_group": "f_and_b"}},
                        {"_source": {"name": "BCAA Recovery Blend", "id": "11", "brand": "FitCo", "category_group": "f_and_b"}},
                    ]
                }
            },
        ]
    )
    with _with_fake_client(fake):
        out = suggest_module.suggest("bcca", size=5)

    assert len(fake.calls) == 2
    assert out["meta"]["fallback_used"] is True
    assert out["meta"]["fuzzy_fallback_used"] is True
    assert out["meta"]["prefix_fallback_used"] is True
    assert out["meta"]["phonetic_fallback_used"] is False
    assert out["meta"]["returned"] == 2
    assert any("BCAA" in item["text"] for item in out["suggestions"])


def test_suggest_non_prefix_token_mismatch_triggers_fallback():
    fake = _FakeClient(
        responses=[
            {
                "suggest": {
                    "name_suggest": [
                        {
                            "options": [
                                {"text": "Cream Bell Vanilla Shake", "_source": {"id": "21", "brand": "Cream Bell", "category_group": "f_and_b"}},
                                {"text": "Creamy Oats", "_source": {"id": "22", "brand": "FoodCo", "category_group": "f_and_b"}},
                                {"text": "Cereal Mix", "_source": {"id": "23", "brand": "FoodCo", "category_group": "f_and_b"}},
                                {"text": "Creatine-Free Cookie", "_source": {"id": "24", "brand": "SnackCo", "category_group": "f_and_b"}},
                            ]
                        }
                    ]
                }
            },
            {
                "hits": {
                    "hits": [
                        {"_source": {"name": "Muscle BCAA 2:1:1", "id": "25", "brand": "SuppCo", "category_group": "f_and_b"}}
                    ]
                }
            },
        ]
    )
    with _with_fake_client(fake):
        out = suggest_module.suggest("bcca", size=6)

    assert len(fake.calls) == 2
    assert out["meta"]["fallback_used"] is True
    assert any(item["text"] == "Muscle BCAA 2:1:1" for item in out["suggestions"])


def test_suggest_three_char_token_mismatch_triggers_fallback():
    fake = _FakeClient(
        responses=[
            {
                "suggest": {
                    "name_suggest": [
                        {
                            "options": [
                                {"text": "Earth Story Organic Seeds", "_source": {"id": "41", "brand": "Earth Story", "category_group": "f_and_b"}},
                                {"text": "Early Harvest Oats", "_source": {"id": "42", "brand": "Early Harvest", "category_group": "f_and_b"}},
                                {"text": "Earl Grey Tea", "_source": {"id": "43", "brand": "TeaCo", "category_group": "f_and_b"}},
                                {"text": "Earthy Trail Mix", "_source": {"id": "44", "brand": "SnackCo", "category_group": "f_and_b"}},
                            ]
                        }
                    ]
                }
            },
            {
                "hits": {
                    "hits": [
                        {"_source": {"name": "Essential EAA Aminos", "id": "45", "brand": "SuppCo", "category_group": "f_and_b"}}
                    ]
                }
            },
        ]
    )
    with _with_fake_client(fake):
        out = suggest_module.suggest("eaa", size=6)

    assert len(fake.calls) == 2
    assert out["meta"]["fallback_used"] is True
    assert any(item["text"] == "Essential EAA Aminos" for item in out["suggestions"])
    assert out["suggestions"][0]["text"] == "Essential EAA Aminos"


def test_suggest_dedupes_and_respects_size_across_stages():
    fake = _FakeClient(
        responses=[
            {
                "suggest": {
                    "name_suggest": [
                        {
                            "options": [
                                {"text": "BCAA Gold", "_source": {"id": "30", "brand": "SuppCo", "category_group": "f_and_b"}},
                            ]
                        }
                    ]
                }
            },
            {
                "hits": {
                    "hits": [
                        {"_source": {"name": "BCAA Gold", "id": "30", "brand": "SuppCo", "category_group": "f_and_b"}},
                        {"_source": {"name": "BCAA Recovery", "id": "31", "brand": "SuppCo", "category_group": "f_and_b"}},
                        {"_source": {"name": "BCAA Max", "id": "32", "brand": "SuppCo", "category_group": "f_and_b"}},
                    ]
                }
            },
        ]
    )
    with _with_fake_client(fake):
        out = suggest_module.suggest("bcca", size=2)

    assert len(fake.calls) == 2
    assert len(out["suggestions"]) == 2
    texts = [item["text"] for item in out["suggestions"]]
    assert len(set(texts)) == 2
    assert "BCAA Gold" in texts
    assert any(text in texts for text in ("BCAA Recovery", "BCAA Max"))


def test_protein_override_prefers_supplements_in_completion():
    fake = _FakeClient(
        responses=[
            {
                "suggest": {
                    "name_suggest": [
                        {
                            "options": [
                                {
                                    "text": "Protein Chef Roasted Soya Mixture",
                                    "_source": {
                                        "id": "51",
                                        "brand": "Protein Chef",
                                        "category_group": "f_and_b",
                                        "category_paths": ["f_and_b/food/light_bites/savory_namkeen"],
                                    },
                                },
                                {
                                    "text": "Whey Protein Isolate 1kg",
                                    "_source": {
                                        "id": "52",
                                        "brand": "SuppCo",
                                        "category_group": "f_and_b",
                                        "category_paths": ["f_and_b/supplements/protein/whey_isolate"],
                                    },
                                },
                                {
                                    "text": "Protein Oats",
                                    "_source": {
                                        "id": "53",
                                        "brand": "FoodCo",
                                        "category_group": "f_and_b",
                                        "category_paths": ["f_and_b/food/breakfast_essentials/muesli_and_oats"],
                                    },
                                },
                            ]
                        }
                    ]
                }
            }
        ]
    )
    with _with_fake_client(fake):
        out = suggest_module.suggest("protein", size=3)

    assert len(fake.calls) == 1
    assert out["suggestions"][0]["text"] == "Whey Protein Isolate 1kg"


def test_protein_override_adds_supplement_boost_clause_in_fallback():
    fake = _FakeClient(
        responses=[
            {"suggest": {"name_suggest": [{"options": []}]}},
            {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "name": "Whey Protein Concentrate",
                                "id": "61",
                                "brand": "SuppCo",
                                "category_group": "f_and_b",
                                "category_paths": ["f_and_b/supplements/protein/whey_concentrate"],
                            }
                        }
                    ]
                }
            },
            {"hits": {"hits": []}},
        ]
    )
    with _with_fake_client(fake):
        out = suggest_module.suggest("protein powder", size=5)

    assert out["meta"]["fallback_used"] is True
    assert len(fake.calls) == 3
    prefetch_filters = fake.calls[1]["query"]["bool"]["filter"]
    assert any("prefix" in clause and "category_paths" in clause["prefix"] for clause in prefetch_filters)


def test_non_override_query_does_not_prefer_supplements():
    fake = _FakeClient(
        responses=[
            {
                "suggest": {
                    "name_suggest": [
                        {
                            "options": [
                                {
                                    "text": "Protein Bar Dark Chocolate",
                                    "_source": {
                                        "id": "71",
                                        "brand": "SnackCo",
                                        "category_group": "f_and_b",
                                        "category_paths": ["f_and_b/food/light_bites/energy_bars"],
                                    },
                                },
                                {
                                    "text": "Whey Protein Bar",
                                    "_source": {
                                        "id": "72",
                                        "brand": "SuppCo",
                                        "category_group": "f_and_b",
                                        "category_paths": ["f_and_b/supplements/protein/whey_isolate"],
                                    },
                                },
                            ]
                        }
                    ]
                }
            },
            {"hits": {"hits": []}},
        ]
    )
    with _with_fake_client(fake):
        out = suggest_module.suggest("protein bar", size=2)

    assert len(fake.calls) == 2
    fallback_should = fake.calls[1]["query"]["bool"]["should"]
    assert not any("prefix" in clause and "category_paths" in clause["prefix"] for clause in fallback_should)
    # Non-exact query should preserve completion ordering.
    assert out["suggestions"][0]["text"] == "Protein Bar Dark Chocolate"


def test_protein_override_prefetches_supplements_when_completion_has_none():
    fake = _FakeClient(
        responses=[
            {
                "suggest": {
                    "name_suggest": [
                        {
                            "options": [
                                {
                                    "text": "Protein Chef Roasted Soya Mixture",
                                    "_source": {
                                        "id": "81",
                                        "brand": "Protein Chef",
                                        "category_group": "f_and_b",
                                        "category_paths": ["f_and_b/food/light_bites/savory_namkeen"],
                                    },
                                },
                                {
                                    "text": "Protein Oats",
                                    "_source": {
                                        "id": "82",
                                        "brand": "FoodCo",
                                        "category_group": "f_and_b",
                                        "category_paths": ["f_and_b/food/breakfast_essentials/muesli_and_oats"],
                                    },
                                },
                            ]
                        }
                    ]
                }
            },
            {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "name": "Whey Protein Isolate 1kg",
                                "id": "83",
                                "brand": "SuppCo",
                                "category_group": "f_and_b",
                                "category_paths": ["f_and_b/supplements/protein/whey_isolate"],
                            }
                        }
                    ]
                }
            },
        ]
    )
    with _with_fake_client(fake):
        out = suggest_module.suggest("protein", size=3)

    assert len(fake.calls) == 2
    assert out["meta"]["fallback_used"] is True
    assert out["suggestions"][0]["text"] == "Whey Protein Isolate 1kg"
