"""
search_v2/config/settings.py
───────────────────────────────
Single source of truth for every Search V2 feature flag and tunable. Everything
here is overridable via environment variable so capabilities can be turned on/off
or retuned without touching code — required by the brief ("every major capability
should be configurable").

Usage:
    from search_v2.config.settings import SETTINGS
    if SETTINGS.ENABLE_SEMANTIC: ...

All flags default to the recommended production posture for V2 (everything on,
hybrid fusion, business ranking on) — set ENABLE_X=false to disable any one
piece for A/B testing or debugging in the playground.

.env loading: if a `.env` file exists at the search repo root (the directory
directly containing search_v2/), its values are loaded into the process
environment via python-dotenv — but only for variables not already set. Real
environment variables (however they got set — shell export, Docker, CI,
systemd, etc.) always take priority over `.env` file contents; this is
python-dotenv's own default behavior (`override=False`), made explicit below
rather than left implicit. If python-dotenv isn't installed, or no `.env` file
is present, this is a silent no-op — nothing else about config loading changes.
"""
from __future__ import annotations

import os
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

try:
    from dotenv import load_dotenv

    _ENV_FILE = Path(__file__).resolve().parents[2] / ".env"  # search_v2/config/ -> search_v2/ -> repo root
    load_dotenv(dotenv_path=_ENV_FILE, override=False)
except ImportError:
    pass  # python-dotenv not installed — real environment variables still work as before


def _bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _float(name: str, default: float) -> float:
    val = os.getenv(name)
    return float(val) if val is not None else default


def _int(name: str, default: int) -> int:
    val = os.getenv(name)
    return int(val) if val is not None else default


def _str(name: str, default: str) -> str:
    return os.getenv(name, default)


def _list(name: str, default: List[float]) -> List[float]:
    val = os.getenv(name)
    if not val:
        return default
    return [float(x) for x in val.split(",")]


def _json_dict(name: str, default: Dict[str, float]) -> Dict[str, float]:
    val = os.getenv(name)
    if not val:
        return default
    try:
        parsed = json.loads(val)
        return parsed if isinstance(parsed, dict) else default
    except (json.JSONDecodeError, TypeError):
        return default


@dataclass(frozen=True)
class SearchV2Settings:
    # ── Top-level capability switches ──────────────────────────────
    ENABLE_LEXICAL: bool = field(default_factory=lambda: _bool("ENABLE_LEXICAL", True))
    ENABLE_SEMANTIC: bool = field(default_factory=lambda: _bool("ENABLE_SEMANTIC", True))
    ENABLE_VECTOR_SEARCH: bool = field(default_factory=lambda: _bool("ENABLE_VECTOR_SEARCH", True))
    ENABLE_HYBRID: bool = field(default_factory=lambda: _bool("ENABLE_HYBRID", True))
    ENABLE_AUTOCOMPLETE: bool = field(default_factory=lambda: _bool("ENABLE_AUTOCOMPLETE", True))
    ENABLE_SYNONYMS: bool = field(default_factory=lambda: _bool("ENABLE_SYNONYMS", True))
    ENABLE_FUZZY: bool = field(default_factory=lambda: _bool("ENABLE_FUZZY", True))
    # The analysis-phonetic plugin is NOT bundled with vanilla OpenSearch
    # distributions (confirmed against OpenSearch's own docs) — only some
    # managed offerings (e.g. AWS OpenSearch Service) include it. Index
    # creation fails outright with "Unknown filter type [phonetic]" on a
    # cluster that doesn't have it. Default True preserves the originally
    # intended production behavior (assumed available on the AWS OpenSearch
    # target) — set false for local/self-hosted clusters without the plugin.
    # See indexing/mapping_builder.py and indexing/index_v2.py.
    ENABLE_PHONETIC: bool = field(default_factory=lambda: _bool("SEARCH_V2_ENABLE_PHONETIC", True))
    ENABLE_TYPO_CORRECTION: bool = field(default_factory=lambda: _bool("ENABLE_TYPO_CORRECTION", True))
    ENABLE_ROMAN_HINDI_NORMALIZATION: bool = field(default_factory=lambda: _bool("ENABLE_ROMAN_HINDI_NORMALIZATION", True))
    ENABLE_QUERY_EXPANSION: bool = field(default_factory=lambda: _bool("ENABLE_QUERY_EXPANSION", True))
    ENABLE_BUSINESS_RANKING: bool = field(default_factory=lambda: _bool("ENABLE_BUSINESS_RANKING", True))
    ENABLE_RERANKER: bool = field(default_factory=lambda: _bool("ENABLE_RERANKER", True))
    ENABLE_DERIVATIVE_DEMOTION: bool = field(default_factory=lambda: _bool("ENABLE_DERIVATIVE_DEMOTION", True))
    # Leaf-category exact-match boost — fires only when the query text equals
    # a stored `leaf_category` value (e.g. query "watermelon" boosts products
    # with leaf_category="watermelon"). This is the taxonomy-driven commodity
    # detection mechanism: no LLMs, no manually-maintained lists, purely
    # deterministic against the indexed metadata. Disabled by setting boost to
    # 0 or ENABLE_LEAF_CATEGORY_BOOST=false.
    ENABLE_LEAF_CATEGORY_BOOST: bool = field(default_factory=lambda: _bool("SEARCH_V2_ENABLE_LEAF_CATEGORY_BOOST", True))
    LEAF_CATEGORY_COMMODITY_BOOST: float = field(default_factory=lambda: _float("SEARCH_V2_LEAF_CATEGORY_COMMODITY_BOOST", 8.0))

    # ── Cluster / index ──────────────────────────────────────────────
    # SEARCH_V2_ES_URL takes precedence; falls back to ES_URL so that
    # single-cluster local dev requires no extra env configuration.
    # In production, set SEARCH_V2_ES_URL to the dedicated OpenSearch
    # endpoint and leave ES_URL pointing at V1's Elasticsearch cluster.
    ES_URL: str = field(default_factory=lambda: _str("SEARCH_V2_ES_URL", "") or _str("ES_URL", ""))
    ES_API_KEY: str = field(default_factory=lambda: _str("ES_API_KEY", ""))
    INDEX_NAME: str = field(default_factory=lambda: _str("SEARCH_V2_INDEX_NAME", "products-search-v2"))
    HYBRID_PIPELINE_NAME: str = field(default_factory=lambda: _str("SEARCH_V2_PIPELINE_NAME", "search-v2-hybrid-pipeline"))

    # ── Embedding model (see embedding/model_registry.py for the full candidate list) ──
    # Production runtime default is now Bedrock Titan Text Embeddings V2 at
    # 512 dimensions — see embedding/bedrock_embedding_service.py. The local
    # sentence-transformers path (EmbeddingService) only runs when
    # EMBEDDING_BACKEND=local, and only for the gunicorn preload step in
    # shopping_bot/__init__.py — see get_embedding_service()'s docstring.
    EMBEDDING_MODEL_KEY: str = field(default_factory=lambda: _str("SEARCH_V2_EMBEDDING_MODEL", "bge-base-en-v1.5"))
    EMBEDDING_DIM: int = field(default_factory=lambda: _int("SEARCH_V2_EMBEDDING_DIM", 512))
    # "bedrock" (production default) or "local" (sentence-transformers, dev override)
    EMBEDDING_BACKEND: str = field(default_factory=lambda: _str("SEARCH_V2_EMBEDDING_BACKEND", "bedrock"))

    # ── Bedrock (query-embedding calls only — this repo owns runtime query
    # embedding independently of the Search repo's own indexing-time Titan
    # calls; no code is shared between them, only the model/dimension
    # contract). AWS_BEARER_TOKEN_BEDROCK is the SAME env var already read by
    # shopping_bot/config.py's Cfg.AWS_BEARER_TOKEN_BEDROCK for the existing
    # Claude integration — one secret, reused, not duplicated. ────────────
    BEDROCK_REGION: str = field(default_factory=lambda: _str("BEDROCK_REGION", "ap-south-1"))
    BEDROCK_EMBEDDING_MODEL_ID: str = field(default_factory=lambda: _str("BEDROCK_EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0"))

    @property
    def AWS_BEARER_TOKEN_BEDROCK(self) -> str:
        """Read at access time, not import time, matching shopping_bot/config.py's
        own accessor for this exact secret."""
        return os.getenv("AWS_BEARER_TOKEN_BEDROCK", "")

    # ── Semantic confidence floor ────────────────────────────────────────────
    # A minimum raw kNN score a semantic hit must clear to be fed into fusion
    # at all (0 = disabled, the default — no behavior change out of the box).
    # RRF fuses purely on RANK, never looking at the raw score (see
    # fusion.py's module docstring on why) — which means a semantic hit with
    # a genuinely weak/unconfident similarity score still gets full RRF
    # credit for whatever rank position it happened to land in. This knob is
    # the one place that raw score CAN matter: for embeddings normalized to
    # unit length (see embedding_service.py's normalize_embeddings=True) under
    # OpenSearch's "cosinesimil" space, score = (1 + cosine_similarity) / 2,
    # i.e. ~0.5 = orthogonal/unrelated, 1.0 = identical — but this depends on
    # the actual space_type configured on the live index/mapping, which
    # wasn't independently re-verified here. Left at 0 (off) until calibrated
    # against real query/score distributions on the live cluster — turning it
    # on with a wrong guess at the scale risks silently dropping legitimate
    # weak-but-real semantic matches, which is worse than doing nothing.
    SEMANTIC_MIN_SCORE: float = field(default_factory=lambda: _float("SEARCH_V2_SEMANTIC_MIN_SCORE", 0.0))

    # ── Fusion strategy: "rrf" | "weighted" | "native_hybrid" ──────
    # Default is RRF — see retrieval/fusion.py's module docstring for the full
    # reasoning. Short version: rank-based fusion sidesteps the BM25-vs-cosine
    # score-scale mismatch entirely, needs no cluster-side search pipeline, and
    # works on any OpenSearch/Elasticsearch version (the production domain is
    # on OpenSearch 2.15, which lacks native RRF support — that only shipped
    # in 2.19). "native_hybrid" remains available and is what Search V1 used.
    FUSION_STRATEGY: str = field(default_factory=lambda: _str("SEARCH_V2_FUSION_STRATEGY", "rrf"))
    # [lexical_weight, semantic_weight]. Lexical is weighted >= semantic:
    # lexical enforces query-term coverage (see lexical_query_builder.py's
    # minimum_should_match — a product must contain most/all of the query's
    # own words), while kNN/semantic similarity has no equivalent mechanism
    # and can drift toward a merely topically-related product (e.g. "protein
    # chips" pulling toward protein-powder embeddings) even when lexical has
    # correctly identified the literal intended product. Previously semantic
    # was weighted higher (0.55 vs 0.45), letting that drift outrank a
    # correct, fully-covering lexical match in RRF.
    FUSION_WEIGHTS: List[float] = field(default_factory=lambda: _list("SEARCH_V2_FUSION_WEIGHTS", [0.6, 0.4]))
    RRF_RANK_CONSTANT: int = field(default_factory=lambda: _int("SEARCH_V2_RRF_RANK_CONSTANT", 60))

    # ── Retrieval window sizes ──────────────────────────────────────
    RETRIEVAL_K: int = field(default_factory=lambda: _int("SEARCH_V2_RETRIEVAL_K", 75))
    RERANK_TOP_N: int = field(default_factory=lambda: _int("SEARCH_V2_RERANK_TOP_N", 40))
    DEFAULT_RESULT_SIZE: int = field(default_factory=lambda: _int("SEARCH_V2_DEFAULT_RESULT_SIZE", 10))

    # ── Fuzzy / typo ─────────────────────────────────────────────────
    FUZZINESS: str = field(default_factory=lambda: _str("SEARCH_V2_FUZZINESS", "AUTO"))
    TYPO_MAX_EDIT_DISTANCE: int = field(default_factory=lambda: _int("SEARCH_V2_TYPO_MAX_EDIT_DISTANCE", 2))

    # ── Runtime artifact fetch (vocabulary.json / product_type_lexicon.json) ──
    # Optional: fetched once at gateway startup (see gateway.py's _build_search())
    # and, on success, overwritten onto the same local file the existing
    # load_vocabulary()/load_product_type_lexicon() already read — those
    # functions are unchanged. Empty URL = fetch skipped entirely, gateway
    # falls back to whatever is already on the local file, exactly as before
    # this feature existed.
    VOCAB_URL: str = field(default_factory=lambda: _str("SEARCH_V2_VOCAB_URL", ""))
    PRODUCT_TYPE_LEXICON_URL: str = field(default_factory=lambda: _str("SEARCH_V2_PRODUCT_TYPE_LEXICON_URL", ""))
    ARTIFACT_FETCH_TIMEOUT_SEC: float = field(default_factory=lambda: _float("SEARCH_V2_ARTIFACT_FETCH_TIMEOUT_SEC", 2.0))

    # ── Business ranking bounds (see ranking/business_ranking.py) ───
    # Calibrated so business ranking can influence at most ~9 rank positions in either
    # direction (derivation: max_mult = (k+9+1)/(k+1) = 70/61 ≈ 1.148 with k=60).
    # Previous bounds [0.75, 1.35] allowed ~21-position swings, which let high-nutrition
    # packaged products (e.g. protein bars) overtake more-relevant products (e.g. whey
    # proteins) for queries where RRF had already produced the correct ordering — do not
    # widen past here without re-deriving this the same way.
    # [0.90, 1.12] (~8 positions) was previously so tight that flean_nutrition_rule's own
    # base term (spanning 1.0-1.15 pre-widening) saturated against BUSINESS_MAX_MULTIPLIER
    # for any product above roughly the 80th percentile, giving ZERO differentiation among
    # 80th-100th percentile products — widened slightly to [0.85, 1.15] so the Flean signal
    # (now made symmetric and full-range in flean_nutrition_rule) has real headroom to
    # differentiate across the WHOLE percentile range, still comfortably short of the
    # bounds that caused the original incident.
    BUSINESS_MIN_MULTIPLIER: float = field(default_factory=lambda: _float("SEARCH_V2_BUSINESS_MIN_MULTIPLIER", 0.85))
    BUSINESS_MAX_MULTIPLIER: float = field(default_factory=lambda: _float("SEARCH_V2_BUSINESS_MAX_MULTIPLIER", 1.15))

    # ── Business ranking: per-rule weights ───────────────────────────
    # Maps rule function name → scalar weight in [0.0, 1.0].
    # A weight of 0.0 neutralises the rule (multiplies by 1.0) while keeping
    # it in the breakdown for transparency. A weight of 1.0 applies the rule
    # at full strength. Intermediate values scale the rule's deviation from 1.0.
    #
    # Defaults: ratings_rule, review_count_rule, and stock_rule are disabled.
    #   - ratings/review_count: review_stats is not populated in the current
    #     V2 index (sanitize_for_es extracts it but filter_document_for_indexing
    #     strips it — not in ALLOWLIST_INDEX_FIELDS). Both rules return 1.0
    #     on every V2 document — disabling them is architecturally correct and
    #     has no current effect on scores.
    #   - stock_rule: availability is a retrieval FILTER concern, not a ranking
    #     signal. The rule also reads availability.in_stock which doesn't match
    #     V2's actual field path (availability.blinkit.in_stock) — it was
    #     already a silent no-op. Availability filtering belongs in build_filters()
    #     in lexical_query_builder.py via the `in_stock_only` filter key.
    #
    # To restore a rule: SEARCH_V2_BUSINESS_RULE_WEIGHTS='{"stock_rule": 1.0}'
    # To tune partially:  '{"ratings_rule": 0.5}' — applies half the deviation.
    BUSINESS_RULE_WEIGHTS: Dict[str, float] = field(
        default_factory=lambda: _json_dict(
            "SEARCH_V2_BUSINESS_RULE_WEIGHTS",
            {"ratings_rule": 0.0, "review_count_rule": 0.0, "stock_rule": 0.0},
        )
    )

    # ── Derivative-product demotion (apple vs apple juice — see retrieval/derivative_demotion.py) ──
    DERIVATIVE_DEMOTION_FACTOR: float = field(default_factory=lambda: _float("SEARCH_V2_DERIVATIVE_DEMOTION_FACTOR", 0.85))

    # ── Business ranking: category priority boosts (ranking/business_ranking.py) ──
    # JSON object string, e.g. '{"organic": 1.1, "clearance": 0.9}' — category_group
    # or leaf_category values mapped to a small multiplier. Empty by default (no
    # opinion baked in); set via env when the business actually has named priorities.
    CATEGORY_PRIORITY_BOOSTS: Dict[str, float] = field(default_factory=lambda: _json_dict("SEARCH_V2_CATEGORY_PRIORITY_BOOSTS", {}))

    # ── NL filter extraction: macro constraint thresholds ────────────────────
    # Thresholds used by NLFilterExtractor when converting "high protein" or
    # "low sugar" into range filters. Values are per-100g unless otherwise noted.
    # Override via env to tune without code changes.
    MACRO_HIGH_PROTEIN_G: float = field(default_factory=lambda: _float("SEARCH_V2_MACRO_HIGH_PROTEIN_G", 15.0))
    MACRO_LOW_SUGAR_G: float = field(default_factory=lambda: _float("SEARCH_V2_MACRO_LOW_SUGAR_G", 5.0))
    MACRO_LOW_FAT_G: float = field(default_factory=lambda: _float("SEARCH_V2_MACRO_LOW_FAT_G", 3.0))
    MACRO_LOW_CAL_KCAL: float = field(default_factory=lambda: _float("SEARCH_V2_MACRO_LOW_CAL_KCAL", 100.0))
    MACRO_HIGH_FIBER_G: float = field(default_factory=lambda: _float("SEARCH_V2_MACRO_HIGH_FIBER_G", 6.0))
    MACRO_LOW_SODIUM_MG: float = field(default_factory=lambda: _float("SEARCH_V2_MACRO_LOW_SODIUM_MG", 140.0))
    MACRO_LOW_CARBS_G: float = field(default_factory=lambda: _float("SEARCH_V2_MACRO_LOW_CARBS_G", 15.0))

    # ── NL filter extraction: enable flag ────────────────────────────────────
    ENABLE_NL_FILTER_EXTRACTION: bool = field(default_factory=lambda: _bool("SEARCH_V2_ENABLE_NL_FILTERS", True))

    # ── Product Intent Identification ─────────────────────────────────────────
    # See query_processing/product_intent_extractor.py and
    # indexing/product_type_lexicon_builder.py (search repo). Confidence for a
    # resolved head term is a continuous, catalog-derived score in [0, 1]
    # (purity * head_position_ratio * frequency_reliability) — these two
    # thresholds are the only hand-set numbers in the whole mechanism, and
    # they're generic cutoffs applied uniformly to every term, never a
    # per-product/per-phrase special case.
    #   >= HIGH_CONFIDENCE   -> hard-filter retrieval to this product type
    #   [LOW, HIGH)          -> strong should-boost only, nothing excluded
    #   <  LOW_CONFIDENCE    -> ignored entirely, retrieval unchanged
    ENABLE_PRODUCT_INTENT: bool = field(default_factory=lambda: _bool("SEARCH_V2_ENABLE_PRODUCT_INTENT", True))
    PRODUCT_INTENT_HIGH_CONFIDENCE: float = field(default_factory=lambda: _float("SEARCH_V2_PRODUCT_INTENT_HIGH_CONFIDENCE", 0.55))
    PRODUCT_INTENT_LOW_CONFIDENCE: float = field(default_factory=lambda: _float("SEARCH_V2_PRODUCT_INTENT_LOW_CONFIDENCE", 0.25))
    # Safety-net: if a high-confidence hard filter returns zero hits (lexicon
    # miss on an otherwise-valid query), retry once with the filter removed
    # rather than showing an empty page. See hybrid_search_orchestrator.py.
    ENABLE_PRODUCT_INTENT_RELAXATION: bool = field(default_factory=lambda: _bool("SEARCH_V2_ENABLE_PRODUCT_INTENT_RELAXATION", True))
    # Business policy override: when True, a genuinely zero-result
    # product-type-gated query returns ZERO products — ENABLE_PRODUCT_INTENT_
    # RELAXATION is ignored entirely, no retry, no fallback into
    # unrelated/unfiltered results (e.g. "granola bar under 5 calories" — a
    # real, correctly-empty answer — returning chocolate/drinks/oats instead
    # is worse than an empty page for a strict, unambiguous product query).
    # Default False preserves EXACTLY today's relaxation behavior; flip via
    # env with no code change, no reindex, no lexicon change — this only
    # gates the existing runtime relaxation retry in
    # hybrid_search_orchestrator.py, nothing about how filters/confidence/
    # product_type are computed.
    STRICT_ZERO_RESULTS: bool = field(default_factory=lambda: _bool("SEARCH_V2_STRICT_ZERO_RESULTS", False))
    # Retrieval pool ceiling used ONLY when a high-confidence product-type
    # filter is active — decoupled from RETRIEVAL_K so "20 genuinely relevant
    # products" or "100 genuinely relevant products" aren't truncated at the
    # ordinary 75-candidate floor. Unfiltered/ambiguous queries are completely
    # unaffected (still governed by RETRIEVAL_K as before).
    PRODUCT_INTENT_MAX_POOL_SIZE: int = field(default_factory=lambda: _int("SEARCH_V2_PRODUCT_INTENT_MAX_POOL_SIZE", 300))


SETTINGS = SearchV2Settings()


def reload_settings() -> SearchV2Settings:
    """Re-read env vars into a fresh settings object — useful in the playground
    when a developer flips a toggle and wants it to take effect without
    restarting the process. Does NOT mutate the module-level SETTINGS singleton;
    callers (e.g. the playground backend) should hold their own reference."""
    return SearchV2Settings()
