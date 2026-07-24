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


def _get_client() -> OpenSearchClient:
    global _client
    if _client is None:
        _client = OpenSearchClient(settings=SETTINGS)
    return _client


def suggest(query: str, size: int = 8, category_group: Optional[str] = None) -> Dict[str, Any]:
    query_text = " ".join((query or "").strip().split())
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

    seen: set = set()
    suggestions: List[Dict[str, Any]] = []
    for option in options:
        text = " ".join(str(option.get("text") or "").split()).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        src = option.get("_source") or {}
        suggestions.append({
            "text": text,
            "type": "product",
            "id": src.get("id"),
            "brand": src.get("brand") or None,
            "category_group": src.get("category_group"),
        })

    return {
        "suggestions": suggestions[:size],
        "meta": {
            "query": query_text,
            "size": size,
            "returned": len(suggestions[:size]),
            "took_ms": took_ms,
            # V1's multi-tier fallback cascade (bool_prefix -> fuzzy -> prefix
            # -> phonetic) has no equivalent here — one completion-suggester
            # call either finds matches or doesn't. Reported as False (never
            # fired, truthfully) rather than omitted, for schema consistency
            # with clients that read these flags. Found missing during the
            # final V1-vs-V2 parity audit.
            "fallback_used": False,
            "fuzzy_fallback_used": False,
            "prefix_fallback_used": False,
            "phonetic_fallback_used": False,
        },
    }
