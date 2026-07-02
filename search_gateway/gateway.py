"""
Search gateway — routes queries to V1 or V2 based on SEARCH_ENGINE env var.

Env vars:
  SEARCH_ENGINE              v1 | v2 | auto   (default: auto)
  SEARCH_GATEWAY_MIN_RESULTS minimum number of products V2 must return before
                             a fallback to V1 is triggered; default 1 (any
                             non-empty result avoids fallback). Raise this
                             during rollout for a more conservative stance and
                             lower it later without touching code.

Routing semantics:
  v1   — always V1, V2 never executes.
  v2   — always V2, exceptions propagate to the caller.
  auto — execute V2 first; fall back to V1 if:
           • V2 raises any exception, OR
           • V2 returns fewer than SEARCH_GATEWAY_MIN_RESULTS products.
         Only ONE engine executes per request.

Wiring (in your application factory):
  from shopping_bot.data_fetchers.es_products import get_es_fetcher, get_search_gateway
  gateway = get_search_gateway()         # singleton, created once per process
  results = gateway.search(params)       # same params dict as fetcher.search today
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Optional

_log = logging.getLogger("search_gateway")


# ── V2 → V1 response shape helpers ───────────────────────────────────────────

def _map_filters(params: Dict[str, Any]):
    """Convert V1 param dict to a SearchFilters object for V2."""
    from search_v2.retrieval.filters import SearchFilters
    return SearchFilters.from_dict(params)


def _to_v1_product(item: Any, rank: int) -> Dict[str, Any]:
    """
    Map a V2 RankedItem to the flat product dict shape that V1 returns and
    that all ShopBot consumers (llm_service, bot_core, routes) expect.

    Field coverage mirrors V1's _transform_results() plus additional fields
    that V1 was missing (rating alias, image_url alias, nested package_claims,
    nested review_stats, personal-care signals, nutritional_breakdown dict).
    """
    src = item.source or {}
    stats = src.get("stats") or {}

    # Nutritional: flat fields and full breakdown dict
    nutritional = ((src.get("category_data") or {}).get("nutritional") or {})
    nutrition = nutritional.get("nutri_breakdown") or {}

    # Claims
    claims = src.get("package_claims") or {}
    health_claims = claims.get("health_claims") or []
    dietary_labels = claims.get("dietary_labels") or []

    # Reviews
    review = src.get("review_stats") or {}
    avg_rating = review.get("avg_rating")

    # Quality percentile
    score_pcts = stats.get("adjusted_score_percentiles") or {}

    # Image — prefer first element from images array
    images = src.get("images") or []
    image = images[0] if images else None

    # Bonus percentiles (positive nutrition signals)
    bonus_percentiles = {
        "protein":       (stats.get("protein_percentiles") or {}).get("subcategory_percentile"),
        "fiber":         (stats.get("fiber_percentiles") or {}).get("subcategory_percentile"),
        "wholefood":     (stats.get("wholefood_percentiles") or {}).get("subcategory_percentile"),
        "fortification": (stats.get("fortification_percentiles") or {}).get("subcategory_percentile"),
        "simplicity":    (stats.get("simplicity_percentiles") or {}).get("subcategory_percentile"),
    }
    # Penalty percentiles (negative nutrition signals)
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

        # ── Nutrition ──────────────────────────────────────────────────
        # Flat fields (consumed by product_search.py and simple UX)
        "protein_g": nutrition.get("protein_g"),
        "carbs_g": nutrition.get("carbs_g"),
        "fat_g": nutrition.get("fat_g"),
        "calories": nutrition.get("energy_kcal"),
        # Full breakdown dict (consumed by llm_service.py XML prompt)
        "nutritional_breakdown": nutrition,
        # Serving size (consumed by llm_service.py serving_size XML tag)
        "nutritional_qty": nutritional.get("qty", ""),

        # ── Claims & labels ────────────────────────────────────────────
        # Flat (consumed by product_search.py and simple UX)
        "health_claims": health_claims if isinstance(health_claims, list) else [],
        "dietary_labels": dietary_labels if isinstance(dietary_labels, list) else [],
        # Nested (consumed by llm_service.py personal-care product briefs)
        "package_claims": claims,

        # ── Quality scores ─────────────────────────────────────────────
        "flean_percentile": score_pcts.get("subcategory_percentile"),
        "flean_score": (src.get("flean_score") or {}).get("adjusted_score"),
        "bonus_percentiles": {k: v for k, v in bonus_percentiles.items() if v is not None},
        "penalty_percentiles": {k: v for k, v in penalty_percentiles.items() if v is not None},

        # ── Image ──────────────────────────────────────────────────────
        # Both field names are consumed in ShopBot
        "image": image,
        "image_url": image,

        # ── Ingredients ────────────────────────────────────────────────
        "ingredients": (src.get("ingredients") or {}).get("raw_text"),

        # ── Reviews ────────────────────────────────────────────────────
        # Flat fields (consumed by product_search.py)
        "avg_rating": avg_rating,
        "total_reviews": review.get("total_reviews"),
        # Alias used by llm_service.py and bot_core.py
        "rating": avg_rating,
        # Nested dict (consumed by llm_service.py personal-care briefs)
        "review_stats": review,

        # ── Personal-care signals ──────────────────────────────────────
        # Not in V2 ALLOWLIST_INDEX_FIELDS → source is empty; parity with V1.
        # Use .get(key, {}) not (get(key) or {}) — the `or` form collapses
        # falsy values like [] to {}, corrupting typed list data.
        "skin_compatibility": src.get("skin_compatibility", {}),
        "efficacy": src.get("efficacy", {}),
        "side_effects": src.get("side_effects", {}),
    }


def _build_v2_search() -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """
    Initialise V2 clients once and return a params→dict callable.
    Called lazily — never invoked when SEARCH_ENGINE=v1.

    The gateway's only responsibilities are:
      1. Map request fields to SearchFilters (structural translation, no NLP).
      2. Call the Search V2 pipeline (which handles all query understanding).
      3. Map V2 results back to the V1 response shape.
    """
    from search_v2.config.settings import SETTINGS
    from search_v2.embedding.embedding_service import get_embedding_service
    from search_v2.query_processing.query_pipeline import process_search_request
    from search_v2.query_processing.typo_correction import VocabularyCorrector
    from search_v2.query_processing.vocabulary_builder import (
        VOCABULARY_PATH, load_vocabulary, seed_vocabulary,
    )
    from search_v2.ranking.business_ranking import apply_business_ranking
    from search_v2.retrieval.hybrid_search_orchestrator import hybrid_search
    from search_v2.retrieval.opensearch_client import OpenSearchClient

    client = OpenSearchClient(settings=SETTINGS)
    emb_svc = get_embedding_service(SETTINGS.EMBEDDING_MODEL_KEY)

    # Build the typo corrector once — same pattern as playground/cli.py _build_corrector().
    # Prefers the catalogue-derived vocabulary.json if present; falls back to seed terms.
    corrector: Optional[VocabularyCorrector] = None
    if SETTINGS.ENABLE_TYPO_CORRECTION:
        try:
            generated = load_vocabulary(VOCABULARY_PATH)
            vocab = generated if generated else seed_vocabulary()
            corrector = VocabularyCorrector(vocab)
            _log.info("gateway: typo corrector loaded (%d terms)", len(vocab))
        except Exception:
            _log.exception("gateway: failed to build typo corrector — typo correction disabled")

    def _search(params: Dict[str, Any]) -> Dict[str, Any]:
        t0 = time.monotonic()
        raw_q = params.get("q") or ""
        size = int(params.get("size") or SETTINGS.DEFAULT_RESULT_SIZE)

        # Structured filters from the caller — no NLP here
        explicit_filters = _map_filters(params)

        # Full query-processing pipeline: NL extraction → typo correction → SearchRequest
        request = process_search_request(
            raw_q,
            explicit_filters=explicit_filters,
            corrector=corrector,
            enable_typo_correction=SETTINGS.ENABLE_TYPO_CORRECTION,
            settings=SETTINGS,
        )

        hybrid_result = hybrid_search(
            client, request.processed_query, request.filters, size, SETTINGS, emb_svc
        )
        ranked = apply_business_ranking(
            hybrid_result.items,
            subcategory=params.get("subcategory", "_default"),
            settings=SETTINGS,
        )
        products = [_to_v1_product(item, rank) for rank, item in enumerate(ranked, 1)]
        took_ms = round((time.monotonic() - t0) * 1000)
        return {
            "meta": {
                "total_hits": len(products),
                "returned": len(products),
                "query_successful": True,
                "engine": "v2",
                "took_ms": took_ms,
            },
            "products": products,
        }

    return _search


# ── Gateway ───────────────────────────────────────────────────────────────────

class SearchGateway:
    """
    Drop-in replacement for ElasticsearchProductsFetcher.search().

    Routing:
      SEARCH_ENGINE=v1   → always V1.
      SEARCH_ENGINE=v2   → always V2 (exceptions propagate).
      SEARCH_ENGINE=auto → V2 first; fall back to V1 if V2 raises an exception
                           OR returns fewer than SEARCH_GATEWAY_MIN_RESULTS
                           products. Only one engine executes per request.
    """

    def __init__(
        self,
        v1_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
        v2_fn: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> None:
        self._v1 = v1_fn
        self._v2_override = v2_fn        # caller-supplied (useful for testing)
        self._v2: Optional[Callable] = None
        self._v2_lock = threading.Lock()  # guards lazy init of _v2

        self._engine = os.getenv("SEARCH_ENGINE", "auto").lower()
        self._min_results = int(os.getenv("SEARCH_GATEWAY_MIN_RESULTS", "1"))

    # ── Internal ─────────────────────────────────────────────────────────────

    def _get_v2(self) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
        """Return the V2 search callable, building it lazily (thread-safe)."""
        if self._v2_override is not None:
            return self._v2_override
        if self._v2 is None:
            with self._v2_lock:
                if self._v2 is None:     # second check inside lock
                    self._v2 = _build_v2_search()
        return self._v2

    # ── Public ───────────────────────────────────────────────────────────────

    def warmup(self) -> None:
        """
        Pre-initialise V2 clients and load the embedding model.
        Call during application startup so the first user request is not
        penalised by model load time. No-op when SEARCH_ENGINE=v1.
        """
        if self._engine == "v1":
            return
        try:
            self._get_v2()
            _log.info("gateway.warmup: V2 clients ready")
        except Exception:
            _log.exception("gateway.warmup: V2 init failed — will retry on first request")

    def search(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute search and return a V1-shaped response dict."""

        if self._engine == "v1":
            return self._v1(params)

        if self._engine == "v2":
            # Pure V2 — exceptions propagate to the caller
            return self._get_v2()(params)

        # ── auto mode: V2 first, sequential fallback to V1 ──────────────────
        q = params.get("q", "")

        try:
            result = self._get_v2()(params)
        except Exception as exc:
            _log.warning(
                "gateway: v2_error → v1 | q=%r | reason=exception | error=%s",
                q, exc,
            )
            return self._v1(params)

        n = len(result.get("products") or [])
        if n < self._min_results:
            _log.info(
                "gateway: v2_insufficient → v1 | q=%r | returned=%d | min=%d",
                q, n, self._min_results,
            )
            return self._v1(params)

        _log.info("gateway: v2 | q=%r | returned=%d", q, n)
        return result
