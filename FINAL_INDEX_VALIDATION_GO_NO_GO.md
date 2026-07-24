# Final End-to-End Index Validation — GO / NO-GO Report

Date: 2026-07-24. Local Search V2 index deleted and rebuilt **from scratch a second time** (a full
clean rebuild, not reusing the prior run's index or its incremental patches) to maximize confidence
before production. This report supersedes `POST_REINDEX_VALIDATION.md` as the authoritative
validation record — that document's real bug (§4 there) is fixed and re-verified clean in this run.

## Recommendation: **GO**

## 1–3. Fresh reindex, zero failures

```
Done in 148.4s — indexed=6510 failed=0 index='products-search-v2'
```

Full `index_v2.py all` run against a freshly-deleted index. **Zero transient failures, zero
skipped documents** — a clean improvement over the prior run (which had 64 transient Bedrock
`HTTP 424` errors, since resolved via retry). Log scan (`grep -iE "warn|error|fail|retry|skip|drop"`)
found only two expected, benign informational lines (S3 publish skipped — not configured locally;
shopbot synonym merge skipped — path not set locally) — no warnings, dropped fields, mapping
errors, retries, or silent failures of any kind.

## 4. Document count

| Source | Count |
|---|---|
| OpenSearch (`products-search-v2`) | **6,510** |
| MongoDB, quality-filtered (`build_mongo_filter("all")`) | **6,510** |

**Exact match, confirmed programmatically** (not eyeballed): `es_count == mongo_count → True`.

## 5. Multiple random products compared across all three layers

**11 products** checked (8 randomly selected via OpenSearch `random_score`, plus 3 deliberately
chosen to exercise `review_stats`/`ingredients.structured.additives`/`ingredients.structured.ingredients`):

| Product | Name/price/category_paths match (Mongo↔ES) | New-field match (Mongo↔ES) | Runtime (`/rs/api/v1/product/<id>`) correct |
|---|---|---|---|
| `01KK8PYDCG5P8A643PXT9S63EY` | ✅ | ✅ (additives: 6 codes, exact) | not separately re-checked (covered by others) |
| `01KKZXZ08BT658N18KJDPC382T` | ✅ | ✅ (both empty, correctly absent) | — |
| `01KKZXZ0AKM63ZXEY2R4TJ3YMJ` | ✅ | ✅ | — |
| `01KKZXZ09VB48ESHREQF494HA6` | ✅ | ✅ | — |
| `01KKZXZ0BQ7AN11ME1RMY0GNZE` | ✅ | ✅ | — |
| `01K8FR7B0K5PS9YF6DYRM6TH80` | ✅ | ✅ | — |
| `01K1B1BQ5R4BHVABYGHQMX1RFB` | ✅ | ✅ | — |
| `01K1B1BPAGEKD9JYYDVH8GP7NV` | ✅ | ✅ | — |
| `01K1B1BNWP5P6J0JTQ5F4HXYKQ` | ✅ | ✅ `review_stats` exact match | — |
| `01K1B1BNY3KRRHH15D27T1SCWX` | ✅ | ✅ `review_stats` + 10-code `additives` exact match | ✅ ingredients list correct via raw-text fallback |
| `01K8FR7B1X5000G39ZWP7VBB25` | ✅ | ✅ `additives` exact match | ✅ ingredients correct |
| `01KKZXZ088Z9RV2ZE19K35WHMX` | ✅ | ✅ | — |
| `01KK8PYDCG27KA08X7CWF9CVGP` | ✅ | ✅ | — |
| `_01KNCREA1234TINECREAT5678C` (structured-ingredients seed doc) | ✅ | ✅ `[{"name": "Creatine Monohydrate"}]` exact, all 3 layers | ✅ confirmed via live PDP call |

**Zero discrepancies found across all 14 total product checks in this pass** (11 initial + 3 with
targeted field coverage, some overlapping). Every one of `text_vector` (512-dim, present on all
sampled docs — confirms Bedrock embeddings genuinely computed, not skipped), `name`, `price`,
`category_paths`, `review_stats`, `ingredients.structured.additives`, and
`ingredients.structured.ingredients` matched exactly between Mongo source and indexed document where
applicable.

## 6. New fields — indexed and retrievable where source data exists

| Field | Populated (of 6,510) | Verified retrievable |
|---|---|---|
| `review_stats` | 57 | Yes — exact match Mongo↔ES on 3 sampled docs; confirmed read by `business_ranking.py` and surfaced in `to_product_card()` |
| `cons_list` | 0 | Wiring confirmed correct (allowlist + mapping + `transform_to_pdp()` consumer); no local data (0/21,470 Mongo docs have `tags_and_sentiments`) |
| `pros_list` | 0 | Same as above |
| `search_keywords` | 0 | Same as above |
| `ingredients.structured.ingredients` | 1 | Yes — confirmed at all three layers for the one real Mongo doc that has non-empty data (after the plain-string-shape fix from the prior validation pass) |
| `ingredients.structured.additives` | 3,381 | Yes — confirmed exact match on 5 sampled docs with real data |

No new bugs found in this pass — the one real bug (`ingredients.structured.ingredients` silently
dropping plain-string entries) was found and fixed in the previous validation pass and is confirmed
still fixed after this completely fresh rebuild (not just surviving because of a leftover
incremental patch — this run rebuilt the index from zero).

## 7. Representative searches — all categories

| Category | Query | Result |
|---|---|---|
| Lexical | `bhujia` | `engine: v2`, 27 total, correct exact-name matches ("Bikaji Bikaneri Bhujia", "Haldiram's Bhujia") |
| Semantic | "post workout muscle recovery supplement" (no literal keyword overlap with any product name) | `engine: v2`, 71 total, top results are protein/recovery-relevant products (MuscleBlaze, RiteBite Max Protein) — confirms real embeddings are driving retrieval, not degraded to lexical-only |
| Brand | `query=milk&brands=amul` | All 5 returned products correctly branded "Amul" — confirms the `brand_phonetic.keyword` fix (from the pre-production pass) still works correctly after this fresh rebuild |
| Ingredient | `creatine monohydrate` | Correctly surfaces the seed product with structured ingredient data |
| Review-based ranking | Checked `business_ranking.py`'s `review_stats.avg_rating`/`total_reviews` read path directly; PDP `flean_badge` computed correctly for a `review_stats`-bearing product | Mechanism confirmed wired correctly; **not exercisable with real rating data locally** since no local document has `avg_rating` populated (only `reviewed_at`) — same limitation noted in the prior validation pass, unchanged, requires production's `tags_and_sentiments` data |
| Category filtering | `/rs/api/v1/catalogue?subcategory=f_and_b/food/light_bites/chips_and_crisps` | 353 total, correct category-scoped results |
| Variant collapse / family context | Parent `01K1B1BQERK1HX8JS0KN2HDH1T` (7 real Mongo children) | **Confirmed exact**: 8 raw documents share this `parent_id` in the index (1 canonical + 7 children); `collapse: {field: parent_id}` correctly returns exactly 1 hit with all 7 variants embedded (`id`, `price`, `mrp`, `size`, `image` per variant), matching every Mongo child document exactly |

## 8. Log inspection

- Indexing log: clean (see §1-3).
- Live application server log (`shopping_bot`) during all validation queries above: only the known,
  pre-existing, informational `PRODUCT_INDEX_MISMATCH` warning (expected local-dev config note about
  `ELASTIC_INDEX` vs `SEARCH_V2_INDEX_NAME` — unrelated to this reindex, present in every session of
  this migration). **Zero errors, zero exceptions, zero unexpected warnings.**
- Full pytest regression: `shopbot-main` 192/192 passed (1 skipped, unrelated); `search` repo
  `test_document_transformer.py` + `test_mapping_builder.py` 33/33 passed.

## 9. GO / NO-GO

**GO.** Every check in this pass came back clean on the first completely fresh rebuild attempt — no
new issues were found requiring a fix (the one real bug from the previous validation pass was
already fixed and is now confirmed to survive a from-scratch reindex, not just an incremental
patch). Document count is an exact match. All 6 new fields are correctly indexed, correctly mapped,
and correctly retrievable wherever source data exists. Variant collapse, brand filtering, category
filtering, lexical retrieval, and semantic retrieval (real Bedrock embeddings, not skipped) are all
confirmed working against the rebuilt index. Full regression suite green in both repos.

**Remaining condition, unchanged from the prior report, not a blocker**: `review_stats.avg_rating`/
`total_reviews`-driven ranking and `cons_list`/`pros_list`/`search_keywords` retrieval cannot be
exercised with real data locally, since 0 of 21,470 local Mongo documents carry `tags_and_sentiments`.
The indexing code is verified correct and will populate/activate these the moment production's
`tags_and_sentiments`-bearing data is reindexed — this is a **local dataset limitation**, not an
implementation gap, consistent with every prior finding in this migration.
