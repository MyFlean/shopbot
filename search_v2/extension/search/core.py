"""
Native Search V2 query-driven search — the plain-function replacement for
search_gateway/gateway.py's SearchGateway class.

Same pipeline (query processing -> hybrid retrieval -> business ranking ->
pagination -> dynamic filters), reorganized as module-level lazy singletons
(matching every other search_v2/extension/* module) instead of a class with
a closure factory. No feature flags, no V1 compatibility layer: this module
IS Search V2's query search, unconditionally.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from shopping_bot.data_fetchers.dynamic_search_filters import (
    build_dynamic_price_ranges,
    build_facet_aggregations,
    build_price_bounds_aggregation,
    parse_dynamic_filters_from_aggs,
)
from search_v2.extension.product import to_product_card

_log = logging.getLogger("search_v2.extension.search")

_client = None
_emb_svc = None
_corrector = None
_product_intent_extractor = None
_search_fn: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
_lock = threading.Lock()


def _map_filters(params: Dict[str, Any]):
    from search_v2.retrieval.filters import SearchFilters
    return SearchFilters.from_dict(params)


def _validate_vocabulary_payload(payload: Any) -> Optional[Dict[str, int]]:
    """Return a cleaned {token: frequency} dict if `payload` looks like a
    real vocabulary (mirrors what load_vocabulary()/seed_vocabulary() already
    produce), else None. Never raises."""
    if not isinstance(payload, dict) or not payload:
        return None
    vocab: Dict[str, int] = {}
    for token, freq in payload.items():
        if not isinstance(token, str) or not token.strip():
            return None
        try:
            vocab[token] = int(freq)
        except (TypeError, ValueError):
            return None
    return vocab


def _validate_product_type_lexicon_payload(payload: Any) -> Optional[Dict[str, Any]]:
    """Return `payload` unchanged if it looks like a real
    {term: {confidence, ...}} lexicon, else None. Never raises."""
    if not isinstance(payload, dict) or not payload:
        return None
    for term, entry in payload.items():
        if not isinstance(term, str) or not isinstance(entry, dict) or "confidence" not in entry:
            return None
    return payload


def _fetch_and_overwrite_artifact(
    url: str,
    timeout_sec: float,
    local_path: Path,
    validator: Callable[[Any], Optional[Any]],
    artifact_label: str,
) -> None:
    """
    Fetch `artifact_label`'s JSON from `url` and, if it downloads and
    validates cleanly, atomically overwrite `local_path` with it. Never
    raises, and never touches `local_path`, on any failure — a fetch that
    doesn't produce a clean result is just "nothing new today," never a
    startup error.
    """
    if not url:
        return

    import requests

    try:
        resp = requests.get(url, timeout=timeout_sec)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        _log.warning("search: failed to fetch %s from %s (%s) — using local file", artifact_label, url, exc)
        return

    validated = validator(payload)
    if validated is None:
        _log.warning("search: %s payload from %s failed validation — using local file", artifact_label, url)
        return

    try:
        tmp_path = local_path.with_suffix(local_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(validated), encoding="utf-8")
        os.replace(tmp_path, local_path)
        _log.info("search: %s refreshed from %s (%d entries)", artifact_label, url, len(validated))
    except Exception as exc:
        _log.warning("search: failed to write fetched %s to %s (%s) — using local file", artifact_label, local_path, exc)


def _build_search() -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """Initialise V2 clients once and return a params->dict callable."""
    from search_v2.config.settings import SETTINGS
    from search_v2.embedding.embedding_service import get_embedding_service
    from search_v2.query_processing.query_pipeline import process_search_request
    from search_v2.query_processing.typo_correction import VocabularyCorrector
    from search_v2.query_processing.vocabulary_builder import (
        VOCABULARY_PATH, load_vocabulary, seed_vocabulary,
    )
    from search_v2.query_processing.product_intent_extractor import (
        PRODUCT_TYPE_LEXICON_PATH, ProductIntentExtractor, load_product_type_lexicon,
    )
    from search_v2.query_processing.canonical_produce import (
        PRODUCE_SYNONYMS_PATH, load_produce_alias_map,
    )
    from search_v2.ranking.business_ranking import apply_business_ranking, finalize_search_ranking
    from search_v2.retrieval.hybrid_search_orchestrator import hybrid_search
    from search_v2.retrieval import lexical_query_builder
    from search_v2.retrieval.opensearch_client import OpenSearchClient

    client = OpenSearchClient(settings=SETTINGS)
    try:
        client.search({"size": 0, "query": {"match_all": {}}})
    except Exception:
        _log.exception("search: OpenSearch warmup call failed")
    # No explicit model_key here, deliberately: get_embedding_service() picks
    # Bedrock Titan vs. the local sentence-transformers path based on
    # SETTINGS.EMBEDDING_BACKEND.
    emb_svc = get_embedding_service()
    try:
        emb_svc.embed_query("warmup")
    except Exception:
        _log.exception("search: embedding warmup call failed")

    corrector: Optional[VocabularyCorrector] = None
    if SETTINGS.ENABLE_TYPO_CORRECTION:
        try:
            _fetch_and_overwrite_artifact(
                SETTINGS.VOCAB_URL, SETTINGS.ARTIFACT_FETCH_TIMEOUT_SEC,
                VOCABULARY_PATH, _validate_vocabulary_payload, "vocabulary.json",
            )
            generated = load_vocabulary(VOCABULARY_PATH)
            vocab = generated if generated else seed_vocabulary()
            corrector = VocabularyCorrector(vocab)
            _log.info("search: vocabulary corrector loaded (%d terms)", len(vocab))
        except Exception:
            _log.exception("search: failed to build typo corrector — typo correction disabled")

    product_intent_extractor: Optional[ProductIntentExtractor] = None
    if SETTINGS.ENABLE_PRODUCT_INTENT:
        try:
            _fetch_and_overwrite_artifact(
                SETTINGS.PRODUCT_TYPE_LEXICON_URL, SETTINGS.ARTIFACT_FETCH_TIMEOUT_SEC,
                PRODUCT_TYPE_LEXICON_PATH, _validate_product_type_lexicon_payload, "product_type_lexicon.json",
            )
            lexicon = load_product_type_lexicon(PRODUCT_TYPE_LEXICON_PATH)
            produce_aliases = load_produce_alias_map(PRODUCE_SYNONYMS_PATH)
            product_intent_extractor = ProductIntentExtractor(
                lexicon, settings=SETTINGS, produce_aliases=produce_aliases,
            )
            _log.info(
                "search: product intent lexicon loaded (%d terms), produce alias map loaded (%d aliases)",
                len(lexicon), len(produce_aliases),
            )
        except Exception:
            _log.exception("search: failed to build product intent extractor — feature disabled")

    def _search(params: Dict[str, Any]) -> Dict[str, Any]:
        t0 = time.monotonic()
        raw_q = params.get("q") or ""
        size = int(params.get("size") or SETTINGS.DEFAULT_RESULT_SIZE)

        explicit_filters = _map_filters(params)

        req = process_search_request(
            raw_q,
            explicit_filters=explicit_filters,
            corrector=corrector,
            enable_typo_correction=SETTINGS.ENABLE_TYPO_CORRECTION,
            product_intent_extractor=product_intent_extractor,
            settings=SETTINGS,
        )

        # hybrid_search() returns the FULL retrieval candidate pool (bounded
        # by SETTINGS.RETRIEVAL_K), NOT just this page — business ranking
        # needs that full pool to have real candidates to promote;
        # pagination happens below, after ranking.
        hybrid_result = hybrid_search(
            client, req.processed_query, req.filters, size, SETTINGS, emb_svc,
            routing_context=req.routing_context,
        )

        sort_by = (req.filters.sort_by or "").strip().lower()
        is_explicit_non_relevance_sort = bool(sort_by) and sort_by != "relevance"

        ranked = apply_business_ranking(
            hybrid_result.items,
            subcategory=params.get("subcategory", "_default"),
            settings=SETTINGS,
            resort=not is_explicit_non_relevance_sort,
            product_type=req.filters.product_type,
            product_type_category=req.filters.product_type_category,
            health_intent=req.health_intent,
        )
        ranked = finalize_search_ranking(
            ranked,
            product_type=req.filters.product_type,
            product_type_category=req.filters.product_type_category,
            promote_lab=not is_explicit_non_relevance_sort,
        )

        offset = req.filters.offset or 0

        page_items = ranked[offset: offset + size]
        products = [
            to_product_card(item.source or {}, rank=rank, score=item.final_score)
            for rank, item in enumerate(page_items, 1)
        ]
        took_ms = round((time.monotonic() - t0) * 1000)
        meta: Dict[str, Any] = {
            "total_hits": len(ranked),
            "returned": len(products),
            "query_successful": True,
            "engine": "v2",
            "took_ms": took_ms,
        }
        if req.product_intent is not None and req.product_intent.primary_product:
            meta["product_intent"] = {
                "primary_product": req.product_intent.primary_product,
                "modifiers": req.product_intent.modifiers,
                "confidence": round(req.product_intent.confidence, 4),
                "tier": req.product_intent.tier,
                "relaxed": hybrid_result.product_intent_relaxed,
                "fresh_produce": bool(req.product_intent.fresh_produce_ids),
            }
        if req.health_intent is not None and req.health_intent.detected:
            meta["health_intent"] = {
                "categories": list(req.health_intent.categories),
                "matched_phrases": list(req.health_intent.matched_phrases),
                "primary_preferences": list(req.health_intent.primary_preferences),
                "secondary_preferences": list(req.health_intent.secondary_preferences),
            }
        if req.routing_context is not None:
            meta["routing"] = {
                "product_intent_source": req.routing_context.product_intent_source,
                "product_intent_confidence": round(req.routing_context.product_intent_confidence, 4),
                "is_compound": req.routing_context.product_intent_is_compound,
                "health_intent_detected": req.routing_context.health_intent_detected,
                "decision": "LEXICAL_ONLY" if hybrid_result.fallback_reason == "query_router: LEXICAL_ONLY" else "HYBRID",
            }

        dynamic_filters = []
        try:
            facet_query_body = lexical_query_builder.build_query(
                req.processed_query,
                filters=req.filters,
                size=0,
                settings=SETTINGS,
                sort_by=None,
                offset=0,
            )
            facet_query = facet_query_body.get("query", {"match_all": {}})

            bounds_req = {
                "size": 0,
                "track_total_hits": False,
                "query": facet_query,
                "aggs": build_price_bounds_aggregation(),
            }
            bounds_resp = client.search(bounds_req)
            bounds_aggs = (bounds_resp.get("aggregations") or {})
            price_min_raw = (bounds_aggs.get("price_min") or {}).get("value")
            price_max_raw = (bounds_aggs.get("price_max") or {}).get("value")

            price_min = float(price_min_raw) if isinstance(price_min_raw, (int, float)) else None
            price_max = float(price_max_raw) if isinstance(price_max_raw, (int, float)) else None
            price_ranges = build_dynamic_price_ranges(price_min, price_max, target_buckets=4)
            facets_aggs = build_facet_aggregations(price_ranges=price_ranges)

            facets_req = {
                "size": 0,
                "track_total_hits": False,
                "query": facet_query,
                "aggs": facets_aggs,
            }
            facets_resp = client.search(facets_req)
            facets_aggs_out = facets_resp.get("aggregations") or {}
            dynamic_filters = parse_dynamic_filters_from_aggs(facets_aggs_out)
        except Exception as exc:
            _log.warning("search: failed to compute dynamic search filters (%s)", exc)

        return {"meta": meta, "products": products, "filters": dynamic_filters}

    return _search


def _get_search_fn() -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    global _search_fn
    if _search_fn is None:
        with _lock:
            if _search_fn is None:
                _search_fn = _build_search()
    return _search_fn


def warmup() -> None:
    """Pre-build the V2 search pipeline (models, vocab, lexicon). No-op if
    already warmed up. Safe to call from a background thread at startup."""
    try:
        _get_search_fn()
        _log.info("search.warmup: Search V2 ready")
    except Exception:
        _log.exception("search.warmup: init failed — will retry on first request")


def search(params: Dict[str, Any]) -> Dict[str, Any]:
    return _get_search_fn()(params)
