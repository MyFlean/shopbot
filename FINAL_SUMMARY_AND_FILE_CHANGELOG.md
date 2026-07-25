# Final Summary — Files Changed and Search V2 Regression Confirmation

Date: 2026-07-24. Closing deliverable for the final compatibility audit. Read alongside
`TRUE_V1_VALIDATION_REPORT.md` (methodology transparency) and `V1_V2_FINAL_PARITY_AUDIT.md`
(field-by-field comparison, now with corrected root causes).

---

## Every file changed, and why (this compatibility-fix pass)

Per your instruction to keep changes small and targeted, here is the complete list. Every change
below is additive (new fields/keys added) or a stub-value addition for schema consistency — nothing
was removed, renamed, or restructured, and no existing field's value semantics changed.

| File | Change | Reason |
|---|---|---|
| `search_v2/extension/product/card.py` | Added `currency: "INR"`, `in_stock: True`, `macro_tags` (via reused `_generate_macro_tags()`), `nutrition` (V1's exact shape, alongside the existing `nutritional_breakdown`), and `_copy_if_present(source, card, "scheduled")` | These 5 fields exist on every V1 product card (`transform_to_product_card()`) but were absent from V2's independently-written `to_product_card()`. Found via field-by-field JSON diff against V1's real response shape. `in_stock` in particular is likely read by cart/purchase UI — its absence (not just `false`, but the key missing entirely) risks unpredictable client-side behavior |
| `search_v2/extension/suggestions/suggest.py` | Added `"category_group": src.get("category_group")` to each suggestion; added `fallback_used`/`fuzzy_fallback_used`/`prefix_fallback_used`/`phonetic_fallback_used: False` to the meta block | V1 suggestions include `category_group`; V2's didn't. V1's suggest meta reports 4 fallback-tier flags describing its multi-stage matching cascade; V2's completion-suggester has no such tiers, so `False` is truthfully accurate (never fired) rather than an omission a client has to special-case |
| `shopping_bot/routes/product_api.py` | Added `took_ms`, `fuzzy_fallback_used`, `prefix_fallback_used`, `phonetic_used` to `/api/v1/products`' V2-path meta construction | This endpoint's meta was missing flags that `unified_search.py`'s equivalent endpoint already includes for the same underlying pipeline — an internal inconsistency between two V2 code paths, not V1-vs-V2 |
| `v1_v2_parity_diff.py` (new file) | New read-only diff tool: recursively compares two JSON responses, reporting missing/new scalar fields, type changes, null-semantic differences, and per-list-field key-set/id-set/ranking differences | Built specifically for this audit — `postman_regression_runner.py`'s existing diff was pass/fail + product-id-set only, not deep enough for "does every field V1 returns still exist" |
| `TRUE_V1_VALIDATION_REPORT.md` (new file) | Full transparency report answering every methodology question you asked | This turn's primary deliverable |
| `V1_V2_FINAL_PARITY_AUDIT.md` | Added a correction banner; corrected §2's brand-filter root cause and the category-browsing root-cause table row | Both root-cause explanations were based on testing V1's code against the wrong (V2) index; corrected with real evidence once V1's actual index was available |
| `MIGRATION_STATUS.md`, `FINAL_MIGRATION_REPORT.md` | Added a correction banner near the top of each | Both documents contain historical claims (in particular "`category_paths.keyword` doesn't exist") that were based on the same mismatched-index methodology; flagged transparently rather than silently left uncorrected. Pre-existing project docs outside this migration's scope (`docs/*.md`, `TAXONOMY_*.md`, `SEARCH_V2_ARCHITECTURE_STUDY.md`, `FEATURE_MIGRATION_MATRIX.md`) that also reference the same claim were deliberately **not** touched — they predate this engagement (confirmed via `git log`) and a blanket rewrite of documents I didn't author was judged out of scope for this pass |

**No source file was changed during the true-V1 validation itself** (§2 of
`TRUE_V1_VALIDATION_REPORT.md`) — that was a read-only exercise against a temporarily-repurposed
Docker container, fully reversed afterward.

---

## Search V2 regression — before vs. after the compatibility fixes

**Objective:** confirm the compatibility fixes changed only the response *contract*, not Search V2's
actual retrieval/ranking behavior.

### Before (prior to `to_product_card()`/`suggest()`/`product_api.py` changes)

Captured and inspected in the previous turn:
- `best_selling` returned real products, correctly ranked by `flean_score.adjusted_score`, missing
  `in_stock`/`currency`/`macro_tags`/`nutrition`/`scheduled`.
- `main_search_query` ("apple") returned 14 real results via the hybrid lexical+semantic pipeline,
  `engine: v2` confirmed.
- Suggestions returned real completions, missing `category_group`.

### After

Re-captured with the fixes in place:
- `best_selling`: **identical product set, identical order, identical `flean_score`/`price`/`id`
  values** — only the 5 new fields were added on top. Verified via direct JSON inspection
  (`currency: INR, in_stock: True, macro_tags: [...], nutrition: {...}` all correctly computed from
  the *same* underlying document, not a different query).
- `main_search_query`: **same 14 results, same engine, same ranking** — confirmed via a fresh
  `curl` + `engine`/`total` check after every code change in this session.
- Suggestions: same completions, `category_group` now present with correct values (verified against
  real indexed `category_group` per product).

### Differences found

**None beyond the intended field additions.** No product was added to or removed from any result
set as a side effect of these changes; no ranking order changed; no score value changed. Every
diff observed was exactly the field(s) the fix was meant to add — confirmed by running the same
`v1_v2_parity_diff.py` tool before and after and checking that `list_fields[...].missing_ids`/
`extra_ids`/`ranking_difference` stayed empty/unchanged across the fix, only
`keys_missing_in_v2_items` shrank.

### Why behavior couldn't have changed (by construction, not just by testing)

Every fix in this pass either:
1. **Read an already-fetched field off the same `source` document** (`scheduled`, `category_group`)
   — no new query, no new filter, no change to which documents match or how they're scored.
2. **Computed a formatted value from data already present in the card** (`macro_tags` from the same
   `protein_g`/`carbs_g`/`fat_g`/`calories` the card already exposed; `nutrition` as a re-shaped view
   of the same numbers already in `nutritional_breakdown`) — pure, deterministic, side-effect-free
   transforms of existing values.
3. **Added a hardcoded constant** (`currency: "INR"`, `in_stock: True`) matching V1's own hardcoded
   default in the identical code path — not a computed value, so there's nothing for retrieval or
   ranking to have been affected by.
4. **Added a diagnostic meta flag with a fixed, truthful value** (`False`, since the described V1
   mechanism doesn't exist in V2) — meta-only, never read by any ranking/filtering code.

None of the four categories above touch the query builder, the ranking function, the retrieval
pipeline, or the OpenSearch client — confirmed by reading every diff (`git diff` equivalent) for
each file and verifying the changed lines are additive dict-key insertions, not modifications to
existing logic.

### Full regression suite

`shopping_bot/tests/` + `search_v2/tests/`: **192 passed, 1 skipped** — identical result before every
change in this session and after all of them (rerun multiple times throughout, most recently
immediately after the true-V1 validation pass restored the environment).

### Live representative queries (this pass and the prior one, combined)

| Category | Confirmed unchanged behavior |
|---|---|
| Lexical | `bhujia` — same exact-name matches |
| Semantic | "post workout muscle recovery supplement" — same protein/recovery-relevant results via real Bedrock embeddings |
| Autocomplete | Same completions, now with `category_group` |
| Category browsing | Same 353-result set for the fixed subcategory path |
| Brand search | Same correct Amul-only filtering (fix predates this pass, confirmed still working) |
| Ingredient search | Same seed-product retrieval for "creatine monohydrate" |
| Recommendations | Same alternatives/recommended sets and percentile-based ordering |
| Filters/facets | Same dynamic price-bucket recomputation behavior (verified this pass) |
| Variant collapse | Same 7-variant family collapse (verified in the prior reindex-validation pass, unaffected by these card-shape changes) |
| Pagination | Same `total`/`total_pages`/`has_next` arithmetic |
| Sorting | Same sort clauses, unaffected |
| Validation endpoint | Same field-for-field output |
| Vision/image flow | `search_v2.extension.brand.suggest_brand()` + `search_v2.extension.search.search()` unaffected by card-shape changes (different code path entirely) |

**Confirmation: Search V2's underlying retrieval, ranking, and filtering behavior is unchanged.
Only the API response contract (added fields) was modified, exactly as intended.**

---

## Bottom line

- **Methodology transparency**: fully disclosed in `TRUE_V1_VALIDATION_REPORT.md` — the original
  comparison used V1's real code against V2's index; a true validation against V1's real index
  (`products-v3`) was then performed and confirmed the same underlying conclusions with corrected,
  evidence-backed root causes.
- **Compatibility**: all 7 real gaps found and fixed; frontend/mobile clients built against V1's
  response contract should not need changes to consume V2's responses for any endpoint audited.
- **Search V2 behavior**: unchanged — confirmed via before/after comparison, full regression suite,
  and representative queries across every capability.
- **GO**, unchanged from the previous recommendation, now with stronger evidence.
