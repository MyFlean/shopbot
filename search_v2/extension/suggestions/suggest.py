"""
Native Search V2 autocomplete/suggestions.

Completion-suggester query against name_suggest, built once against a
lazily-created, process-level OpenSearch client — no gateway class.
Replaces shopping_bot/data_fetchers/es_products.py's search_suggestions().
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from search_v2.config.settings import SETTINGS
from search_v2.retrieval.lexical_query_builder import build_suggest_query
from search_v2.retrieval.opensearch_client import OpenSearchClient

_client: Optional[OpenSearchClient] = None
_FALLBACK_MIN_RESULTS = 3
_FALLBACK_SOURCE_FIELDS = ["name", "id", "brand", "category_group"]


def _get_client() -> OpenSearchClient:
    global _client
    if _client is None:
        _client = OpenSearchClient(settings=SETTINGS)
    return _client


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _suggestion_item(text: str, src: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "text": text,
        "type": "product",
        "id": src.get("id"),
        "brand": src.get("brand") or None,
        "category_group": src.get("category_group"),
    }


def _extract_completion_suggestions(options: List[Dict[str, Any]], seen: set[str]) -> List[Dict[str, Any]]:
    suggestions: List[Dict[str, Any]] = []
    for option in options:
        text = _normalize_text(option.get("text"))
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        src = option.get("_source") or {}
        suggestions.append(_suggestion_item(text=text, src=src))
    return suggestions


def _has_token_match(query_text: str, suggestions: List[Dict[str, Any]]) -> bool:
    compact_query = query_text.casefold()
    if " " in compact_query or len(compact_query) < 3:
        return False
    for item in suggestions[:5]:
        text = _normalize_text(item.get("text")).casefold()
        brand = _normalize_text(item.get("brand")).casefold()
        if compact_query in text or compact_query in brand:
            return True
    return False


def _should_use_lexical_fallback(query_text: str, suggestions: List[Dict[str, Any]], size: int) -> bool:
    if not suggestions:
        return True

    if len(suggestions) < min(size, _FALLBACK_MIN_RESULTS):
        return True

    # Completion can return lexical-neighbour noise for short supplement queries
    # (e.g. "bcca"). If none of the top suggestions contain the typed token,
    # run a lexical+fuzzy fallback to recover in-token matches.
    return not _has_token_match(query_text, suggestions)


def _build_fallback_query(query_text: str, *, size: int, category_group: Optional[str]) -> Dict[str, Any]:
    should_clauses: List[Dict[str, Any]] = [
        {
            "multi_match": {
                "query": query_text,
                "type": "bool_prefix",
                "fields": [
                    "name^4",
                    "name._2gram^2.6",
                    "name._3gram^1.9",
                    "brand^2.2",
                    "brand._2gram^1.4",
                    "brand._3gram^1.1",
                ],
                "boost": 2.8,
            }
        },
        {
            "multi_match": {
                "query": query_text,
                "fields": ["name^4", "brand^2"],
                "type": "best_fields",
                "operator": "or",
                "fuzziness": "AUTO",
                "prefix_length": 1,
                "max_expansions": 25,
                "boost": 2.0,
            }
        },
        {
            "match_phrase_prefix": {
                "name": {
                    "query": query_text,
                    "boost": 1.8,
                    "max_expansions": 20,
                }
            }
        },
        {
            "match": {
                "name": {
                    "query": query_text,
                    "operator": "or",
                    "fuzziness": "AUTO",
                    "prefix_length": 1,
                    "max_expansions": 50,
                    "boost": 1.4,
                }
            }
        },
        {
            "match": {
                "brand": {
                    "query": query_text,
                    "operator": "or",
                    "fuzziness": "AUTO",
                    "prefix_length": 1,
                    "max_expansions": 30,
                    "boost": 1.1,
                }
            }
        },
    ]

    bool_query: Dict[str, Any] = {"should": should_clauses, "minimum_should_match": 1}
    if category_group:
        bool_query["filter"] = [{"term": {"category_group": category_group}}]

    return {
        "size": min(max(size * 4, 12), 100),
        "track_total_hits": False,
        "_source": {"includes": list(_FALLBACK_SOURCE_FIELDS)},
        "query": {"bool": bool_query},
    }


def _append_fallback_suggestions(
    query_text: str,
    suggestions: List[Dict[str, Any]],
    seen: set[str],
    *,
    size: int,
    category_group: Optional[str],
) -> int:
    fallback_body = _build_fallback_query(query_text, size=size, category_group=category_group)
    t0 = time.monotonic()
    response = _get_client().search(fallback_body)
    took_ms = round((time.monotonic() - t0) * 1000)

    hits = ((response.get("hits") or {}).get("hits") or [])
    for hit in hits:
        src = hit.get("_source") or {}
        text = _normalize_text(src.get("name"))
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        suggestions.append(_suggestion_item(text=text, src=src))
        if len(suggestions) >= size:
            break
    return took_ms


def _merge_suggestions_with_preferred_order(
    primary: List[Dict[str, Any]],
    secondary: List[Dict[str, Any]],
    *,
    size: int,
) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for source in (primary, secondary):
        for item in source:
            text = _normalize_text(item.get("text"))
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
            if len(merged) >= size:
                return merged
    return merged


def _token_priority(item: Dict[str, Any], query_text: str) -> tuple[int, int]:
    token = query_text.casefold()
    text = _normalize_text(item.get("text")).casefold()
    brand = _normalize_text(item.get("brand")).casefold()
    text_contains = 1 if token and token in text else 0
    brand_contains = 1 if token and token in brand else 0
    return (text_contains, brand_contains)


def suggest(query: str, size: int = 8, category_group: Optional[str] = None) -> Dict[str, Any]:
    query_text = _normalize_text(query)
    if not query_text:
        return {"suggestions": [], "meta": {"query": "", "size": 0, "returned": 0}}

    size = max(1, min(int(size), 100))
    body = build_suggest_query(query_text, category_group=category_group, size=size)

    t0 = time.monotonic()
    response = _get_client().search(body)
    took_ms = round((time.monotonic() - t0) * 1000)

    options = (
        (response.get("suggest") or {}).get("name_suggest", [{}])[0].get("options", [])
        if response.get("suggest")
        else []
    )

    seen: set[str] = set()
    suggestions = _extract_completion_suggestions(options, seen)

    completion_has_token_match = _has_token_match(query_text, suggestions)
    fallback_used = _should_use_lexical_fallback(query_text, suggestions, size)
    fuzzy_fallback_used = False
    prefix_fallback_used = False
    phonetic_fallback_used = False
    if fallback_used:
        completion_suggestions = list(suggestions)
        took_ms += _append_fallback_suggestions(
            query_text,
            suggestions,
            seen,
            size=size,
            category_group=category_group,
        )
        fuzzy_fallback_used = True
        prefix_fallback_used = True
        fallback_suggestions = [item for item in suggestions if item not in completion_suggestions]
        if not completion_has_token_match and fallback_suggestions:
            # When completion misses the token entirely (e.g. "eaa"), promote
            # lexical-fallback hits ahead of noisy completion candidates.
            fallback_suggestions.sort(
                key=lambda item: _token_priority(item, query_text),
                reverse=True,
            )
            suggestions = _merge_suggestions_with_preferred_order(
                fallback_suggestions,
                completion_suggestions,
                size=size,
            )

    return {
        "suggestions": suggestions[:size],
        "meta": {
            "query": query_text,
            "size": size,
            "returned": len(suggestions[:size]),
            "took_ms": took_ms,
            "fallback_used": fallback_used,
            "fuzzy_fallback_used": fuzzy_fallback_used,
            "prefix_fallback_used": prefix_fallback_used,
            "phonetic_fallback_used": phonetic_fallback_used,
        },
    }
