"""
Native Search V2 brand-name canonicalization.

Wraps search_v2/retrieval/aggregations.py's build_brand_suggest_query()/
parse_brand_suggest_response() — a query builder + parser that already
existed for this exact purpose but was never wired to a callable client
call anywhere (confirmed via repo-wide grep before this module was added).
Matches ElasticsearchProductsFetcher.suggest_brand()'s signature/behavior:
given a noisy hint (e.g. OCR-extracted brand text), return the most
frequent matching real brand value in the index, or None.
"""
from __future__ import annotations

from typing import Optional

from search_v2.config.settings import SETTINGS
from search_v2.retrieval.aggregations import build_brand_suggest_query, parse_brand_suggest_response
from search_v2.retrieval.opensearch_client import OpenSearchClient

_client: Optional[OpenSearchClient] = None


def _get_client() -> OpenSearchClient:
    global _client
    if _client is None:
        _client = OpenSearchClient(settings=SETTINGS)
    return _client


def suggest_brand(brand_hint: str, category_group: Optional[str] = None) -> Optional[str]:
    hint = (brand_hint or "").strip().strip("'\" ")
    if not hint:
        return None
    body = build_brand_suggest_query(hint, category_group=category_group, size=5)
    response = _get_client().search(body)
    return parse_brand_suggest_response(response)
