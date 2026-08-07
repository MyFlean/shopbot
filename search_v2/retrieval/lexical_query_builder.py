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
  wildcard                -> a single low-boosted wildcard clause against
                             name_phonetic.keyword — deliberately minimal
                             (wildcards are expensive/imprecise), a
                             tail-catch safety net, not a primary mechanism
                             (was name.exact_normalized; see
                             aggregations.py's module docstring for why
                             that field doesn't actually exist on the
                             currently-running index)
  autocomplete            -> build_suggest_query(), uses the completion suggester
  exact match boosting    -> term query on name_phonetic.keyword, highest boost
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

import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union

from search_v2.config.settings import SearchV2Settings, SETTINGS
from search_v2.query_processing.query_pipeline import ProcessedQuery
from search_v2.retrieval.listing import LISTING_COLLAPSE, LISTING_SOURCE_EXCLUDES

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
    # Same relative weight as name.phonetic (0.8/3.0 of name's weight),
    # scaled to brand's own weight — the mapping/analyzer already exist and
    # are already populated (auto-derived multi-field, same mechanism as
    # name.phonetic), this was just never queried.
    "brand.phonetic": 0.5,
    "vernacular_synonyms": 2.5,  # an exact vernacular term hit ("seb") is a strong, precise signal
    "description": 1.0,
}
CATEGORY_HIERARCHIES_BOOST = 0.8
SUPPLEMENT_DEPARTMENT_BOOST = 3.0
SUPPLEMENT_PROTEIN_CATEGORY_BOOST = 5.5
SUPPLEMENT_PRE_POST_WORKOUT_CATEGORY_BOOST = 4.0

_BRAND_FIELDS = {"brand", "brand.camel", "brand.phonetic"}
_NAME_FIELDS = {"name", "name.camel", "name.phonetic"}
_PROTEIN_SUPPLEMENT_QUERY_PATTERN = re.compile(r"^protein(?:\s+powder)?$")
_SUPPLEMENT_PROTEIN_CATEGORIES = (
    ("protein", SUPPLEMENT_PROTEIN_CATEGORY_BOOST),
    ("pre_post_workout", SUPPLEMENT_PRE_POST_WORKOUT_CATEGORY_BOOST),
)


def _core_text(text: str, health_intent_matched_phrases: Tuple[str, ...]) -> str:
    if not health_intent_matched_phrases:
        return text
    descriptor_words = set()
    for phrase in health_intent_matched_phrases:
        descriptor_words.update(phrase.lower().split())
    core_words = [w for w in text.split() if w.lower() not in descriptor_words]
    return " ".join(core_words).strip()


def _is_protein_supplement_query(text: str) -> bool:
    normalized = " ".join(text.lower().split())
    return bool(_PROTEIN_SUPPLEMENT_QUERY_PATTERN.fullmatch(normalized))

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


def _minimum_should_match_for(text: str) -> Optional[str]:
    """How many of the query's own terms a document must contain to count as
    a match, as a function of query LENGTH only — no per-word logic, so this
    applies uniformly across the whole catalog rather than special-casing any
    product/category.

    Why this exists: plain multi_match `best_fields` with no
    minimum_should_match uses OR semantics — a document matching just ONE of
    several query terms is already "a match" and competes on equal footing
    with one matching all of them. For a query like "gluten free chips" or
    "protein chips", that lets a product which only shares the MODIFIER
    ("gluten", "free", "protein" — common, high-frequency words spread across
    many unrelated products) rank alongside or above a product that actually
    contains the head noun ("chips") the user is asking for, because BM25
    scores term overlap, not query-intent coverage. Requiring most/all query
    terms to be present forces candidates to satisfy the query's FULL intent
    (modifier AND noun) rather than any single token of it.

    One- and two-word queries require every term (a two-word query like
    "peanut butter" or "greek yogurt" only means something as the pair).
    Longer queries relax slightly to 75% so one incidental/filler word
    doesn't zero out otherwise-strong matches.
    """
    n = len(text.split())
    if n <= 1:
        return None  # nothing to require coverage of
    if n <= 3:
        return "100%"
    return "75%"


def _bool_prefix_minimum_should_match_for(text: str) -> Optional[str]:
    # Deliberately not _minimum_should_match_for()'s "100%"/"75%" — verified
    # against the live cluster that those return ZERO hits here specifically
    # (this clause's name._2gram/_3gram shingle fields change how Lucene
    # counts should-clauses for percentage purposes). "50%" still closes the
    # no-coverage-requirement gap without that failure mode.
    n = len(text.split())
    if n <= 1:
        return None
    return "50%"


def _field_match_clauses(
    text: str,
    settings: SearchV2Settings,
    include_bool_prefix: bool = True,
    health_intent_matched_phrases: Tuple[str, ...] = (),
) -> List[Dict[str, Any]]:
    """`include_bool_prefix=False` omits the bool_prefix/_2gram/_3gram clause
    below — see build_query()'s docstring for why this exists (a rare,
    cluster-side query-construction failure for certain query text, not a
    feature toggle for normal use)."""
    fuzziness = _es_fuzziness(settings.ENABLE_FUZZY)
    fuzzy_fields = [f for f in FIELD_WEIGHTS if f not in _BRAND_FIELDS]
    min_should_match = _minimum_should_match_for(text)
    core_text = _core_text(text, health_intent_matched_phrases)
    core_word_count = len(core_text.split()) if core_text else 0

    multi_match: Dict[str, Any] = {
        "query": text,
        "type": "best_fields",
        "fields": [f"{field}^{FIELD_WEIGHTS[field]}" for field in fuzzy_fields],
        "tie_breaker": 0.3,
    }
    if fuzziness:
        multi_match["fuzziness"] = fuzziness
        multi_match["prefix_length"] = settings.FUZZY_PREFIX_LENGTH
    if min_should_match:
        multi_match["minimum_should_match"] = min_should_match

    clauses: List[Dict[str, Any]] = [{"multi_match": multi_match}]

    # Brand fields are proper nouns — ES-level edit-distance fuzziness there
    # lets short, common query words coincidentally collide with unrelated
    # brand names (e.g. "gain" is one edit from "Jain"), flooding results
    # with an unrelated brand's whole catalog. Brand still gets exact,
    # camelCase, and phonetic (sound-alike) matching, just not fuzzy.
    brand_match: Dict[str, Any] = {
        "query": text,
        "type": "best_fields",
        "fields": [f"{field}^{FIELD_WEIGHTS[field]}" for field in FIELD_WEIGHTS if field in _BRAND_FIELDS],
        "tie_breaker": 0.3,
    }
    if min_should_match:
        brand_match["minimum_should_match"] = min_should_match
    clauses.append({"multi_match": brand_match})

    # Phrase queries — rewards the query appearing as a contiguous phrase,
    # which plain best_fields multi_match doesn't specifically reward. A
    # small slop tolerates a descriptor word landing between the query's
    # terms in the indexed name (e.g. query "gluten free chips" against a
    # product named "... Gluten Free Potato Chips" — without slop, that
    # extra "Potato" token makes this exact-order phrase clause never fire
    # at all for almost any real multi-word grocery query).
    #
    # Health Intent descriptor words (e.g. "heart" in "heart healthy foods")
    # are excluded from this and the exact-match clause below — a phrase or
    # exact match on the product NAME is a much stronger signal than an
    # ordinary term match, and a health-context word appearing in a name for
    # unrelated reasons (a Valentine's "Heart Shaped" chocolate box) shouldn't
    # earn it. Skipped entirely (not run against a 1-word residual) when
    # fewer than 2 non-descriptor words remain, since a single-word "phrase"
    # tests nothing the ordinary multi_match above doesn't already cover, and
    # could otherwise match on pure coincidence (e.g. a brand name that
    # happens to contain one leftover word) with no coverage requirement to
    # guard it.
    if core_word_count >= 2:
        clauses.append({"match_phrase": {"name": {"query": core_text, "boost": 4.0, "slop": 2}}})
        clauses.append({"match_phrase_prefix": {"name": {"query": core_text, "boost": 2.0, "slop": 2}}})
    elif not health_intent_matched_phrases:
        clauses.append({"match_phrase": {"name": {"query": text, "boost": 4.0, "slop": 2}}})
        clauses.append({"match_phrase_prefix": {"name": {"query": text, "boost": 2.0, "slop": 2}}})

    # bool_prefix — the purpose-built query type for search_as_you_type fields.
    # minimum_should_match matches the main multi_match's discipline above —
    # without it, a single generic term (e.g. "foods") shared with a brand
    # name embedded in the indexed name field (many catalog brands are named
    # "___ Foods") can qualify a document for the whole multi-word query
    # through this clause alone, bypassing the term-coverage requirement
    # every other clause enforces. Unaffected by Health Intent — it's a
    # prefix/autocomplete mechanism, not exact-match or phrase-match.
    if include_bool_prefix:
        bool_prefix_match: Dict[str, Any] = {
            "query": text,
            "type": "bool_prefix",
            "fields": ["name", "name._2gram", "name._3gram"],
            "boost": 1.5,
        }
        bool_prefix_msm = _bool_prefix_minimum_should_match_for(text)
        if bool_prefix_msm:
            bool_prefix_match["minimum_should_match"] = bool_prefix_msm
        clauses.append({"multi_match": bool_prefix_match})

    # Exact-match boosting — the single biggest lever for derivative-product
    # ranking (see DERIVATIVE_MARKER_TERMS for the complementary demotion side).
    # Uses core_text (Health Intent descriptor words removed) for the same
    # reason as the phrase clauses above — see that comment.
    exact_match_text = core_text if health_intent_matched_phrases else text
    if exact_match_text:
        # name_phonetic.keyword, not the planned-but-absent
        # name.exact_normalized (see aggregations.py's module docstring for
        # the mapping-drift investigation) — case_insensitive since this
        # field has no lower_keyword normalizer.
        clauses.append({"term": {"name_phonetic.keyword": {"value": exact_match_text.lower(), "boost": 15.0, "case_insensitive": True}}})

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

    if _is_protein_supplement_query(text):
        clauses.append(
            {
                "nested": {
                    "path": "category_hierarchies",
                    "score_mode": "max",
                    "query": {
                        "bool": {
                            "filter": [
                                {"term": {"category_hierarchies.segments": "supplements"}},
                            ],
                            "should": [
                                *[
                                    {
                                        "term": {
                                            "category_hierarchies.segments": {
                                                "value": category_segment,
                                                "boost": category_boost,
                                            }
                                        }
                                    }
                                    for category_segment, category_boost in _SUPPLEMENT_PROTEIN_CATEGORIES
                                ],
                                {
                                    "term": {
                                        "category_hierarchies.segments": {
                                            "value": "supplements",
                                            "boost": SUPPLEMENT_DEPARTMENT_BOOST,
                                        }
                                    }
                                }
                            ],
                            "minimum_should_match": 1,
                        }
                    },
                }
            }
        )

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
    return {"wildcard": {"name_phonetic.keyword": {"value": f"*{token}*", "boost": 0.3, "case_insensitive": True}}}


def _variant_dis_max(
    query: ProcessedQuery,
    settings: SearchV2Settings,
    include_bool_prefix: bool = True,
    health_intent_matched_phrases: Tuple[str, ...] = (),
) -> Dict[str, Any]:
    """dis_max across every query variant (original text + any typo-corrected /
    segmentation-repaired text) — see query_pipeline.py for why both are kept
    rather than committing to one rewrite. Each variant's own clause set is
    itself a dis_max of the field/phrase/prefix/exact clauses above, scaled by
    that variant's confidence."""
    variant_queries = []
    for variant in query.variants:
        inner_clauses = _field_match_clauses(
            variant.text, settings, include_bool_prefix=include_bool_prefix,
            health_intent_matched_phrases=health_intent_matched_phrases,
        )
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
    if any(_is_protein_supplement_query(variant.text) for variant in query.variants):
        active_markers = [marker for marker in active_markers if marker != "powder"]
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
        # brand_phonetic.keyword, not the planned-but-absent
        # brand.exact_normalized — see aggregations.py's module docstring.
        clauses.append({"term": {"brand_phonetic.keyword": {"value": str(filters["brand"]).lower(), "case_insensitive": True}}})

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
    include_bool_prefix: bool = True,
    health_intent_matched_phrases: Tuple[str, ...] = (),
) -> Dict[str, Any]:
    """The main entry point. Returns a complete OpenSearch request body.

    `filters` accepts either the legacy Dict form (backward compatible) or a
    SearchFilters object. When SearchFilters is passed, must_not exclusions and
    personal-care should clauses are threaded through automatically.

    `include_bool_prefix=False` omits the bool_prefix/_2gram/_3gram clause
    from every variant's match clauses. This is NOT a normal-use toggle — it
    exists purely as a fallback for a narrow, cluster-side query-construction
    failure: for some query text, OpenSearch's search-time synonym_graph
    filter combined with the _2gram/_3gram shingle analysis that bool_prefix
    needs can produce a token graph exceeding Lucene's internal
    CachingTokenFilter buffer (>100 cached tokens), which OpenSearch rejects
    with a 400 ("Too many cached tokens"). This isn't specific to any one
    query string — it depends on how large that text's merged synonym
    equivalence group happens to be, a property of the catalog's synonym
    data, not of the code. hybrid_search_orchestrator.py detects exactly
    this error and retries once with include_bool_prefix=False rather than
    failing the request; every other clause (multi_match with fuzziness,
    match_phrase, match_phrase_prefix — i.e. all of typo correction's and
    synonym expansion's actual matching power) is completely unaffected,
    since none of them individually trigger this (verified against the
    live cluster — see the retry site for the specific queries checked)."""
    settings = settings or SETTINGS
    size = size if size is not None else settings.DEFAULT_RESULT_SIZE

    positive_query = _variant_dis_max(
        query, settings, include_bool_prefix=include_bool_prefix,
        health_intent_matched_phrases=health_intent_matched_phrases,
    )

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

    # An empty query (every variant's text is blank) has no lexical match to
    # require — a "should" + minimum_should_match:1 clause built from empty
    # text can never match anything, at any filter combination. This is a
    # genuine, deliberate "filters/category only, no text" search (e.g.
    # unified_search.py's filters-only branch, with no query and no
    # subcategory to route through category_browsing instead), not a
    # degraded/failed query — so it drops the should-match requirement
    # entirely and retrieves purely by filter, exactly like
    # category_browsing.browse()'s filter-only query.
    has_query_text = any(v.text.strip() for v in query.variants)

    bool_clause: Dict[str, Any] = {}
    if has_query_text:
        bool_clause["should"] = [positive_query] + should_extras
        bool_clause["minimum_should_match"] = 1
    elif should_extras:
        bool_clause["should"] = should_extras
    if filter_clauses:
        bool_clause["filter"] = filter_clauses
    if must_not_clauses:
        bool_clause["must_not"] = must_not_clauses

    base_query: Dict[str, Any] = {"bool": bool_clause} if bool_clause else {"match_all": {}}

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
        "_source": {"excludes": list(LISTING_SOURCE_EXCLUDES)},
        "collapse": dict(LISTING_COLLAPSE),
    }

    if _effective_offset:
        body["from"] = _effective_offset

    if _effective_sort_by:
        from search_v2.retrieval.sorting import build_sort_clauses
        sort_clauses = build_sort_clauses(_effective_sort_by)
        if sort_clauses:
            body["sort"] = sort_clauses

    return body


ALL_CATEGORY_GROUPS = ("f_and_b", "personal_care")


def build_suggest_query(prefix: str, category_group: Optional[str] = None, size: int = 8) -> Dict[str, Any]:
    """Autocomplete via the completion suggester (name_suggest field — see
    indexing/mapping_builder.py). Fed by BOTH layers of the synonym system at
    index time, so "seb" can autocomplete to apple products even though
    completion suggesters don't go through a synonym-aware analyzer
    themselves (see ARCHITECTURE.md).

    name_suggest's category_group context has no mapping-level default, so
    OpenSearch rejects a completion query that omits it entirely ("Missing
    mandatory contexts"). Passing every known category group is the
    unrestricted case; a specific category_group narrows it."""
    suggest_clause: Dict[str, Any] = {
        "prefix": prefix,
        "completion": {
            "field": "name_suggest",
            "size": size,
            "fuzzy": {"fuzziness": "AUTO"},
            "contexts": {"category_group": [category_group] if category_group else list(ALL_CATEGORY_GROUPS)},
        },
    }

    return {"suggest": {"name_suggest": suggest_clause}, "_source": ["name", "id", "brand", "category_group"]}
