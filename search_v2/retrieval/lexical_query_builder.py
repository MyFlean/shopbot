"""
search_v2/retrieval/lexical_query_builder.py
─────────────────────────────────────────────────
Builds the OpenSearch lexical (BM25) query for Search V2. Every capability
from the brief's "Lexical Search" section lives here:

  BM25                  -> the underlying scoring, nothing to configure
  dis_max                -> _variant_dis_max() combines original + corrected +
                             segmentation-repaired query text variants
  multi_match             -> _field_match_clauses(), intelligent field weighting
                             via FIELD_WEIGHTS
  phrase queries          -> match_phrase clause, high boost
  phrase_prefix           -> match_phrase_prefix clause
  bool_prefix             -> multi_match type=bool_prefix against the
                             search_as_you_type field's generated subfields
  search_as_you_type      -> consumed via the field type itself (see
                             indexing/mapping_builder.py) + the bool_prefix clause
  fuzziness               -> ES-level fuzziness:AUTO on the main multi_match,
                             COMPLEMENTARY to query_processing/typo_correction.py
                             (see that module's docstring for why both)
  wildcard                -> a single low-boosted wildcard clause against the
                             exact_normalized keyword field — deliberately
                             minimal (wildcards are expensive/imprecise), a
                             tail-catch safety net, not a primary mechanism
  autocomplete            -> build_suggest_query(), uses the completion suggester
  exact match boosting    -> term query on name.exact_normalized, highest boost
  intelligent field weighting -> FIELD_WEIGHTS
  category boosting       -> nested term query against category_hierarchies.segments
  derivative product demotion -> build_query() wraps everything in a `boosting`
                             query (OpenSearch's purpose-built "demote without
                             excluding" query type) — see
                             DERIVATIVE_MARKER_TERMS and the module-level note
                             on why this is a GENERAL mechanism, not a
                             per-product hardcode.

No LLM anywhere in this file.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from search_v2.config.settings import SearchV2Settings, SETTINGS
from search_v2.query_processing.query_pipeline import ProcessedQuery

if TYPE_CHECKING:
    from search_v2.retrieval.filters import SearchFilters

# ── Field weighting (brief: "intelligent field weighting") ────────────────
# Tuned by signal strength: an exact-ish hit on the product name is a much
# stronger relevance signal than the same term appearing in a long description.
# NOTE: category_hierarchies is deliberately NOT in this dict — it's a
# `nested` field (see indexing/mapping_builder.py), and a nested field can't
# be referenced from a flat multi_match `fields` list any more than from a
# flat `match` clause (same class of structural mismatch, different query
# type). Its boost lives in CATEGORY_HIERARCHIES_BOOST below instead, used
# only by the dedicated `nested` query clause.
# ── Marketplace-agnostic availability ────────────────────────────────────────
# Each entry is a dot-path to a boolean "in-stock" flag for one marketplace
# channel. Extend this list when new channels are onboarded; no other code
# in the retrieval layer needs to change. The business rule in business_ranking
# must remain at weight=0.0 (availability is a retrieval gate, not a ranking
# signal — see config/settings.py BUSINESS_RULE_WEIGHTS).
AVAILABILITY_IN_STOCK_PATHS = [
    "availability.in_stock",          # generic/legacy flat field
    "availability.blinkit.in_stock",  # Blinkit channel
    "availability.zepto.in_stock",    # Zepto channel
]


def _build_in_stock_filter() -> Dict[str, Any]:
    """Returns a filter clause that treats a product as available if ANY
    known marketplace channel reports it as in-stock. Extend
    AVAILABILITY_IN_STOCK_PATHS to add new channels without touching logic."""
    return {
        "bool": {
            "should": [{"term": {path: True}} for path in AVAILABILITY_IN_STOCK_PATHS],
            "minimum_should_match": 1,
        }
    }


FIELD_WEIGHTS = {
    "name": 3.0,
    "name.camel": 1.5,
    "name.phonetic": 0.8,
    "brand": 2.0,
    "brand.camel": 1.0,
    "vernacular_synonyms": 2.5,  # an exact vernacular term hit ("seb") is a strong, precise signal
    "description": 1.0,
}
CATEGORY_HIERARCHIES_BOOST = 0.8

# General linguistic markers of a PROCESSED/DERIVATIVE product, not specific
# to any one product. This is what makes "apple should rank fresh apple
# before apple juice" a property of the pipeline rather than a hardcoded
# "if query == apple" rule — the same list demotes "milk" -> "milk powder"/
# "milk chocolate", "coffee" -> "coffee flavoured candy", etc. across the
# WHOLE catalog uniformly. Configurable, not exhaustive — extend as needed.
DERIVATIVE_MARKER_TERMS = [
    "juice", "drink", "beverage", "cider", "vinegar", "extract", "syrup",
    "sauce", "jam", "jelly", "candy", "candies", "flavoured", "flavored",
    "flavour", "flavor", "essence", "concentrate", "dried", "dehydrated",
    "crushed", "puree", "paste", "chutney", "pickle", "wine", "beer",
    "chips", "crisps", "powder", "extract",
]


def _es_fuzziness(enable_fuzzy: bool) -> Optional[str]:
    return SETTINGS.FUZZINESS if enable_fuzzy else None


def _field_match_clauses(text: str, settings: SearchV2Settings) -> List[Dict[str, Any]]:
    fuzziness = _es_fuzziness(settings.ENABLE_FUZZY)
    multi_match: Dict[str, Any] = {
        "query": text,
        "type": "best_fields",
        "fields": [f"{field}^{weight}" for field, weight in FIELD_WEIGHTS.items()],
        "tie_breaker": 0.3,
    }
    if fuzziness:
        multi_match["fuzziness"] = fuzziness

    clauses: List[Dict[str, Any]] = [{"multi_match": multi_match}]

    # Phrase queries — rewards the query appearing as a contiguous phrase,
    # which plain best_fields multi_match doesn't specifically reward.
    clauses.append({"match_phrase": {"name": {"query": text, "boost": 4.0}}})

    # phrase_prefix — supports "as you type" partial phrase queries.
    clauses.append({"match_phrase_prefix": {"name": {"query": text, "boost": 2.0}}})

    # bool_prefix — the purpose-built query type for search_as_you_type fields.
    clauses.append({
        "multi_match": {
            "query": text,
            "type": "bool_prefix",
            "fields": ["name", "name._2gram", "name._3gram"],
            "boost": 1.5,
        }
    })

    # Exact-match boosting — the single biggest lever for derivative-product
    # ranking (see DERIVATIVE_MARKER_TERMS for the complementary demotion side).
    clauses.append({"term": {"name.exact_normalized": {"value": text.lower(), "boost": 15.0}}})

    # Category boosting — generic, not category-specific: an exact match
    # against any single category-path segment nudges relevant-category
    # products up. category_hierarchies is a `nested` field with a
    # keyword-typed `segments` array (see indexing/mapping_builder.py) — it
    # must be queried through a `nested` query, not a flat `match`/`multi_match`
    # clause (neither works directly against a nested field; OpenSearch
    # indexes nested objects as separate hidden Lucene documents). `segments`
    # being `keyword` rather than `text` also means this is an exact-value
    # match (e.g. the literal string "biscuits_and_crackers"), not analyzed
    # full-text matching — a real but narrow signal: it helps when the query
    # text happens to equal a category segment verbatim, not general
    # free-text overlap with category names.
    clauses.append({
        "nested": {
            "path": "category_hierarchies",
            "query": {"term": {"category_hierarchies.segments": {"value": text.lower(), "boost": CATEGORY_HIERARCHIES_BOOST}}},
            "score_mode": "max",
        }
    })

    # Commodity / base-product boost — taxonomy-driven, no LLMs, no manually-
    # maintained keyword lists. Fires only when the query normalizes to match
    # a stored leaf_category value (leaf_category is a keyword field).
    #
    # Normalization applied to the query text (to tolerate taxonomy evolution):
    #   - lowercase                        ("Watermelon" → "watermelon")
    #   - spaces and hyphens → underscores ("cherry tomato" → "cherry_tomato")
    #   - case_insensitive=True on the term query (catalog data may be "Watermelon"
    #     or "watermelon"; the query-side normalization is symmetric)
    #
    # Why this fixes commodity ranking:
    #   "watermelon" → normalized "watermelon" → fires for leaf_category="Watermelon"
    #   "cherry tomato" → normalized "cherry_tomato" → fires for leaf_category="Cherry_Tomato"
    #   "Prime Strawberry Watermelon" drink → leaf_category="energy_drinks" → no fire
    #   "protein" → no leaf_category named "protein" → no fire
    #   "whey protein" → normalizes to "whey_protein", no such leaf_category → no fire
    #
    # Conservative: boost 8.0 is above the category_hierarchies soft signal
    # (0.8) but well below the exact-name term boost (15.0). It elevates the
    # correct commodity above derivative products without overwhelming BM25.
    if settings.ENABLE_LEAF_CATEGORY_BOOST and settings.LEAF_CATEGORY_COMMODITY_BOOST > 0:
        normalized_for_taxonomy = text.lower().replace(" ", "_").replace("-", "_")
        clauses.append({
            "term": {
                "leaf_category": {
                    "value": normalized_for_taxonomy,
                    "boost": settings.LEAF_CATEGORY_COMMODITY_BOOST,
                    "case_insensitive": True,
                }
            }
        })

    return clauses


def _wildcard_clause(text: str) -> Optional[Dict[str, Any]]:
    """A single, deliberately low-boosted wildcard safety net — see module
    docstring. Skipped for multi-word queries (wildcards on phrases are not
    meaningful) and very short tokens (would match almost everything)."""
    token = text.strip().lower()
    if not token or " " in token or len(token) < 4:
        return None
    return {"wildcard": {"name.exact_normalized": {"value": f"*{token}*", "boost": 0.3, "case_insensitive": True}}}


def _variant_dis_max(query: ProcessedQuery, settings: SearchV2Settings) -> Dict[str, Any]:
    """dis_max across every query variant (original text + any typo-corrected /
    segmentation-repaired text) — see query_pipeline.py for why both are kept
    rather than committing to one rewrite. Each variant's own clause set is
    itself a dis_max of the field/phrase/prefix/exact clauses above, scaled by
    that variant's confidence."""
    variant_queries = []
    for variant in query.variants:
        inner_clauses = _field_match_clauses(variant.text, settings)
        if settings.ENABLE_FUZZY:
            wc = _wildcard_clause(variant.text)
            if wc:
                inner_clauses.append(wc)
        variant_queries.append({
            "dis_max": {
                "tie_breaker": 0.3,
                "queries": inner_clauses,
                "boost": variant.confidence,
            }
        })

    return {"dis_max": {"tie_breaker": 0.2, "queries": variant_queries}}


def build_derivative_demotion_negative_query(query: ProcessedQuery) -> Optional[Dict[str, Any]]:
    """The 'negative' side of the boosting query — matches products whose NAME
    contains a general derivative/processed-product marker. Guards against the
    obvious correctness trap: if the user's OWN query contains one of these
    words ("apple juice"), we must not demote the very products they're
    looking for."""
    query_words = set()
    for variant in query.variants:
        query_words.update(variant.text.lower().split())

    active_markers = [m for m in DERIVATIVE_MARKER_TERMS if m not in query_words]
    if not active_markers:
        return None

    return {"match": {"name": {"query": " ".join(active_markers), "operator": "or"}}}


def build_filters(filters: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """General-purpose filter builder covering the common filter dimensions.
    Not a port of the V1/legacy 800-line filter builder (different index
    schema) — extend as new filter dimensions are needed."""
    filters = filters or {}
    clauses: List[Dict[str, Any]] = []

    if filters.get("category_group"):
        clauses.append({"term": {"category_group": filters["category_group"]}})
    if filters.get("leaf_category"):
        clauses.append({"term": {"leaf_category": filters["leaf_category"]}})
    if filters.get("category_path_prefix"):
        clauses.append({"prefix": {"category_paths": filters["category_path_prefix"]}})
    if filters.get("brand"):
        clauses.append({"term": {"brand.exact_normalized": str(filters["brand"]).lower()}})

    price_range: Dict[str, Any] = {}
    if filters.get("price_min") is not None:
        price_range["gte"] = filters["price_min"]
    if filters.get("price_max") is not None:
        price_range["lte"] = filters["price_max"]
    if price_range:
        clauses.append({"range": {"price": price_range}})

    if filters.get("dietary_labels"):
        clauses.append({"terms": {"package_claims.dietary_labels": list(filters["dietary_labels"])}})

    if filters.get("in_stock_only"):
        clauses.append(_build_in_stock_filter())

    return clauses


def build_query(
    query: ProcessedQuery,
    filters: Optional[Union[Dict[str, Any], "SearchFilters"]] = None,
    size: Optional[int] = None,
    settings: Optional[SearchV2Settings] = None,
    sort_by: Optional[str] = None,
    offset: int = 0,
) -> Dict[str, Any]:
    """The main entry point. Returns a complete OpenSearch request body.

    `filters` accepts either the legacy Dict form (backward compatible) or a
    SearchFilters object. When SearchFilters is passed, must_not exclusions and
    personal-care should clauses are threaded through automatically.
    """
    settings = settings or SETTINGS
    size = size if size is not None else settings.DEFAULT_RESULT_SIZE

    positive_query = _variant_dis_max(query, settings)

    # Resolve filter clauses — support both legacy dict and SearchFilters
    filter_clauses: List[Dict[str, Any]] = []
    must_not_clauses: List[Dict[str, Any]] = []
    should_extras: List[Dict[str, Any]] = []
    _effective_sort_by = sort_by
    _effective_offset = offset

    if filters is not None:
        # Import here to avoid circular at module load time
        from search_v2.retrieval.filters import SearchFilters, build_filter_clauses
        if isinstance(filters, SearchFilters):
            fc = build_filter_clauses(filters)
            filter_clauses = fc.filter_clauses
            must_not_clauses = fc.must_not_clauses
            should_extras = fc.should_clauses
            if _effective_sort_by is None:
                _effective_sort_by = filters.sort_by
            if _effective_offset == 0:
                _effective_offset = filters.offset
        else:
            filter_clauses = build_filters(filters)

    bool_clause: Dict[str, Any] = {
        "should": [positive_query] + should_extras,
        "minimum_should_match": 1,
    }
    if filter_clauses:
        bool_clause["filter"] = filter_clauses
    if must_not_clauses:
        bool_clause["must_not"] = must_not_clauses

    base_query: Dict[str, Any] = {"bool": bool_clause}

    final_query = base_query
    if settings.ENABLE_DERIVATIVE_DEMOTION:
        negative_query = build_derivative_demotion_negative_query(query)
        if negative_query:
            final_query = {
                "boosting": {
                    "positive": base_query,
                    "negative": negative_query,
                    "negative_boost": settings.DERIVATIVE_DEMOTION_FACTOR,
                }
            }

    body: Dict[str, Any] = {
        "size": size,
        "query": final_query,
        "_source": {"excludes": ["text_vector", "text_vector_source", "vernacular_synonyms"]},
    }

    if _effective_offset:
        body["from"] = _effective_offset

    if _effective_sort_by:
        from search_v2.retrieval.sorting import build_sort_clauses
        sort_clauses = build_sort_clauses(_effective_sort_by)
        if sort_clauses:
            body["sort"] = sort_clauses

    return body


def build_suggest_query(prefix: str, category_group: Optional[str] = None, size: int = 8) -> Dict[str, Any]:
    """Autocomplete via the completion suggester (name_suggest field — see
    indexing/mapping_builder.py). Fed by BOTH layers of the synonym system at
    index time, so "seb" can autocomplete to apple products even though
    completion suggesters don't go through a synonym-aware analyzer
    themselves (see ARCHITECTURE.md)."""
    suggest_clause: Dict[str, Any] = {
        "prefix": prefix,
        "completion": {
            "field": "name_suggest",
            "size": size,
            "fuzzy": {"fuzziness": "AUTO"},
        },
    }
    if category_group:
        suggest_clause["completion"]["contexts"] = {"category_group": [category_group]}

    return {"suggest": {"name_suggest": suggest_clause}, "_source": ["name", "id", "brand"]}
