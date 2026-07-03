"""
Search gateway — always routes to Search V2.

No feature flags, no engine switching, no V1 compatibility layers.
Design: single SearchGateway class with lazy init and a warmup() hook for
gunicorn preload (copy-on-write shared model weights across workers).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

import requests

_log = logging.getLogger("search_gateway")


def _map_filters(params: Dict[str, Any]):
    from search_v2.retrieval.filters import SearchFilters
    return SearchFilters.from_dict(params)


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


def _coerce_vocabulary_payload(payload: Any) -> Dict[str, int]:
    """Validate and coerce API payload into {token: positive_int_frequency}."""
    if not isinstance(payload, dict):
        raise ValueError("vocabulary payload must be a JSON object")

    vocab: Dict[str, int] = {}
    for raw_token, raw_freq in payload.items():
        token = str(raw_token).strip().lower()
        if not token:
            continue
        try:
            freq = int(raw_freq)
        except (TypeError, ValueError):
            continue
        if freq > 0:
            vocab[token] = freq

    if not vocab:
        raise ValueError("vocabulary payload has no valid token-frequency pairs")
    return vocab


def _fetch_vocabulary_from_api(url: str, timeout_sec: float) -> Dict[str, int]:
    """Fetch vocabulary from app-config endpoint."""
    resp = requests.get(url, timeout=timeout_sec)
    resp.raise_for_status()
    return _coerce_vocabulary_payload(resp.json())


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
    from search_v2.ranking.business_ranking import apply_business_ranking
    from search_v2.retrieval.hybrid_search_orchestrator import hybrid_search
    from search_v2.retrieval.opensearch_client import OpenSearchClient

    client = OpenSearchClient(settings=SETTINGS)
    emb_svc = get_embedding_service(SETTINGS.EMBEDDING_MODEL_KEY)

    corrector: Optional[VocabularyCorrector] = None
    if SETTINGS.ENABLE_TYPO_CORRECTION:
        try:
            vocab_source = "seed"
            try:
                vocab = _fetch_vocabulary_from_api(
                    SETTINGS.VOCAB_URL, SETTINGS.VOCAB_TIMEOUT_SEC
                )
                vocab_source = "api"
            except Exception as api_exc:
                _log.warning(
                    "gateway: failed to fetch vocabulary from api (%s); "
                    "falling back to local vocabulary.json",
                    api_exc,
                )
                try:
                    generated = load_vocabulary(VOCABULARY_PATH)
                    if generated:
                        vocab = generated
                        vocab_source = "local"
                    else:
                        vocab = seed_vocabulary()
                        vocab_source = "seed"
                except Exception as local_exc:
                    _log.warning(
                        "gateway: failed to load local vocabulary.json (%s); "
                        "falling back to seed vocabulary",
                        local_exc,
                    )
                    vocab = seed_vocabulary()
                    vocab_source = "seed"

            corrector = VocabularyCorrector(vocab)
            _log.info(
                "gateway: typo corrector loaded from %s (%d terms)",
                vocab_source,
                len(vocab),
            )
        except Exception:
            _log.exception("gateway: failed to build typo corrector — typo correction disabled")

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
            settings=SETTINGS,
        )

        hybrid_result = hybrid_search(
            client, req.processed_query, req.filters, size, SETTINGS, emb_svc
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
