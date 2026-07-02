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
    EMBEDDING_MODEL_KEY: str = field(default_factory=lambda: _str("SEARCH_V2_EMBEDDING_MODEL", "bge-base-en-v1.5"))
    EMBEDDING_DIM: int = field(default_factory=lambda: _int("SEARCH_V2_EMBEDDING_DIM", 768))

    # ── Fusion strategy: "rrf" | "weighted" | "native_hybrid" ──────
    # Default is RRF — see retrieval/fusion.py's module docstring for the full
    # reasoning. Short version: rank-based fusion sidesteps the BM25-vs-cosine
    # score-scale mismatch entirely, needs no cluster-side search pipeline, and
    # works on any OpenSearch/Elasticsearch version (the production domain is
    # on OpenSearch 2.15, which lacks native RRF support — that only shipped
    # in 2.19). "native_hybrid" remains available and is what Search V1 used.
    FUSION_STRATEGY: str = field(default_factory=lambda: _str("SEARCH_V2_FUSION_STRATEGY", "rrf"))
    FUSION_WEIGHTS: List[float] = field(default_factory=lambda: _list("SEARCH_V2_FUSION_WEIGHTS", [0.45, 0.55]))
    RRF_RANK_CONSTANT: int = field(default_factory=lambda: _int("SEARCH_V2_RRF_RANK_CONSTANT", 60))

    # ── Retrieval window sizes ──────────────────────────────────────
    RETRIEVAL_K: int = field(default_factory=lambda: _int("SEARCH_V2_RETRIEVAL_K", 75))
    RERANK_TOP_N: int = field(default_factory=lambda: _int("SEARCH_V2_RERANK_TOP_N", 40))
    DEFAULT_RESULT_SIZE: int = field(default_factory=lambda: _int("SEARCH_V2_DEFAULT_RESULT_SIZE", 10))

    # ── Fuzzy / typo ─────────────────────────────────────────────────
    FUZZINESS: str = field(default_factory=lambda: _str("SEARCH_V2_FUZZINESS", "AUTO"))
    TYPO_MAX_EDIT_DISTANCE: int = field(default_factory=lambda: _int("SEARCH_V2_TYPO_MAX_EDIT_DISTANCE", 2))

    # ── Business ranking bounds (see ranking/business_ranking.py) ───
    # Calibrated so business ranking can influence at most ~8 rank positions in either
    # direction (derivation: max_mult = (k+8+1)/(k+1) = 69/61 ≈ 1.131 with k=60).
    # Previous bounds [0.75, 1.35] allowed ~21-position swings, which let high-nutrition
    # packaged products (e.g. protein bars) overtake more-relevant products (e.g. whey
    # proteins) for queries where RRF had already produced the correct ordering.
    BUSINESS_MIN_MULTIPLIER: float = field(default_factory=lambda: _float("SEARCH_V2_BUSINESS_MIN_MULTIPLIER", 0.90))
    BUSINESS_MAX_MULTIPLIER: float = field(default_factory=lambda: _float("SEARCH_V2_BUSINESS_MAX_MULTIPLIER", 1.12))

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

    # ── NL filter extraction: enable flag ────────────────────────────────────
    ENABLE_NL_FILTER_EXTRACTION: bool = field(default_factory=lambda: _bool("SEARCH_V2_ENABLE_NL_FILTERS", True))


SETTINGS = SearchV2Settings()


def reload_settings() -> SearchV2Settings:
    """Re-read env vars into a fresh settings object — useful in the playground
    when a developer flips a toggle and wants it to take effect without
    restarting the process. Does NOT mutate the module-level SETTINGS singleton;
    callers (e.g. the playground backend) should hold their own reference."""
    return SearchV2Settings()
