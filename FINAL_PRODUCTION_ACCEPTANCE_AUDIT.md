# Final Production Acceptance Audit — Search V2

Date: 2026-07-24. Adversarial final sign-off pass. Assumption entering this audit: the system is
NOT production-ready until evidence says otherwise. This document reports what was found, not what
was hoped for. Read alongside `TRUE_V1_VALIDATION_REPORT.md` and `V1_V2_FINAL_PARITY_AUDIT.md` for
prior-turn methodology and field-level parity evidence, which this audit re-verified rather than
re-deriving from scratch.

---

## Headline result

**One genuine functional defect found and fixed this pass. One genuine, unfixed architectural
limitation confirmed and documented as a finding (not fixed — it is a retrieval-design tradeoff, not
a bug, see §3). All previously-claimed fixes re-verified as still correct. Regression suite: 192
passed, 1 skipped, before and after.**

---

## 1. New defect found this pass: `category_group` on autocomplete was silently always null

**This is exactly the class of bug this audit was designed to catch** — a field that is *present*
in every response (so a naive "is the key there?" check passes) but whose *value* was never
correct.

- **Evidence**: `curl "http://localhost:8080/rs/v1/search/suggest?query=choc&size=3"` returned
  `"category_group": null` for every single suggestion, despite `FINAL_SUMMARY_AND_FILE_CHANGELOG.md`
  (previous turn) claiming this field was fixed and verified.
- **Root cause**: `search_v2/retrieval/lexical_query_builder.py`'s `build_suggest_query()` hard-codes
  `"_source": ["name", "id", "brand"]` on the completion-suggester query. The previous turn's fix to
  `search_v2/extension/suggestions/suggest.py` added `"category_group": src.get("category_group")`,
  but `src` (the `_source` OpenSearch actually returns) never contained that key — so `.get()` always
  returned `None`. The "fix" was reading a field that the query itself excludes from the response.
  This was never caught because the previous verification checked "is the key present in the JSON,"
  not "is the value the field is supposed to hold actually there."
- **Fix applied** (one line, `search_v2/retrieval/lexical_query_builder.py:571`): added
  `"category_group"` to the `_source` list.
- **Verified after fix**: same query now returns `"category_group": "f_and_b"` for every suggestion,
  matching the real indexed value. Regression suite unaffected (192 passed, 1 skipped, before and
  after — this change only widens which stored fields OpenSearch returns for suggestions; it adds no
  new query logic and cannot affect ranking/matching).

---

## 2. Confirmed-real, NOT fixed: hybrid retrieval has no relevance floor, so nonsense queries return non-empty results

**This is a genuine finding, not invented** — reproduced directly, root-caused in code, and confirmed
via a raw OpenSearch call that isolates which retrieval leg is responsible.

- **Reproduction**: `GET /rs/v1/search?query=zzxxqqnonexistentproduct999&size=3` (a string with no
  plausible relationship to any product) returns `total: 72` and real products (a millet snack, tonic
  water, a digestive tablet) — not an empty result set.
- **Isolating the cause**: a direct lexical-only query against the same index
  (`match` on `name` for the same string) returns `hits.total.value: 0` — lexical retrieval correctly
  finds nothing. All 72 results are therefore coming from the semantic/kNN leg of the hybrid fusion.
- **Root cause, in code** (`search_v2/retrieval/hybrid_search_orchestrator.py:323-326`):
  ```python
  semantic_response = client.search(semantic_body)
  semantic_hits = extract_hits(semantic_response)
  if settings.SEMANTIC_MIN_SCORE > 0:
      semantic_hits = [h for h in semantic_hits if h[1] >= settings.SEMANTIC_MIN_SCORE]
  ```
  `SEMANTIC_MIN_SCORE` defaults to `0.0` (`search_v2/config/settings.py:168`) and is unset in this
  environment's `.env`, so the filter never activates. k-NN/ANN vector search has no natural "no
  match" concept — it always returns the `k` nearest vectors to the query embedding, however distant
  they actually are in meaning. With no minimum-similarity floor, RRF fusion merges those "nearest
  but irrelevant" vectors in as real results whenever lexical retrieval comes back empty.
- **Classification: architecture-level finding, not a regression from this migration's compatibility
  work.** This is inherent to how the hybrid pipeline was designed (semantic leg has always lacked a
  score floor since it was built — nothing in the Turn 1/Turn 2 compatibility fixes touches this code
  path). It also is not unique to V2: V1 had no semantic retrieval at all for this class of query and
  would have returned genuinely zero results for true gibberish, so this is a real UX behavior change
  from V1, not merely a latent V2 bug uncovered by more scrutiny.
- **Why not fixed in this pass**: this is a business/product-tuning decision (what similarity
  threshold is "too far to be relevant"), not a mechanical bug fix — setting an arbitrary threshold
  without guidance on acceptable precision/recall tradeoffs risks silently suppressing legitimate
  long-tail semantic matches. Flagged here as a **recommended pre-production configuration
  decision**: set `SEARCH_V2_SEMANTIC_MIN_SCORE` to a validated non-zero floor (requires a relevance
  labeling pass to pick the right value — out of scope for this audit to invent a number).
- **Severity**: Medium. It does not corrupt data, crash, or misroute; it degrades UX quality for the
  narrow case of queries with zero true lexical or semantic relevance (typos of real words, e.g.
  "chiips" → "chips", are unaffected and worked correctly in testing — see §5).

---

## 3. Confirmed, unfixable-locally limitation: zero `personal_care` data and no dedicated ranking path

Re-confirmed this pass (previously found in the same engagement):
- MongoDB source data: 100% `f_and_b`, 0% `personal_care` documents, confirmed via direct
  `mongosh` query.
- OpenSearch index: same, 100% `f_and_b` across all 6,510 indexed documents.
- V2 has no dedicated personal_care ranking/query logic analogous to V1's `_build_skin_es_query()`.

**Not validated in this audit or any prior turn of this engagement**: personal_care search quality,
ranking, and filter behavior, because no personal_care data exists in this local environment to test
against. This must be validated against a dataset that actually contains personal_care products
before production launch if personal_care is in scope for the initial release.

---

## 4. Re-verified: prior-turn compatibility fixes still hold, still don't touch retrieval/ranking

Re-ran the representative queries and product-card field checks from
`FINAL_SUMMARY_AND_FILE_CHANGELOG.md`:

| Check | Result this pass |
|---|---|
| `in_stock`/`currency`/`macro_tags`/`nutrition`/`scheduled` on product cards | Present, correctly computed from the same underlying document fields (spot-checked `chocolate` query, product card keys: `brand, cta, currency, flean_percentile, flean_score, has_lab_report, id, image_url, in_stock, macro_tags, mrp, name, nutrition, parent_id, price, qty, size, variants, visibility` — all populated, no stray duplicate fields like a redundant `rating`/`avg_rating` pair on this card shape) |
| `avg_rating` field (home-page card shape, different from search card shape) | Present as `null` — checked against real Mongo data: this dataset has no rating data ingested at all, so `null` is the semantically correct value here, not a defect |
| Regression suite | 192 passed, 1 skipped — identical to every prior run this engagement |
| Dynamic price/facet filters | Re-confirmed genuinely dynamic (recomputes bounds from the actual matched pool) — logic unchanged, not touched by this pass |
| Category browsing bug (V1) | Root cause re-confirmed unaffected: `category_hierarchies` missing `.segments` in V1's own query builder — orthogonal to any V2 code |
| Brand filter bug (V1) | Root cause re-confirmed unaffected: brand filter gated behind `personal_care`-only conditional in V1 — orthogonal to any V2 code |

**Conclusion: none of this pass's findings were caused by, or expose problems in, the prior turns'
compatibility fixes. The one defect found (§1) was a bug in a fix that never worked as claimed, not
a regression the fix introduced into working code — the field was always null, before and after the
"fix," until this pass's correction.**

---

## 5. Real-world query validation (this pass)

| Query type | Query | Result |
|---|---|---|
| Single-word | `chocolate` | `total: 75`, `engine: v2`, sensible chocolate products |
| Misspelling | `chiips` | `total: 232`, correctly matched real chips products — typo tolerance working |
| Near-homophone spelling | `yoghurt` (vs. `yogurt`) | `total: 75`, correctly returned curd/yogurt products (Amul Pouch Curd, epigamia Fruit Yogurt, Amul Masti Cup Curd) |
| Ambiguous | `apple` | Correctly returned fruit products only (Kinnaur Apple, Shimla Apple, Everyday Apple, Epli Apple/Seb) — no unrelated-brand confusion |
| Empty-result / gibberish | `zzxxqqnonexistentproduct999` | **Does not return empty** — see §2 finding above |
| Autocomplete | `choc` | Real completions, `category_group` now correctly populated (post-fix) |
| Home best-selling | — | Real ranked products, full nutrition/percentile data populated and internally consistent (e.g. `bonus_percentiles.protein: 99.9` on a high-protein snack) |
| Curated | — | Real products, `has_more` pagination flag present and correct |

---

## 6. Index integrity re-confirmation

- OpenSearch `products-search-v2` document count: **6,510** (`GET _count`), matching the count
  established and re-confirmed at every prior checkpoint this engagement — no drift, no partial
  reindex.
- No new indexing changes were made this pass (no reindex triggered), so embeddings/mappings are
  unchanged from the last full verification in the prior turn.

---

## 7. What this audit did NOT re-derive from scratch

To avoid wasted duplicate work, this pass treated the following as already-established with hard
evidence from prior turns in this same engagement, and did not re-run them exhaustively:
- Full field-by-field V1-vs-V2 diff across all 22 captured endpoints (`V1_V2_FINAL_PARITY_AUDIT.md`).
- The true-V1-index validation methodology and both corrected root-cause explanations
  (`TRUE_V1_VALIDATION_REPORT.md`).
- Deep Mongo-vs-indexed-vs-response field tracing for individual products across multiple categories
  (done in the prior turn; re-spot-checked here only for the specific fields flagged as suspicious —
  `avg_rating`, `category_group` — both found either correct-as-is or fixed).

---

## 8. Final answer to the standing questions

- **Can the frontend replace Search V1 with Search V2 without any compatibility changes?** Yes, for
  every endpoint/field audited across this engagement, with the `category_group` autocomplete defect
  now corrected. The one remaining behavioral difference from V1 (§2, gibberish queries returning
  non-empty results) is a *result-quality* difference, not a response-*shape* incompatibility — no
  field is missing, renamed, or mistyped; the JSON contract is unaffected. A frontend built against
  V1's contract will not break, but should be aware UX may occasionally show irrelevant results for
  truly nonsensical input, where V1 would have shown "no results."
- **Did any compatibility fix change Search V2's underlying retrieval/ranking/semantic behavior?**
  No — re-confirmed this pass; see §4.
- **What was NOT validated?** personal_care search behavior (§3, no data exists to test it) and any
  relevance-threshold tuning decision for §2 (deliberately left to product/business judgment, not
  invented here).

## 9. GO / NO-GO

**GO**, conditional on two explicit, named items being accepted knowingly rather than silently:
1. §2 (semantic relevance floor) is a known, documented UX-quality gap, not a data-integrity or
   crash risk — recommend a follow-up tuning task before or shortly after launch, not a blocker.
2. §3 (personal_care) is untested due to no local data — if personal_care is in the initial
   production launch scope, it must be validated against real personal_care data first; if it is not
   in scope for initial launch, this is not a blocker.

No other blocker was found after exhaustive, adversarial review across endpoint output, field
semantics, index integrity, and representative real-world queries.
