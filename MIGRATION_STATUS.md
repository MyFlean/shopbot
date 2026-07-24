# Migration Status

Last updated: 2026-07-23. This file is updated after every phase.

## Current phase: **Migration complete. Only production-only deployment steps remain.**

This pass added: an interactive Category Browsing explorer CLI (`dev_category_browsing_cli.py`);
a complete V1-fallback audit with 11 proven-unnecessary fallbacks removed and 2 more previously-
unmigrated endpoints found and fixed (`/rs/api/v1/products/search`, a chat-flow product lookup) —
see `V1_FALLBACK_AUDIT.md` and `V1_RUNTIME_INVENTORY.md`; a production-readiness review that found
and fixed a real deployment breakage the `search_gateway/` deletion had introduced — see
`PRODUCTION_READINESS.md`; and an automated Postman regression runner with a V1-vs-V2 comparison
mode, plus 21 previously-missing endpoints added to the collection — see `POSTMAN_REGRESSION.md`.
`FINAL_MIGRATION_REPORT.md` §§16-21 has full detail and evidence for all of the above.

## Overall status

| Feature | Status | Regression status | Remaining work | Blockers | Dependencies |
|---|---|---|---|---|---|
| Lexical/hybrid/semantic search | Done (pre-existing) | Green (baseline established this session) | None | None | None |
| Product Intent extraction | Done (pre-existing) | Green | None | None | None |
| Business ranking | Done (pre-existing) | Green | None | None | None |
| Dynamic filters (search endpoint) | Done (pre-existing) | Green | None | None | None |
| Suggestions/Autocomplete | **Done** | Green — full pytest suite (192/192) + live local endpoint test on both `/rs/v1/search/suggest` and `/rs/v2/search/suggest` | None | None | — |
| Category/subcategory browsing | **Done** | Green — 192/192 pytest + live local test: `f_and_b/food/packaged_meals/pasta_and_soups` now returns `total=97` (was 0 under V1's `.keyword` bug) with correct dynamic filters | None | None |
| Taxonomy | **Done (no new code)** | N/A — confirmed reuse, not migration | Confirmed `shopping_bot/data/category_mapping.json` is the one existing shared taxonomy; `category_browsing/` already resolves through it via the existing `_resolve_subcategory_es_path()`, not a new lookup. `home/categories.json` is a separate, non-competing UI tile-display config | None | None |
| Dynamic filters (all endpoints) | **Effectively done** | Green | Search endpoint (pre-existing) and category browsing (this session) both compute filters via the same shared `dynamic_search_filters.py` helpers | None | None |
| Best Sellers | **Done** | Green — 192/192 pytest + live local test, 6 real products, correctly flean-score-sorted | None | None |
| Supplements | **Done** | Green — 192/192 pytest + live local test, 3 products (fewer than the 6-product target — real data limitation, category has fewer qualifying candidates, not a bug) | None | None — shared `bestsellers/` module |
| Curated home collections | **Done** | Green — 192/192 pytest + live local test (`/home/curated`, `/home/curated/all`), real products with nutrition/macro_tags | None | None |
| Flean Picks | **Done** | Green — 192/192 pytest + live local test: `source=see_all` (4 collections × 12 products) and `source=home` (12 flat products) and `/flean-picks/<collection_key>`, all 200 with real data | `fallback_meta` diagnostic block (tier-count telemetry) intentionally not replicated — disclosed simplification, no user-facing product data lost | None |
| Product-card transform | **Done** | Green | New `search_v2/extension/product/card.py`, shared by category_browsing, bestsellers, curated, flean_picks | None | None |
| PDP | **Done** | Green — 192/192 pytest + live local test, native V2 fetch + reused `transform_to_pdp()` | Indexing allowlist fixed in `search` repo (`review_stats`/`cons_list`/`pros_list`/`search_keywords`/structured ingredients); validated via a disposable test index (structured ingredients confirmed populating; review_stats/cons_list unverifiable locally — 0 of 21,470 local Mongo docs have `tags_and_sentiments` at all) | None blocking — see `FINAL_MIGRATION_REPORT.md` |
| Healthier alternatives / Recommended | **Done** | Green — 192/192 pytest + live local test: `/alternatives` (5 results) and `/recommended` (8 results), both against a real product, correct subcategory-percentile ordering | Full field parity (review_stats/cons_list/structured ingredients) still pending the same indexing allowlist decision as PDP — cards render correctly today with the fields that ARE indexed | None — shares `product/card.py` |
| Scanner | **Done** | Green — 192/192 pytest; logic reuses `search_v2.extension.search.search()` (V2 query pipeline) for the brand+name text search, same V2-first/V1-fallback pattern | None | None | `extension/search` |
| Flean Score lookup | **Done** | Green — 192/192 pytest + live local test (`/rs/api/v1/flean-score`), real product, real score | None | None | `extension/pdp.fetch_product` |
| Catalogue | **Done** | Green — 192/192 pytest + live local test (`/rs/api/v1/catalogue`), `total=353` returned via native V2 category browsing | None | None | `extension/category_browsing` |
| Products (`/api/v1/products`) | **Done** | Green — 192/192 pytest + live local test: query path (`total=14`), filters-only path (`total=75`), both native V2 | None | None | `extension/search`, `extension/category_browsing` |
| Batch PDP | **Done** | Green — 192/192 pytest + live local test, one `terms` query for all requested ids (new `extension/pdp.fetch_products_batch()`), correct `not_found` handling | None | None | `extension/pdp` |
| Catalogue mapping | **Done (no new code)** | N/A | Already engine-agnostic — reads `category_mapping.json` directly, no ES call | None | None |
| SearchGateway retirement | **Done** | Green — 192/192 pytest + live local test after deletion | `search_gateway/` package deleted entirely. Its pipeline (`_build_search()`) moved to `search_v2/extension/search/core.py` as plain module-level functions (`search()`, `warmup()`), no class, matching every other `extension/*` module's convention. All three callers (`unified_search.py`, `shopping_bot/__init__.py`, `dev_search_cli.py`) rewired; test mock target updated | None | None |
| Shop by Goal | **Architecture prepped, not implemented** | N/A | `search_v2/extension/shop_by_goal/` documents which goals are ready today from existing `category_data.tags`/`stats.*_percentiles` (Gluten Free, High Protein, Low Sugar, High Fiber, Low Carb, Gut Health), which need a rule definition only (Keto, Heart Healthy, Diabetic Friendly, Weight Loss), and which need new indexing tags (Kids Nutrition, Sports Nutrition, Immunity) | None — explicitly not implementing APIs yet, per instruction | Shared taxonomy (satisfied) |

## Regression baseline (established this session, available for comparison after each phase)

- Local: `apple, curd, protein bar, greek yogurt, dhaniya, kothambir, angur, atta, coconut oil` —
  full stage-by-stage timing + correctness captured in this session's transport/latency
  investigations.
- Production: same query set, same methodology, captured for comparison.
- Full pytest suite: 192 passed, 1 skipped, 0 failed — current baseline, unaffected by directory
  scaffolding added this session (re-verified after scaffolding).

## Blockers requiring your decision before certain phases can proceed

1. **Indexing allowlist change** (`search` repo, `document_transformer.py`) — needed before PDP/
   alternatives/recommended can reach full field parity with V1. This is a change in the *other*
   repo, done locally, with its own local reindex — flagged here so it's not forgotten, not
   started.
2. **Postman collection gap** — the supplied collection doesn't cover suggestions, flean-picks,
   alternatives, recommended, or subcategory browsing (see `ENDPOINT_MIGRATION_MATRIX.md` §
   footer). Decide whether manual/code-level regression is acceptable for those or whether an
   updated collection is needed before those phases are considered "regression complete."

## Phase 1 implementation notes (Suggestions/Autocomplete)

- `search_v2/extension/suggestions/suggest.py` — plain function, no gateway class. Lazy
  module-level `OpenSearchClient` singleton, same pattern as `get_es_fetcher()`.
- `shopping_bot/routes/unified_search.py`'s `_fetch_flat_suggestions()` now tries V2 first
  (unless `SEARCH_ENGINE=v1`), falls back to V1 on any exception (hard error only when
  `SEARCH_ENGINE=v2` explicitly) — same fallback pattern as the main search endpoint.
- **Bug found and fixed during regression** (not a pre-existing issue, found live):
  `build_suggest_query()`'s completion query omitted the `contexts` clause entirely when no
  `category_group` was supplied, but the `name_suggest` field's mapping has no context default —
  OpenSearch rejected every such query with `"Missing mandatory contexts in context query"`.
  Fixed in `search_v2/retrieval/lexical_query_builder.py` by passing all known category groups
  (`f_and_b`, `personal_care`) as the unrestricted case. This is a real fix to shared retrieval
  code, not a new feature — logged here and in `PERFORMANCE_IMPROVEMENTS.md` is not needed since
  this is a correctness fix, not a performance change.
- **Known data-quality issue, not fixed here** (indexing-side, other repo): some `name_suggest`
  completion inputs are redundant when a product's `name` already starts with its `brand` (e.g.
  "Apis Apis Multifloral Honey") — comes from `index_v2.py`'s
  `attach_vernacular_synonyms_and_suggest()` unconditionally prefixing `brand` onto `name`. Not a
  shopbot-main fix; noting it for whoever next touches that function in the `search` repo.
- V1's suggestions distinguished "product" vs. standalone "brand" entries; V2's completion field
  as currently indexed doesn't produce standalone brand-only suggestions (no such entries in
  `name_suggest.input`). All V2 suggestions are tagged `type: "product"`. This is a disclosed,
  deliberate simplification (per "prefer the cleaner design"), not a silent capability loss —
  `_group_suggestions_by_brand()` still groups correctly by each product's own `brand` field.

## Phase 2-6 implementation notes

- **Category browsing** (`search_v2/extension/category_browsing/browse.py`): filter-only query on
  `category_paths` (bare keyword field). Reuses the existing, already-shared
  `_resolve_subcategory_es_path()` for taxonomy resolution — confirmed no second taxonomy created.
  Wired into `unified_search.py`'s subcategory-only branch, same fallback pattern as the
  query-driven path.
- **Best Sellers / Supplements** (`search_v2/extension/bestsellers/bestsellers.py`): single ES
  request, one `filters` aggregation bucket per category path with a `top_hits` sub-aggregation —
  same per-category-then-backfill selection algorithm as the legacy aggregation path, reusing
  `home_page.py`'s existing `_filter_cards_with_validation_cache` (engine-agnostic Redis stock
  check) unchanged.
- **Curated** (`search_v2/extension/curated/curate.py`): reuses `search_v2/retrieval/filters.py`'s
  `SearchFilters.from_dict()`/`build_filter_clauses()` directly — confirmed via that function's own
  docstring ("Construct from a V1 param dict or REST API request dict") that it's designed to
  accept the same filter dict shape `home_page.py` already builds.
- **Flean Picks** (`search_v2/extension/flean_picks/picks.py`): one `filters` aggregation request
  per relaxation tier (reusing the existing, engine-agnostic `_build_flean_hybrid_tier_filters()`
  for the 3-tier macro relaxation), each bucket keyed by collection and scoped to that collection's
  category paths.
- **Bug found and fixed during regression** (Flean Picks wiring): initial wiring called a nested
  closure (`_apply_validation_for_collected`) defined later in the same function — Python doesn't
  hoist nested function definitions, so this raised `UnboundLocalError` on first live test. Fixed
  by inlining the validation-cache call directly instead of depending on definition order.
- All five phases verified with the full pytest suite (192/192 throughout, no regressions) and
  live local endpoint tests against the already-running local OpenSearch/Redis/Mongo stack.

## Phase 7 implementation notes (Recommendations/Alternatives)

- `search_v2/extension/recommendations/similar.py` — one shared `similar_products()` function
  backs both `/alternatives` and `/recommended`, matching V1 where both endpoints already run
  identical logic (most-specific category path, percentile sort, exclude source) and only differ
  in response shape/default limit.
- Both routes wired with the same try-V2-first, fallback-on-error pattern as every other phase.

## Phase 8 resolution (PDP enrichment indexing gap)

Resolved without a full production-index rebuild: `document_transformer.py`'s
`ALLOWLIST_INDEX_FIELDS` was extended with `review_stats`/`cons_list`/`pros_list`/
`search_keywords` (already computed by `sanitize_for_es()`, previously dropped before reaching the
index), and `mapping_builder.py` given explicit mappings for the new keyword/text fields. Validated
via a disposable local test index (`products-search-v2-test`, 30 docs, deleted after inspection):
`ingredients.structured` populated on 12/30 sampled docs; `review_stats`/`cons_list` correctly 0/30
locally, NOT because the fields are dead — see the `tags_and_sentiments` lineage findings below.
The live local index (`products-search-v2`, 6510 docs) was never touched, avoiding a real AWS
Bedrock re-embedding cost.

## Final V1 runtime dependency audit (entire runtime, not just `extension/`)

Grepped `search_v2/` and (the now-deleted) `search_gateway/` for any functional reference to
`es_products`/`ElasticsearchProductsFetcher`/`get_es_fetcher`. Every hit was a comment or docstring
providing historical context — zero functional imports. The one genuine misplacement found:
`get_search_gateway()` (the V2 gateway singleton getter) was defined inside
`shopping_bot/data_fetchers/es_products.py` (the V1 legacy file) despite having nothing to do with
V1. Fixed by moving it into `search_gateway/gateway.py` — and then, this session, retiring
`search_gateway/` entirely in favor of `search_v2/extension/search/core.py` (see below).

Every route handler (`home_page.py`, `product_api.py`, `unified_search.py`) still calls
`get_es_fetcher()` and `transform_to_product_card()`/`transform_to_pdp()` — but these are the
**shared, engine-agnostic transform layer** (pure functions operating on a raw `_source` dict,
regardless of which engine produced it) and the **V1 fallback path**, not V1-only dependencies.
Every capability tries its native `search_v2/extension/*` implementation first; V1 is reached only
via explicit `SEARCH_ENGINE=v1`, a genuine V2 exception (with `auto` fallback), or an unresolvable
input (e.g. an unrecognized bare subcategory id) — the same disclosed, intentional pattern across
every phase, not an unnecessary dependency.

## `tags_and_sentiments` / `review_stats` / `pros_list` / `cons_list` lineage

- **Where generated**: Mongo-side enrichment (`tags_and_sentiments` on the source product
  document), extracted into ES-ready shape by `sanitize_for_es()` in both `search`'s legacy indexer
  (`index.products-v3.py`, functions `_extract_review_stats`/pros-cons derivation, confirmed at
  lines ~394-409, 757, 937, 948) and, as of this session, `search_v2/indexing/document_transformer.py`.
- **Did V1 depend on it?** Yes, genuinely and end-to-end — V1's own legacy indexer computes and
  writes these fields, and `shopping_bot/data_fetchers/es_products.py` (line ~4140) includes
  `"tags_and_sentiments.*"` in an ES `_source.includes` list for at least one fetch path. This is
  not a feature invented for V2.
- **Is `tags_and_sentiments` the only source?** Yes — every legacy indexer variant
  (`index_v1.py`, `index.v3.py` through `.v6.py`, `index-1.py`) derives `review_stats`/`pros_list`/
  `cons_list` from this one Mongo field, no alternate source found anywhere in the codebase.
- **Why is it 0/21,470 locally?** `mongo-product-scripts/STRUCTURE_COMPARISON_REPORT.md` documents
  that current, canonical ("generated") Mongo product documents include `tags_and_sentiments` as a
  standard root-level key alongside `flean_score`/`stats`/`category_data`. The local
  `products_master` snapshot (21,470 docs, 0 with this field) is an older/simpler
  ("no-variant") snapshot missing this and other enrichment — a **dataset/snapshot limitation**,
  not dead or obsolete code. The indexing pipeline change above is therefore correct and
  forward-compatible with any Mongo snapshot that does carry this enrichment (e.g. production).

## Category Browsing dev CLI + validation

New `dev_category_browsing_cli.py` (root), mirroring `dev_search_cli.py`'s conventions exactly:
`.env` loading + placeholder validation, `create_app(config_name='lambda')` init, banner, REPL with
inline `sort:`/`size:`/`page:`/`compare` modifiers, raw V2 OpenSearch query display, wall-clock
timing, and an optional `compare` mode running both V1 (`search_by_subcategory()`) and V2
(`category_browsing.browse()`) side by side, reporting count/missing/extra/ranking differences.

**Validation results** (real local data, `chips_and_crisps` and `ketchup_and_sauces` categories):
- **Sort by price**: identical product sets and identical ranking between V1 and V2 — confirms
  baseline retrieval correctness.
- **Sort by flean_score (default)**: V1's total (260, 240) is **lower** than V2's (353, 342) for
  the same category — expected and consistent with the previously-confirmed `category_paths.keyword`
  bug: that field doesn't exist, so V1 falls back to a `wildcard` query on the analyzed
  `category_paths` text field, which undercounts. V2's exact `term` match against the field's
  correct `keyword` mapping is the more complete, more correct result — not a regression.

## SearchGateway retirement

`search_gateway/` (the `SearchGateway` class, `_build_search()` closure, `get_search_gateway()`
singleton) has been **deleted entirely**. Its logic now lives in
`search_v2/extension/search/core.py` as plain module-level functions — `search(params)` and
`warmup()` — with the same lazy, thread-safe double-checked-locking initialization, just without a
wrapping class (matching every other `extension/*` module's convention, and the user's explicit
instruction not to introduce/depend on a gateway abstraction). All three callers rewired:
`unified_search.py` (`v2_search(gw_params)`), `shopping_bot/__init__.py` (startup warmup),
`dev_search_cli.py` (dev REPL). The one test mocking `get_search_gateway` was updated to mock
`v2_search` directly. Verified: 192/192 pytest, live `/rs/v1/search?query=apple` → `engine: v2,
total: 14` both before and after deletion.

## `unified_search.py` — is it still required?

Investigated per explicit instruction. **Conclusion: it is not a removable V1↔V2 bridge — it is
the production Flask route file for `/rs/v1/search`, `/rs/v1/search/suggest`, and
`/rs/v2/search/suggest`.** Something has to host these routes, the same way `home_page.py` and
`product_api.py` remain as route files after their internal logic moved to native V2 calls. Its
request-parsing and response-shaping logic were already fully V2-native.

One real gap was found and closed this session: **filters-only requests (no query, no subcategory)
and subcategory+filters requests silently fell through to V1**, because:
1. `search_v2/extension/search/core.py`'s query pipeline had no filter-only retrieval mode — an
   empty query string made the `minimum_should_match: 1` clause structurally unsatisfiable,
   returning zero hits regardless of filters. Fixed in `lexical_query_builder.build_query()`
   (skip the should/minimum_should_match requirement when every query variant's text is blank —
   filter clauses alone drive retrieval, exactly like `category_browsing.browse()` already did)
   and `hybrid_search_orchestrator.py` (short-circuit straight to lexical-only for empty query text,
   skipping a meaningless embed-empty-string semantic call).
2. `category_browsing.browse()` didn't accept filters at all — extended its signature with an
   optional `filters: SearchFilters` param, merging `build_filter_clauses()` output into the
   existing category-path bool query.
`unified_search.py`'s branches were then widened: the query-driven branch now also fires for
filters-only requests (passing `q=""`), and the subcategory branch now threads
`validated_filters` through to `browse()`. Verified live: `POST /rs/v1/search` with
`{"filters": {"price_range": "below_99"}}` → `engine: v2, total: 75`, all prices under ₹99;
`{"subcategory": "...chips_and_crisps", "filters": {"price_range": "below_99"}}` → `engine: v2,
total: 289`, correct category and price bound.

The only remaining paths to V1 in `unified_search.py` are: `SEARCH_ENGINE=v1` (explicit operator
override) and a genuine V2 exception under `auto` (safety fallback) or an unresolvable subcategory
name — the same disclosed fallback pattern used everywhere else in this migration, not an
unnecessary dependency.

## Remaining capabilities implemented this session

- **Scanner** (`/api/v1/scanner`) — reuses `extension/search.search()` for the brand+name text
  lookup (top-3 cards), same V2-first/V1-fallback pattern, careful not to treat a legitimate
  zero-result V2 response as a failure requiring fallback.
- **Flean Score** (`/api/v1/flean-score`) — reuses `extension/pdp.fetch_product()` for the raw
  source, then the shared `transform_to_pdp()` for the score/tags, matching the PDP endpoint's
  exact pattern.
- **Catalogue** (`/api/v1/catalogue`) — thin wrapper over `extension/category_browsing.browse()`,
  with `flean_score`/`price` sort aliases translated to `flean_score_desc`/`price_asc` before
  calling (bare `flean_score`/`price` aren't recognized sort keys in `sorting.py`).
- **Products** (`/api/v1/products`) — same V2-first/V1-fallback branching as `unified_search.py`
  (this endpoint is a documented functional subset of it), reusing `_resolve_subcategory_es_path()`
  and `_v1_filters_to_gw_params()` via a local import to avoid a module-load-time circular import
  with `unified_search.py`.
- **Batch PDP** (`/api/v1/products/pdp/batch`) — new `extension/pdp.fetch_products_batch()`, one
  `terms` query for every requested id (matching V1's `mget_products_batch()`'s single-round-trip
  shape), correct `not_found` handling for missing ids.

All five verified: 192/192 pytest + live local endpoint tests, no V2-fallback-to-V1 warnings logged
for any of them.
