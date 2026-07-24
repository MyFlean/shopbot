# Search V2 Production Architecture Study

Date: 2026-07-23
Scope: read-only investigation across two repos — `shopbot-main` (the application, this repo)
and its sibling `search` repo (`/Users/anant/code material/vscode/professional/flean_es_test/search`,
the indexing pipeline). No code changed, no Git operations performed, no credentials inspected.
All production queries below were run against the already-active, user-confirmed production
environment (verified via the `kothambir` result-count fingerprint, consistent with prior
investigations this session) — no additional environment switch was needed for this phase.

---

## 1. Architecture comparison — Model A / B / C

First, what these models actually *are* in the code, established from evidence, not inferred from
the prompt:

- **`shopping_bot/data_fetchers/es_products.py:186-201`** (`_resolve_products_index()`): the
  legacy V1 fetcher's index is **not** hardcoded. It resolves in this order: explicit override →
  `SEARCH_V2_INDEX_NAME` env var → `ELASTIC_INDEX` env var → `"products_master"` default. Model
  A/B/C are **the same code, three different environment configurations** of this one function —
  not three different branches or implementations.
- **Model A** = `SEARCH_V2_INDEX_NAME` unset or pointed at a different index than `ELASTIC_INDEX`
  → V1 and V2 genuinely query separate indices.
  **Model B** = both env vars point at the same index (whatever V2's own index is —
  `products-search-v2` per `search_v2/config/settings.py`'s default).
  **Model C** = current production: `SEARCH_V2_INDEX_NAME=products-search-v3`,
  `ELASTIC_INDEX=products_master` (still the code default) — mismatched on purpose, and
  `_resolve_products_index()` prefers `SEARCH_V2_INDEX_NAME`, so **both V1 and V2 end up
  querying `products-search-v3`**. The app logs this explicitly at startup
  (`es_products.py:215-219`, `"⚠️ INDEX_MISMATCH..."`) — this isn't a hidden side effect, it's a
  detected-and-logged configuration state.

### Model A (separate indices)

**Pros:** each system's index is purpose-built and never has to compromise its schema for the
other. Simplest to reason about in isolation.
**Cons (confirmed, matches your stated symptom):** any endpoint that needs BOTH V1-style data
(stats, availability, category browsing) and V2-style capability (semantic retrieval, product
intent) has nowhere consistent to get it — whichever endpoint's code path queries the "wrong"
index for what it needs will fail or return stale/inconsistent data. This is architecturally
guaranteed to cause the "search works, category browsing fails, picks fail, APIs inconsistent"
pattern you described, because different routes in `shopbot-main` (home APIs, PDP, unified
search) call different fetchers, and only one of the two indices is current at any given time
unless both are kept in lockstep by a separate indexing job for each.
**Risk:** two indexing pipelines to keep in sync (or one stays stale) is a standing operational
burden, not a one-time cost.

### Model B (V1 and V2 both point at V2's own index, `products-search-v2`)

**Technically feasible? Yes, with one confirmed caveat.** `search_v2/indexing/mapping_builder.py`
(search repo) defines the V2 mapping as a **documented superset** of the legacy schema — its own
comments state fields like `category_hierarchies`, `category_data`, `stats`, `flean_score`,
`availability`, `visibility`, `price`, `mrp`, `images`, `variants`, `package_claims` are "reused
as-is" from the legacy shape, and in at least one case the comment says this was "confirmed
directly against [`mappings.products-v3.json`], not assumed" (`mapping_builder.py:271-276`).
Structurally, V1's read patterns should mostly work against a V2-shaped index.

**The one confirmed break (found empirically, not assumed):** V1's fetcher queries
`category_paths.keyword` in **29 places** in `shopping_bot/data_fetchers/es_products.py` (subcategory
search, best-selling-by-category, category-path filtering). Search V2's mapping defines
`category_paths` as a plain `"type": "keyword"` field — **not** a `text` field with an automatic
`.keyword` sub-field — so `category_paths.keyword` does not exist as a queryable field at all
against this schema. Verified directly against the live production index (identical mapping to
what V2 would build):
```
query category_paths.keyword -> total: 0
query category_paths (no .keyword)  -> total: 97
```
Same real category path, same index, only the field reference differs — 0 vs. 97. **This is a
confirmed, reproducible defect that would affect Model B identically to Model C**, since both
share the same mapping shape. It is not new to Model C; it's inherent to the schema Search V2's
mapping_builder.py produces, wherever it's pointed.
**Missing functionality:** anything in `es_products.py` using `category_paths.keyword` — that's
subcategory browsing, category-scoped best-selling aggregation, and any home/category API that
filters by exact category path — is silently broken (0 results, not an error) against any
V2-shaped index, Model B or Model C alike.

### Model C (current production — `products-search-v3`, both point here)

Same mechanism as Model B, different index name. Two things verified specifically for this
model:

1. **The `category_paths.keyword` bug above is present here too** — confirmed directly against
   the live production index, not inferred from Model B's analysis.
2. **Both synonym layers are empty** (full detail in §3) — this is a data/indexing gap specific
   to how *this particular index* was built, not an inherent property of "unified index"
   architectures in general; a correctly-run indexing pipeline would populate them the same way
   for Model B.

Your own observation that "Postman collection passes, search works, picks work, category
browsing works" and my finding that a direct subcategory query returns 0 results are not
necessarily contradictory: `flean-picks` (tested, returns success) and likely whatever the
Postman collection's "category browsing" checks exercise may go through a different code path
(e.g. curated ID lists, or the Search V2 gateway's own `category_path_prefix` filter — which is a
*different* mechanism, built against `category_hierarchies` nested segments, not
`category_paths.keyword`) that doesn't hit this specific broken field reference. I did not
exhaustively test every category-related endpoint — flagging this as **evidence of a real,
narrow defect**, not a claim that "category browsing" is broken everywhere; see §5 for what
additional testing would nail this down precisely.

**Higher OpenSearch latency:** already exhaustively investigated in the prior session's transport
study — confirmed to be real network/infrastructure round-trip time to the managed OpenSearch
domain, not a schema or query-shape difference between models (the query shapes are identical
regardless of which model routes to which index).

---

## 2. Index comparison

| | Legacy (`products-v3` / `products_master`) | Search V2 own index (`products-search-v2`) | Unified V3 (`products-search-v3`, current prod) |
|---|---|---|---|
| Built by | `search` repo root: `index.products-v3.py` / `index.products-v3.aws.py` | `search_v2/indexing/index_v2.py` + `mapping_builder.py` | Same pipeline as V2's own index (same mapping shape, confirmed) — index name overridden |
| `category_data`, `flean_score`, `stats`, `availability`, `visibility`, `price`/`mrp`/`size`, `images`, `package_claims` | Present (`mappings.products-v3.json`) | Present, explicitly modeled to match legacy (mapping_builder.py comments) | Present (verified live: PDP endpoint against this index renders `flean_badge`, category data correctly) |
| `category_paths` type | `keyword` (verified in `mappings.products-v3.json`) | `keyword` (mapping_builder.py:283-ish) | `keyword` (verified live via `get_mapping`) — **identical to legacy**, so the `.keyword`-suffix bug in `es_products.py` is not new to V3, it would break against ANY of these three the same way |
| `category_hierarchies` | `nested` w/ `segments: keyword` | Same shape, explicitly cross-checked against legacy (mapping_builder.py:269-278) | Same (nested queries in `lexical_query_builder.py` work against it) |
| `text_vector` (kNN) | **Absent** | Present, `knn_vector`, dim from `SETTINGS.EMBEDDING_DIM` | Present (semantic/hybrid search works in production, confirmed this session) |
| `vernacular_synonyms` field | **Absent** | Present, `text` field, Layer 2 synonym design | Present in **mapping** but **empty/null on every sampled document** (verified live — see §3) |
| Global synonym filter (`v2_synonym_search`, synonym_graph) | N/A (no such analyzer in legacy) | Inlined from `SynonymBuilder` output at build time | **Present but only the OpenSearch-required placeholder** (`placeholder_term_a, placeholder_term_b`) — verified live via `get_settings` |
| Phonetic subfields (`name.phonetic`, `brand.phonetic`) | Absent | Present when `ENABLE_PHONETIC` | Present (confirmed — phonetic-boosted clauses appear in the live query DSL this session) |
| Product Intent fields (`product_type`, `product_type_confidence`) | Absent | Present | Present |
| Personal-care nested fields (`skin_compatibility`, `efficacy`, `side_effects`) | Present (legacy already had these) | Present, same nested shape | Present |
| Picks / best-seller support | Via legacy's own aggregation/curated-ID logic | Same `ElasticsearchProductsFetcher` methods, now pointed at this index | Confirmed working (tested `flean-picks` and `best-selling` endpoints this session — both succeed) |
| Semantic support | None | Full (hybrid lexical+semantic via gateway) | Full (confirmed — `meta.engine="v2"`, `HYBRID`/`LEXICAL_ONLY` routing observed this session) |

**Bottom line on schema:** Search V2's mapping is a genuine, deliberately cross-checked superset
of the legacy schema for almost every field. The one confirmed structural landmine
(`category_paths.keyword`) is baked into the mapping design itself (both legacy and V2 use bare
`keyword`, so this isn't something V2 broke — it's a mismatch between the schema's actual shape
and some of V1's own query code, which may predate Search V2 entirely and simply never surfaced
because V1 always had its own separately-maintained index before). The two synonym gaps in
`products-search-v3` specifically are a **data population problem at this index's build time**,
not a schema limitation — the mapping has the right fields and filters defined; they're just
uninitialized in what's currently indexed.

---

## 3. Synonym investigation

### Where do these synonyms originate — indexing or chatbot?

**Indexing.** Confirmed directly from `search` repo, `search_v2/synonyms/synonym_builder.py`
(module docstring and code):

- **Pipeline:** `SynonymBuilder` class merges three sources: (1) vernacular/produce JSON —
  `search_v2/synonyms/sources/fruits_veggies_name_with_synonyms_updated.json` (this is where
  Hindi/vernacular fresh-produce terms live in the *indexing* repo — a sibling/superset of the
  file shopbot-main's `canonical_produce.py` reads for its own, narrower Fresh Produce
  Identification feature); (2) `sources/business_synonyms.csv` (merchandising-editable
  equivalence groups); (3) any pre-existing repo synonym file, auto-detected.
- **Output, two layers** (per the module's own "two-layer synonym design"):
  - Layer 1: `build_synonym_file()` → a flat equivalence-group list, inlined directly into the
    index mapping's `analysis.filter.v2_synonym_index` (basic, index-time) and
    `v2_synonym_search` (`synonym_graph`, search-time) — see `mapping_builder.py:74-81`.
  - Layer 2: `build_product_synonym_map()` → `{product_id: [terms]}`, attached per-document onto
    the `vernacular_synonyms` text field at indexing time (`mapping_builder.py:246`, "Layer 2 of
    the synonym design").
- **Why inlined, not `synonyms_path`:** `mapping_builder.py`'s own comment (lines ~21-30) explains
  this is deliberate — this is a *managed* AWS OpenSearch Service domain, and `synonyms_path`
  would require uploading the file via AWS's package mechanism (more operational overhead); at
  ~136 equivalence groups, inlining directly into the mapping JSON was judged sufficient, with an
  explicit note to switch to a package if the set grows into the thousands.

### Are they indexed in production right now?

**No — verified empirically, both layers, directly against the live production index:**

```
Global synonym filter (v2_synonym_search.synonyms):
  count = 1
  content = ['placeholder_term_a, placeholder_term_b']
  (i.e., the literal OpenSearch-required non-empty placeholder — the real merged set was never loaded)

Per-product vernacular_synonyms field, sampled on 3 real coriander products
(the same ones "dhaniya"/"kothambir" resolve to):
  all three: vernacular_synonyms = None
```

This is the direct, confirmed explanation for "production appears to be missing fresh
vegetable/fruit/Hindi synonyms" — not a missing feature, a **build-time data gap**: whoever
created/rebuilt `products-search-v3` ran (or the process ran) `build_mapping()`/indexing without
wiring in `SynonymBuilder`'s output, leaving both synonym layers inert. This is exactly why
"dhaniya" (a literal substring of the product's own name, "Coriander Bunch (Dhaniya Patta)")
returns results while "kothambir" (a real, curated synonym for the same product, per the sources
file) returns none — the match that should come from either synonym layer never had data to work
with.

### Are these currently living only in chatbot code — and is that intentional?

Partially, but for a **different, narrower feature**. `shopbot-main`'s
`search_v2/query_processing/canonical_produce.py` + `produce_synonyms.json` implement **Fresh
Produce Identification** — a curated, exact/fuzzy-matched allowlist of catalog IDs for produce
items specifically (confirmed and extensively tested in a prior session this conversation). This
is intentional and works as designed *for that one feature* — it is not, and was never meant to
be, a substitute for the index-level synonym layers described above, which cover **all product
categories**, not just fresh produce, and operate as general BM25 text-relevance signals rather
than a hard id-restriction.

### Would adding them as a chatbot resource file be sufficient?

**No — for a structural reason, not a preference.** The two missing layers are:
1. An OpenSearch **analyzer-level filter** (`synonym_graph`), which is compiled into the index's
   analysis settings at index-creation time. A chatbot-side resource file cannot retroactively
   inject query-time synonym expansion into an already-built index's own analyzer — OpenSearch
   applies `synonym_graph` during **query parsing on the server**, before the client ever sees
   results; there's no client-side equivalent that produces the same recall behavior across
   every field/query type it currently touches.
2. A **per-document field** (`vernacular_synonyms`), populated once per product at indexing time.
   A chatbot resource file has no document to attach a value to — it can only affect the query
   side, not what's already been written into existing documents.

Both require a **reindex (or at minimum, closing/reopening the index with updated analysis
settings and reindexing documents to populate the field)** — an operational/indexing task owned
by the `search` repo's pipeline, not a Lambda packaging or chatbot-code change.

**What a chatbot resource file *can* do** (and already does, for the narrower fresh-produce case):
extend `canonical_produce.py`'s curated allowlist, or extend general-purpose typo-correction via
`vocabulary_builder.py`'s vocabulary — these are legitimate, already-working query-time layers,
but they only ever affect the *specific* features they're wired into (fresh-produce hard-filter
and typo correction respectively), not general lexical relevance across the whole catalog the way
the missing index-level synonym layers would.

### If a chatbot-side resource were still added as a stopgap, how would Lambda packaging work?

For completeness, since you asked "if yes, determine how" — even though the answer to "would it
be sufficient" is no for the *general* synonym problem: any additional resource file placed
inside `shopbot-main/search_v2/` (matching where `produce_synonyms.json` and `vocabulary.json`
already live) would automatically be included in the Lambda package, because
`deployment/lambda/Dockerfile.build` already has `COPY search_v2/ /build/package/search_v2/`
(confirmed present and working — this was fixed and verified earlier this session as part of the
production-readiness pass). No new COPY line would be needed for a file placed there specifically.
This only solves packaging, not the underlying index-level gap described above.

---

## 4. Recommendation

**Recommend Model B/C's underlying approach — one unified index — over Model A, but only after
two concrete, already-identified fixes are made; do not recommend "as-is" Model C without them.**

Evidence, not opinion:

- **Correctness:** Model A is structurally guaranteed to produce the "search works, category
  browsing fails, picks fail, inconsistent APIs" symptom you already observed, because different
  routes read from different indices with no mechanism keeping them in sync. A unified index
  removes that entire class of problem by construction — confirmed by the fact that Model C
  already gets PDP, best-selling, and flean-picks working correctly against a single index
  (verified live this session).
- **Maintainability:** one indexing pipeline (`search_v2/indexing/`) to run and monitor, one
  mapping to evolve, instead of two that must be kept in lockstep. `search_v2/README.md`'s own
  framing ("the only part of this repository that should be maintained going forward") reflects
  the same conclusion from the indexing-repo side.
- **API compatibility:** the schema comparison in §2 shows Search V2's mapping was deliberately
  built as a superset of the legacy schema, and empirically, PDP/best-selling/picks already work
  correctly against it in production. The one confirmed break (`category_paths.keyword`) is a
  **fixable, narrow bug in `es_products.py`'s field references**, not a fundamental architecture
  flaw — it would need fixing under Model B just as much as Model C, since both share the mapping
  shape.
- **Future semantic search support:** only Model B/C's index has `text_vector`/`knn_vector` at
  all — Model A's legacy index structurally cannot support hybrid/semantic retrieval without
  becoming Model B or C anyway.
- **Indexing simplicity:** one pipeline (`index_v2.py`) already produces a mapping that's a
  documented superset — simpler than reconciling two independently-evolving schemas.
- **Production readiness:** not yet, on the current production build specifically — two concrete,
  scoped gaps need fixing before Model C (or B) is fully production-ready as currently built:
  1. **Re-run indexing with `SynonymBuilder`'s output wired into `mapping_builder.build_mapping(synonyms=...)`** for whichever index is production's index of record — this is an indexing/data
     operation in the `search` repo, not a shopbot-main code change.
  2. **Fix `es_products.py`'s `category_paths.keyword` references** (29 occurrences) to query
     `category_paths` directly (matching the mapping's actual `keyword` type, verified to return
     97 real hits vs. 0 for the `.keyword` variant on the same data) — this is a shopbot-main
     code fix, explicitly not made in this investigation per your "do not modify search logic"
     constraint; flagging it as the concrete next action, not implementing it.

Between Model B (V1+V2 → V2's own `products-search-v2`) and Model C (both → the
separately-named `products-search-v3`) specifically: I found no functional difference — same
mapping shape, same code paths, same bugs, same fix required. The only distinction is naming/
which index is treated as canonical. I have no evidence either way that the index NAME itself
matters; recommend picking whichever one is easier to operationally standardize on going forward
(likely V2's own `products-search-v2`, so the "-v3" naming doesn't imply a schema version bump
that doesn't actually exist — the mapping is not "v3" of anything, it's the same Search V2 schema
family under a different name).

## 5. Where evidence is insufficient — what additional testing is needed

- I tested subcategory browsing with exactly one real category path and got 0 results due to the
  `.keyword` bug. I have **not** exhaustively tested every category-browsing-adjacent endpoint
  (dynamic filters by category, best-selling-by-category aggregation specifically, home category
  tiles) — recommend running the full Postman collection's category-browsing checks alongside a
  raw `category_paths` (no `.keyword`) query to see exactly which endpoints are silently returning
  zero/degraded results versus which ones use the `category_hierarchies`-nested path instead and
  are unaffected.
- I did not get the background field-by-field `document_transformer.py` vs. `es_products.py`
  read-field diff agent's results in time for this report — if it surfaces additional missing
  fields beyond `category_paths.keyword`, I'll fold them in as a follow-up addendum rather than
  delay this report further.
- The `flean_badge` I observed on one PDP call showed `score: 10` but `level: "unknown"` /
  `"Not Rated"` — I did not chase this further; it may indicate a percentile/stats field that's
  present but incompletely populated for that specific product, or may be normal for that
  product's data. Worth a targeted check if percentile-based badges are reported as wrong in
  production.

No local testing was required for this investigation — everything was read from the repositories
and queried against the already-active, previously-confirmed production environment. If you want
the same comparisons run against local for contrast, tell me and I'll say "I now need the LOCAL
index" and wait for confirmation before switching.

---

## 6. Addendum — field-by-field diff (background analysis, completed after §1-5 above)

A deeper, line-cited pass through `search` repo's `document_transformer.py`/`index_v2.py` (branch
`search_v2_c`) against every field `es_products.py` actually reads, confirming and extending the
structural read in §2-3. Every V1 read uses `.get(default)`/`isinstance` guards, so **nothing in
this list crashes** — all of it degrades silently instead, which is worse to catch in testing, not
better.

**Confirmed compatible (same name, same shape):** `id, parent_id, name, brand, price, mrp, size,
visibility, scheduled, images, category_group, category_paths, description`; `variants[].{id,
price, mrp, size, image}`; `availability.*`; all of `category_data` (nutrition works because V2
keeps both the original spaced keys in `nutri_breakdown` *and* a snake_case
`nutri_breakdown_updated` for 9 mapped nutrients, and V1's `_extract_nutrition_from_source` reads
both); all of `stats.*_percentiles`; all of `flean_score.*`; `package_claims.dietary_labels`;
`skin_compatibility`/`efficacy`/`side_effects`.

**Confirmed gaps, beyond the `category_paths.keyword` bug already reported:**

| V1 reads | Written by V2's indexer? | Effect | Severity |
|---|---|---|---|
| `review_stats.avg_rating`, `review_stats.total_reviews` | **No** — computed in `document_transformer.sanitize_for_es()` but then dropped by the indexing allowlist (`document_transformer.py:452-493` doesn't include `review_stats`) | Reads as `None`; ranking's `field_value_factor` on these falls to its `missing` default; the `review_stats.total_reviews >= N` range filter matches nothing | **Significant** — review-based ranking/filtering is silently inert everywhere it's used |
| `cons_list` | **No** — same allowlist drop | Empty PDP "watch-outs" list | Moderate |
| `ingredients.structured.ingredients`, `ingredients.structured.additives` | **No** — V2's `_finalize_ingredients()` only emits `{raw_text, normalised}` | PDP falls back to a raw-text comma-split; structured ingredient/additive lists are lost | Moderate |
| `package_claims.health_claims`, `package_claims.marketing_keywords` | **Conditional** — only preserved if Mongo stored `package_claims` as a dict; if Mongo stored a list, V2's transformer replaces it with `{"dietary_labels": [...]}` only | Empty PDP highlights for products where the source shape was a list | Data-shape-dependent |
| `package_claims.dietary_labels` | Yes, but forced **UPPERCASE** in the list-source case | Cosmetic — `"GLUTEN FREE"` instead of proper case | Cosmetic |
| root `tags`, root `highlight_tags` | **No** (not in the allowlist) | V1 has a fallback path, but the primary path (`category_data.tags.highlight_tags`) only works if Mongo nested it under `category_data` in the first place | Degrade only for the root-level shape |
| `hero_image`, `hero_image.1080` | **No** — V2 writes flat `images` only | Mostly vestigial since transforms already prefer `images`, but any direct `hero_image` consumer gets nothing | Minor |
| Query-side `name_sayt._2gram`/`._3gram` | **No** — V2's mapping puts shingle/SAYT subfields on `name` itself; it never defines a `name_sayt` field with those subfields at all | Any V1 query clause referencing these subfields silently matches nothing (same class of bug as `category_paths.keyword`) | Degrade (query-side, same root cause as the category bug: V1 query code written against the older field-naming convention) |

This strengthens rather than changes the recommendation in §4: the schema is compatible in
structure and naming for the fields that matter most (nutrition, scoring, availability,
variants), but there is a **recurring pattern** — V1 query/read code in `es_products.py` was
written against field-naming conventions (`.keyword` multi-fields, `name_sayt` as its own
shingled field, `review_stats`/`cons_list`/structured `ingredients` as first-class indexed
fields) that Search V2's indexer either restructured or never populated. Fixing this is a
**shopbot-main code change** (align `es_products.py`'s field references to what the indexer
actually writes) plus, for `review_stats`/`cons_list`/`ingredients.structured`, an **indexing
change** (add them to `document_transformer.py`'s `ALLOWLIST_INDEX_FIELDS` if that data is wanted
in search results) — neither implemented here, both out of scope per your "do not modify search
logic" / "do not modify indexing" constraints. Flagging as the precise next-action list, not
acting on it.
