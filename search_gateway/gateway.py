"""
Search gateway — always routes to Search V2.

No feature flags, no engine switching, no V1 compatibility layers.
Design: single SearchGateway class with lazy init and a warmup() hook for
gunicorn preload (copy-on-write shared model weights across workers).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

_log = logging.getLogger("search_gateway")


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
    validates cleanly, atomically overwrite `local_path` with it — the SAME
    file load_vocabulary()/load_product_type_lexicon() read immediately
    after this call, unchanged. Those functions and everything downstream of
    them (VocabularyCorrector, ProductIntentExtractor) never need to know
    whether the file they read came from this fetch or was already there.

    Never raises, and never touches `local_path`, on any failure — no URL
    configured, network error, timeout, non-2xx status, invalid JSON, or a
    payload that fails `validator`. On every one of those, the existing
    local file is left exactly as it was and the caller's existing
    load_*()/seed_vocabulary() fallback runs completely unchanged. There is
    deliberately no fail-open/fail-closed setting here: a fetch that doesn't
    produce a clean result is just "nothing new today," never a startup
    error — gateway startup must never depend on this succeeding.
    """
    if not url:
        return

    import requests

    try:
        resp = requests.get(url, timeout=timeout_sec)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        _log.warning("gateway: failed to fetch %s from %s (%s) — using local file", artifact_label, url, exc)
        return

    validated = validator(payload)
    if validated is None:
        _log.warning("gateway: %s payload from %s failed validation — using local file", artifact_label, url)
        return

    try:
        tmp_path = local_path.with_suffix(local_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(validated), encoding="utf-8")
        os.replace(tmp_path, local_path)
        _log.info("gateway: %s refreshed from %s (%d entries)", artifact_label, url, len(validated))
    except Exception as exc:
        _log.warning("gateway: failed to write fetched %s to %s (%s) — using local file", artifact_label, local_path, exc)


def _to_v1_product(item: Any, rank: int) -> Dict[str, Any]:
    """
    Map a V2 RankedItem to the flat product dict shape that all routes expect.

    Field coverage mirrors V1's _transform_results() plus additional fields
    added by Search V2 (rating alias, image_url alias, nested package_claims,
    nested review_stats, personal-care signals, nutritional_breakdown dict).
    """
    src = item.source or {}
    stats = src.get("stats") or {}

    nutritional = ((src.get("category_data") or {}).get("nutritional") or {})
    nutrition = nutritional.get("nutri_breakdown") or {}

    claims = src.get("package_claims") or {}
    health_claims = claims.get("health_claims") or []
    dietary_labels = claims.get("dietary_labels") or []

    review = src.get("review_stats") or {}
    avg_rating = review.get("avg_rating")

    score_pcts = stats.get("adjusted_score_percentiles") or {}

    images = src.get("images") or []
    image = images[0] if images else None

    bonus_percentiles = {
        "protein":       (stats.get("protein_percentiles") or {}).get("subcategory_percentile"),
        "fiber":         (stats.get("fiber_percentiles") or {}).get("subcategory_percentile"),
        "wholefood":     (stats.get("wholefood_percentiles") or {}).get("subcategory_percentile"),
        "fortification": (stats.get("fortification_percentiles") or {}).get("subcategory_percentile"),
        "simplicity":    (stats.get("simplicity_percentiles") or {}).get("subcategory_percentile"),
    }
    penalty_percentiles = {
        "sugar":         (stats.get("sugar_penalty_percentiles") or {}).get("subcategory_percentile"),
        "sodium":        (stats.get("sodium_penalty_percentiles") or {}).get("subcategory_percentile"),
        "trans_fat":     (stats.get("trans_fat_penalty_percentiles") or {}).get("subcategory_percentile"),
        "saturated_fat": (stats.get("saturated_fat_penalty_percentiles") or {}).get("subcategory_percentile"),
        "oil":           (stats.get("oil_penalty_percentiles") or {}).get("subcategory_percentile"),
        "sweetener":     (stats.get("sweetener_penalty_percentiles") or {}).get("subcategory_percentile"),
        "calories":      (stats.get("calories_penalty_percentiles") or {}).get("subcategory_percentile"),
        "empty_food":    (stats.get("empty_food_penalty_percentiles") or {}).get("subcategory_percentile"),
    }

    return {
        "rank": rank,
        "score": round(item.final_score, 6),
        "id": src.get("id", item.doc_id),
        "name": src.get("name"),
        "brand": src.get("brand"),
        "price": src.get("price"),
        "mrp": src.get("mrp"),
        "category": src.get("category_group"),
        "category_paths": src.get("category_paths") or [],
        "description": src.get("description"),
        # Nutrition flat fields (product_search.py, simple UX)
        "protein_g": nutrition.get("protein_g"),
        "carbs_g": nutrition.get("carbs_g"),
        "fat_g": nutrition.get("fat_g"),
        "fiber_g": nutrition.get("fiber_g"),
        "calories": nutrition.get("energy_kcal"),
        # qty is read by transform_to_product_card() pre-transformed path
        "qty": nutritional.get("qty", ""),
        # Nutrition nested dict (llm_service.py XML prompt)
        "nutritional_breakdown": nutrition,
        "nutritional_qty": nutritional.get("qty", ""),
        # Claims & labels
        "health_claims": health_claims if isinstance(health_claims, list) else [],
        "dietary_labels": dietary_labels if isinstance(dietary_labels, list) else [],
        "package_claims": claims,
        # Quality scores
        "flean_percentile": score_pcts.get("subcategory_percentile"),
        "flean_score": (src.get("flean_score") or {}).get("adjusted_score"),
        "bonus_percentiles": {k: v for k, v in bonus_percentiles.items() if v is not None},
        "penalty_percentiles": {k: v for k, v in penalty_percentiles.items() if v is not None},
        # Image — both field names consumed in ShopBot
        "image": image,
        "image_url": image,
        # Ingredients
        "ingredients": (src.get("ingredients") or {}).get("raw_text"),
        # Reviews
        "avg_rating": avg_rating,
        "total_reviews": review.get("total_reviews"),
        "rating": avg_rating,
        "review_stats": review,
        # Personal-care signals (not in current V2 index ALLOWLIST; parity with V1)
        "skin_compatibility": src.get("skin_compatibility", {}),
        "efficacy": src.get("efficacy", {}),
        "side_effects": src.get("side_effects", {}),
    }


def _build_search() -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """
    Initialise V2 clients once and return a params→dict callable.
    Called lazily on first request or eagerly at warmup().
    """
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
    from search_v2.ranking.business_ranking import apply_business_ranking
    from search_v2.retrieval.hybrid_search_orchestrator import hybrid_search
    from search_v2.retrieval.opensearch_client import OpenSearchClient

    client = OpenSearchClient(settings=SETTINGS)
    emb_svc = get_embedding_service(SETTINGS.EMBEDDING_MODEL_KEY)

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
            _log.info("gateway: typo corrector loaded (%d terms)", len(vocab))
        except Exception:
            _log.exception("gateway: failed to build typo corrector — typo correction disabled")

    # Product Intent Identification — see query_processing/product_intent_extractor.py
    # and indexing/product_type_lexicon_builder.py (search repo). A missing
    # lexicon file (not yet generated by an indexing run) is expected and
    # safe: ProductIntentExtractor(empty dict) resolves nothing, so
    # process_search_request()'s step 4 becomes a complete no-op — identical
    # behavior to before this feature existed, not an error.
    product_intent_extractor: Optional[ProductIntentExtractor] = None
    if SETTINGS.ENABLE_PRODUCT_INTENT:
        try:
            _fetch_and_overwrite_artifact(
                SETTINGS.PRODUCT_TYPE_LEXICON_URL, SETTINGS.ARTIFACT_FETCH_TIMEOUT_SEC,
                PRODUCT_TYPE_LEXICON_PATH, _validate_product_type_lexicon_payload, "product_type_lexicon.json",
            )
            lexicon = load_product_type_lexicon(PRODUCT_TYPE_LEXICON_PATH)
            # Fresh Produce Identification — curated vernacular produce
            # aliases (aam -> mango family, aloo -> potato family, ...)
            # loaded once here from the single JSON source of truth
            # (query_processing/produce_synonyms.json) and flattened into an
            # in-memory {alias: FreshProduceFamily} dict, exactly like the
            # statistical lexicon above. A missing file behaves the same
            # way: an empty dict makes the exact-match check inside
            # ProductIntentExtractor.extract() a permanent no-op.
            produce_aliases = load_produce_alias_map(PRODUCE_SYNONYMS_PATH)
            product_intent_extractor = ProductIntentExtractor(
                lexicon, settings=SETTINGS, produce_aliases=produce_aliases,
            )
            _log.info(
                "gateway: product intent lexicon loaded (%d terms), produce alias map loaded (%d aliases)",
                len(lexicon), len(produce_aliases),
            )
        except Exception:
            _log.exception("gateway: failed to build product intent extractor — feature disabled")

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
        # by SETTINGS.RETRIEVAL_K), NOT just this page — see
        # hybrid_search_orchestrator.py's module docstring. Business ranking
        # (Flean score) needs that full pool to have any real candidates to
        # promote; pagination happens below, AFTER ranking.
        hybrid_result = hybrid_search(
            client, req.processed_query, req.filters, size, SETTINGS, emb_svc
        )

        sort_by = (req.filters.sort_by or "").strip().lower()
        is_explicit_non_relevance_sort = bool(sort_by) and sort_by != "relevance"

        ranked = apply_business_ranking(
            hybrid_result.items,
            subcategory=params.get("subcategory", "_default"),
            settings=SETTINGS,
            # An explicit non-relevance sort (price_asc, protein_desc, ...)
            # was already applied inside hybrid_search() — business ranking
            # must not re-shuffle an order the user explicitly asked for.
            resort=not is_explicit_non_relevance_sort,
            # Exact product-type matches (e.g. real "eggs") are never ranked
            # below documents that only lexically mention the word (e.g.
            # "egg-less rusk") — see apply_business_ranking()'s docstring.
            # None/None (no resolved product_type) is a complete no-op.
            product_type=req.filters.product_type,
            product_type_category=req.filters.product_type_category,
        )

        offset = req.filters.offset or 0
        page_items = ranked[offset: offset + size]
        products = [_to_v1_product(item, rank) for rank, item in enumerate(page_items, 1)]
        took_ms = round((time.monotonic() - t0) * 1000)
        meta: Dict[str, Any] = {
            "total_hits": len(ranked),
            "returned": len(products),
            "query_successful": True,
            "engine": "v2",
            "took_ms": took_ms,
        }
        # Purely additive observability for Product Intent Identification —
        # absent entirely when the feature found nothing, so existing
        # consumers that don't know this key see no change at all.
        if req.product_intent is not None and req.product_intent.primary_product:
            meta["product_intent"] = {
                "primary_product": req.product_intent.primary_product,
                "modifiers": req.product_intent.modifiers,
                "confidence": round(req.product_intent.confidence, 4),
                "tier": req.product_intent.tier,
                "relaxed": hybrid_result.product_intent_relaxed,
                "fresh_produce": bool(req.product_intent.fresh_produce_ids),
            }
        return {"meta": meta, "products": products}

    return _search


class SearchGateway:
    """
    Product search engine — always executes Search V2.

    Drop-in replacement for ElasticsearchProductsFetcher.search() on the
    /rs/api/v1/products/search route. Thread-safe lazy init with a warmup()
    hook for gunicorn --preload: call warmup() in the master process so all
    workers inherit the loaded model via copy-on-write, avoiding N×500 MB.
    """

    def __init__(self) -> None:
        self._fn: Optional[Callable] = None
        self._lock = threading.Lock()

    def _get_fn(self) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
        if self._fn is None:
            with self._lock:
                if self._fn is None:
                    self._fn = _build_search()
        return self._fn

    def warmup(self) -> None:
        """Pre-build V2 pipeline. No-op if already warmed up."""
        try:
            self._get_fn()
            _log.info("gateway.warmup: Search V2 ready")
        except Exception:
            _log.exception("gateway.warmup: init failed — will retry on first request")

    def search(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return self._get_fn()(params)
