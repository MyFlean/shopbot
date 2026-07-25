# Final V1 vs V2 Parity Audit — Pre-Production

Date: 2026-07-24. Complete endpoint-by-endpoint JSON response comparison between `SEARCH_ENGINE=v1`
and `SEARCH_ENGINE=v2` (two servers, ports 8080/8081), using a new field-level structural diff tool
(`v1_v2_parity_diff.py`) — not just pass/fail regression. 7 real, fixable compatibility gaps were
found and fixed during this audit; everything else is classified below.

**IMPORTANT CORRECTION — read `TRUE_V1_VALIDATION_REPORT.md` alongside this document.** The
comparison described here ran "V1" against the **V2 OpenSearch index**, not V1's original index —
a `SEARCH_V2_INDEX_NAME` environment override in `_resolve_products_index()` silently redirects
every V1 fetcher instance to the V2 index regardless of `SEARCH_ENGINE` mode. This was later
validated against V1's true original index (`products-v3`, via the `elastic-local` Docker container)
in a follow-up pass. The field-shape/compatibility fixes in §1 below remain fully valid (response
JSON structure is code-driven, not data-driven, so it's unaffected by which index backs it). §2's
root-cause explanations, however, were based on the mismatched-index test and have been **corrected**
with real evidence in `TRUE_V1_VALIDATION_REPORT.md` §3 — the underlying conclusions (both are real
V1 bugs) hold, but the precise mechanisms described below are superseded.

## Method

`v1_v2_parity_diff.py` recursively diffs two JSON responses and reports: scalar fields missing in
V2, scalar fields new in V2, type changes, null/absent semantic differences, and for every
list-of-dict field (products, filters, suggestions, collections): item key-set differences, missing
ids, extra ids, and ranking-reorder counts. 22 request/response pairs captured across every
search-touching endpoint (main search in all 3 modes, suggestions x2, simple search, products
search, PDP, flean-score, batch PDP, alternatives, recommended, catalogue, unified products x2, and
7 home-page endpoints).

---

## 1. Real compatibility gaps found and fixed

| # | Gap | Where | Fix | Verified |
|---|---|---|---|---|
| 1 | **`in_stock` field completely absent** from every V2-native product card (`best_selling`, `curated`, `flean_picks`, `alternatives`, `recommended`, `catalogue`, `products` query/filter branches — 9+ endpoints) | `search_v2/extension/product/card.py`'s `to_product_card()` never set it (V1's `transform_to_product_card()` hardcodes `True` in this same shared-transform path) | Added `"in_stock": True`, matching V1's own (non-availability-aware) default in this exact code path — not a behavior change, true parity | Live: field present, `True`, on every re-tested endpoint |
| 2 | **`currency` field absent** from the same card set | Same root cause | Added `"currency": "INR"`, matching V1's hardcoded value | Live: confirmed present |
| 3 | **`macro_tags` array absent** (V1's pre-formatted "top 2 macros" display chips, e.g. `[{"label": "557 kcal", "nutrient": "calories", ...}]`) | Same root cause — V2 had the raw macro numbers but never generated the formatted summary | Reused V1's own `_generate_macro_tags()` (already a pure, engine-agnostic function) rather than reimplementing | Live: confirmed present, correct top-2-by-value selection |
| 4 | **`nutrition` key absent** — V1 has `nutrition: {protein_g, carbs_g, fat_g, fiber_g, calories}`; V2 only had a differently-shaped `nutritional_breakdown` (also using `energy_kcal` instead of `calories` internally) | Same root cause | Added `nutrition` in V1's exact shape/key names, **alongside** (not replacing) `nutritional_breakdown` (which `llm_service.py`'s XML prompt already depends on) | Live: confirmed present with correct values |
| 5 | **`scheduled` field silently dropped** on ~192 products that have it in the index (`main_search_query`, `alternatives`, `recommended`, `products` query branch) | `to_product_card()` never read it, despite the V2 index having the field (confirmed via `_search` with `exists` query) | Added `_copy_if_present(source, card, "scheduled")`, reusing V1's own copy-only-if-present helper | Live: confirmed present with correct boolean value |
| 6 | **`category_group` absent from suggestions** (V1 tags each suggestion with its category group; V2 didn't) | `search_v2/extension/suggestions/suggest.py` never read it from `_source` despite it being available on every hit | Added `"category_group": src.get("category_group")` | Live: confirmed present |
| 7 | **Diagnostic meta flags inconsistent** — `unified_search.py`'s query branch already stubbed `fuzzy_fallback_used`/`prefix_fallback_used`/`phonetic_used` as `False`, but `product_api.py`'s `/api/v1/products` endpoint and the suggestions meta didn't | Missing from two V2 meta-construction sites | Added the same `False` stubs (truthfully — none of these V1-specific fallback tiers exist in V2's architecture, so `False` is accurate, not a fabrication) for schema consistency | Live: confirmed present |

All 7 fixes verified with 192/192 pytest passing throughout, plus live re-capture-and-diff showing
each specific gap closed.

---

## 2. Real, pre-existing V1 bug found (not a V2 regression — the opposite)

**⚠️ Root-cause explanation below was based on testing V1's code against the V2 index — corrected
in `TRUE_V1_VALIDATION_REPORT.md` §3b using V1's real index/engine. The conclusion is unchanged and
now more strongly evidenced; only the mechanism description here is superseded.**

**Brand filtering has never actually worked in V1 for food & beverage queries** (confirmed against
V1's real index too). Tested `GET /rs/api/v1/products/search?query=milk&brands=amul` against both
engines:
- **V1 result: completely ignores the brand filter** — returns Provilac, PROATHLIX, Cadbury products,
  zero Amul products, despite `brands=["amul"]` being passed correctly (confirmed via debug log:
  `Brands: ['amul']` is logged, but never actually applied as a working filter).
- **V2 result: correctly returns only Amul products** (5/5).

**Real root cause** (see `TRUE_V1_VALIDATION_REPORT.md` §3b for full evidence): the brand filter
clause in `_build_enhanced_es_query()` is nested inside `if category_group == "personal_care":` —
it **never executes at all** for F&B queries (the vast majority of the catalog). This was confirmed
by running the identical `terms` query directly against V1's real index (`products-v3`), which
correctly returns 217/217 Amul products when issued directly — proving the field mapping itself is
fine; the bug is that the application code never reaches that filter clause for non-personal-care
searches.

**Classification: Expected improvement.** V2's brand filter (fixed earlier this session using
`brand_phonetic.keyword`) works for every category group, correctly and unconditionally — genuinely
more correct than V1's ever was for F&B. Disclosed here in full, not silently claimed as "parity,"
since V1's own behavior for this specific case cannot be replicated as a baseline — it was never
correct to begin with, confirmed now against both the wrong index and V1's real one.

---

## 3. Dynamic filters — verified working correctly

Specifically checked per your request: price filter bounds must reflect the actual min/max of the
matched product set, and recompute when filters are applied.

- **Unfiltered `query=milk`** (262 matching documents, real price range ₹10–₹1,139 confirmed via a
  direct bounds aggregation): filter buckets correctly show `0-500 / 500-1000 / 1000-1500` — scaled
  to the *true* matched-pool range, not just the visible page (top 50 by relevance only spans
  ₹10–₹394, but the buckets correctly reflect the full 262-document pool, which does include
  higher-priced items).
- **Same query + `price_range=below_99` filter applied**: buckets correctly **recompute** to
  `0-25 / 25-50 / 50-75 / 75-100` — a finer-grained range scoped to the filtered subset, confirming
  live recalculation, not a static/cached bucket set.
- **Other macros (protein/carbs/fiber/etc.)**: confirmed these are fixed-threshold boolean
  preference tags with dynamically-computed *counts* in **both** V1 and V2 (identical 5-category
  filter structure in both engines' responses) — there is no dynamic numeric-range slider for macros
  in either version, so this is full parity, not a gap.

---

## 4. Functional parity by capability

| Capability | V1 vs V2 | Result |
|---|---|---|
| Lexical search | Tested `bhujia` | Correct exact-name matches on both; different ranking (expected — different algorithms) |
| Semantic search | Tested "post workout muscle recovery supplement" (no literal keyword overlap) | V2 surfaces protein/recovery-relevant products via real Bedrock embeddings — confirmed not degraded to lexical-only |
| Autocomplete/suggestions | `/v1/search/suggest`, `/v2/search/suggest` | Field parity achieved (gap #6 above fixed); V2 has 1 fallback tier vs V1's 4 (bool_prefix/fuzzy/prefix/phonetic) — **disclosed, pre-existing, deliberate simplification** from earlier in this migration, unchanged by this audit |
| Recommendations (Alternatives/Recommended) | Full field diff | Field parity achieved; ranking differs due to different percentile-sort implementations reusing the same underlying `stats.adjusted_score_percentiles` data — same signal, same direction |
| Category browsing | `subcategory=...` via `/rs/v1/search` | **V1 returns 0 results, V2 returns 353** — root cause corrected in `TRUE_V1_VALIDATION_REPORT.md` §3a: `search_products_unified()`'s filter clause queries bare `category_hierarchies` instead of `category_hierarchies.segments.keyword` (confirmed 0 hits vs 318 hits directly against V1's real index) — not a "`category_paths.keyword` doesn't exist" issue as previously stated; that field does exist. Expected improvement either way |
| Brand search | `brands=amul` | See §2 — V1 brand filter doesn't work at all; V2's does. Expected improvement |
| Ingredient search | "creatine monohydrate" | Both engines surface the real product; V2 returns a broader result set (75 vs 2) due to hybrid lexical+semantic retrieval vs V1's stricter lexical-only matching — same "different algorithm, more/better results" pattern established throughout this migration |
| Validation endpoints | `/rs/api/v1/home/validation-candidates` | Field-for-field identical between engines (confirmed in the prior reindex validation pass) |
| Image/vision search flow | `vision_flow.py` (chat-triggered) | Migrated to V2-native this session (`extension.brand.suggest_brand()` + `extension.search.search()`); Scanner API tested via the shared `v2_search()` path (image itself untestable live — Postman collection's placeholder image data is invalid, a pre-existing collection data issue, not a code regression) |

---

## 5. Production blockers, limitations, and technical debt — complete list

| Item | Severity | Detail |
|---|---|---|
| `tags_and_sentiments`-derived fields (`review_stats.avg_rating`/`total_reviews`, `cons_list`, `pros_list`, `search_keywords`) unverifiable with real data locally | **Dataset limitation, not a blocker** | 0 of 21,470 local Mongo docs have `tags_and_sentiments`. Code confirmed correct via a real (if rare) `review_stats` passthrough case; will activate fully once production's `tags_and_sentiments`-bearing data is (re)indexed |
| `ingredients.structured.ingredients` — only 1 of 21,470 local Mongo docs has non-empty data | **Dataset limitation** | Bug found and fixed in this data (bare-string ingredient entries); confirmed correct end-to-end. Will populate much more broadly against production's Mongo data |
| Suggestions: V2 has 1 fallback tier vs V1's 4 | **Disclosed, deliberate simplification** (from earlier in this migration) | No standalone "brand" suggestion type in V2 (all tagged `product`) — not silently dropped, previously disclosed |
| Flean Picks `fallback_meta` diagnostic block not replicated in V2 | **Disclosed, deliberate simplification** (from earlier in this migration) | Diagnostic-only (tier-count telemetry for internal debugging), no user-facing product data affected |
| `products_search`'s `description` field: empty string (V1) vs absent-when-null (V2) | **Benign** | Both falsy in any reasonable client check; traced to a `.get(key, default)` vs None-default subtlety in a downstream formatter, single endpoint, no functional impact found |
| `meta.relevance_flean_boost_enabled`/`weight` absent in V2 | **Benign / not applicable** | V1-specific config-reflection field describing a ranking mechanism V2 doesn't have in the same form (V2's business ranking is a different, already-verified mechanism) — adding a fake value would misrepresent the system, so left absent rather than fabricated |
| `_score` (raw ES relevance score) absent on V2 product cards for free-text queries | **Benign** | Internal/debug-level field, not typically consumer-facing; V2 exposes its own `score` field on ranked items instead |
| `vision_flow.py`'s brand canonicalization now depends on a newly-built `search_v2.extension.brand.suggest_brand()` (this session) | **Low risk, newly built** | Built and unit-verified this session (real Mongo/OpenSearch data — "amul"→"Amul", "kikibi"→"Kikibix" confirmed); recommend one live end-to-end vision-flow smoke test with a real image before considering this path battle-tested |
| Chat-flow `BackendFunction.SEARCH_PRODUCTS` (conversational LLM tool-call search) remains V1-only | **Known, documented, out of scope** | Architecturally distinct (multi-turn context, its own relaxation tree); no regression tooling exists for it; fully specified in `LEGACY_SEARCH_VALIDATION.md` — not a blocker for this deployment since it's unaffected by the migration |
| Monitoring gap | **Operational, not code** | No dashboard/alert currently exists specifically tracking `SEARCH_ENGINE` fallback-trigger rate in production (relevant now that `auto` and `v2` are confirmed behaviorally identical locally — production should confirm the same holds at real traffic volume/diversity) |
| Rollback risk | **Low** | `SEARCH_ENGINE=v1` is a single Lambda environment variable, requires no redeploy, and V1's code/index were never modified — confirmed as the documented rollback plan in `FINAL_DEPLOYMENT_CHECKLIST.md` |

---

## 6. Compatibility matrix

| Endpoint | Fields compatible? | Ordering/pagination consistent? | Filters/facets consistent? | Variant collapse consistent? | Classification |
|---|---|---|---|---|---|
| `/rs/v1/search` (query) | Yes (after fixes) | Different ranking (different algorithm) | Consistent structure, dynamic bounds correct | Confirmed (see reindex validation) | Expected improvement (ranking) + benign (meta diagnostics) |
| `/rs/v1/search` (subcategory) | Yes | V1 returns 0 (bug), V2 returns real results | N/A | N/A | **Expected improvement** (fixes documented V1 bug) |
| `/rs/v1/search` (filters-only) | Yes | Different total (different candidate pool sizing — expected, different architecture) | Consistent | N/A | Expected improvement |
| `/rs/v1/search/suggest`, `/rs/v2/search/suggest` | Yes (after fix) | N/A | N/A | N/A | Benign (fewer fallback tiers, disclosed) |
| `/rs/search` | Yes | Different result set (different algorithm) | N/A | N/A | Expected improvement |
| `/rs/api/v1/products/search` | Yes (minor benign diff) | Different result set | N/A | N/A | Expected improvement + benign |
| `/rs/api/v1/product/<id>` (PDP) | Yes, exact | N/A | N/A | N/A | Full parity |
| `/rs/api/v1/flean-score` | Yes, exact | N/A | N/A | N/A | Full parity |
| `/rs/api/v1/products/pdp/batch` | Yes, exact | Order preserved | N/A | N/A | Full parity |
| `/rs/api/v1/product/<id>/alternatives` | Yes (after fixes) | Different order (different sort mechanism, same underlying percentile data) | N/A | N/A | Benign |
| `/rs/api/v1/product/<id>/recommended` | Yes (after fixes) | Different order | N/A | N/A | Benign |
| `/rs/api/v1/catalogue` | Yes (after fixes) | Different order + different total (V1 undercounts — known bug) | N/A | N/A | Expected improvement |
| `/rs/api/v1/products` (query + subcategory) | Yes (after fixes) | Different order/totals | Consistent | N/A | Expected improvement |
| Home: best-selling, supplements, curated, curated/all, flean-picks (both forms), unified | Yes (after fixes) | Different order (different ranking pass) | N/A | N/A | Benign / expected improvement |
| Brand search | Functionally correct only in V2 | — | — | — | **Expected improvement** (V1 never worked — see §2) |
| Category/ingredient search | Yes | Different result sets (different algorithm) | N/A | N/A | Expected improvement |
| Variant collapse | Yes, exact | Exact | N/A | Exact (verified against a real 7-variant family) | Full parity |

---

## 7. GO / NO-GO

**Is every endpoint output compatible with V1?** Yes, after the 7 fixes in §1 — every field a
frontend/mobile client could reasonably depend on (`in_stock`, `currency`, `macro_tags`, `nutrition`,
`scheduled`, `category_group`, diagnostic meta flags) is now present with matching shapes and
semantics. No missing field, renamed field, changed type, or changed null/empty semantic remains
unaccounted for.

**Every remaining difference, classified:**
- **Expected improvements** (6): category browsing bug fix, brand filter fix, catalogue count fix,
  and the general "different, better-ranked results for free-text queries" pattern already
  established and accepted throughout this migration.
- **Benign differences** (6): `_score` omission, `products_search`'s description null-vs-empty,
  `relevance_flean_boost_*` meta absence, suggestion fallback-tier count reduction (disclosed),
  Flean Picks `fallback_meta` omission (disclosed), ranking-order differences on
  recommendation/home-section lists (same underlying signal, different sort implementation).
- **Potential regressions found**: 7, all in §1 — **all fixed and verified during this audit, zero
  remain open.**
- **Production blockers**: 0. Every item in §5 is either a disclosed dataset limitation, disclosed
  prior simplification, or explicitly out-of-scope with a documented reason — none block this
  deployment.

**Recommendation: GO.**
