# True V1 Validation Report — Complete Transparency

Date: 2026-07-24. This document answers every question in your message precisely, without
summarizing away the methodological gap you identified.

---

## 1. Exactly how the previous V1 vs V2 comparison was performed

**Which index did "V1" actually use in the previous audit? The V2 index — not V1's original index.**

Here is the exact mechanism, found in `shopping_bot/data_fetchers/es_products.py`:

```python
def _resolve_products_index(index_override: Optional[str] = None) -> str:
    """Canonical index selection for product APIs."""
    if index_override and str(index_override).strip():
        return str(index_override).strip()
    search_v2_index = str(os.getenv("SEARCH_V2_INDEX_NAME") or "").strip()
    if search_v2_index:
        return search_v2_index          # <-- this branch always wins
    legacy_index = str(os.getenv("ELASTIC_INDEX") or "").strip()
    if legacy_index:
        return legacy_index
    return "products_master"
```

`ElasticsearchProductsFetcher.__init__()` calls this to set `self.index`. Since this repo's `.env`
always has `SEARCH_V2_INDEX_NAME=products-search-v2` set, **every V1 fetcher instance — regardless
of `SEARCH_ENGINE` mode — was silently pointed at the V2 OpenSearch index, never at V1's own
`ELASTIC_INDEX=products_master`.** This is visible in every server log this whole session as the
`PRODUCT_INDEX_MISMATCH` warning, which I had read past without recognizing its full implication for
the comparison's validity until your question forced a closer look.

**Direct answers to your specific questions:**

- **Which index did V1 use during the previous comparison?** The newly-built V2 index
  (`products-search-v2`), not V1's real index. Both "V1" and "V2" in that comparison queried the
  exact same 6,510 documents with the exact same OpenSearch 2.15 mapping.
- **Was V1 actually executed, or were fixtures/snapshots used?** V1's real, unmodified code
  (`ElasticsearchProductsFetcher`, `_build_enhanced_es_query`, `transform_to_product_card`, etc.)
  was genuinely executed — not a fixture, not a snapshot, not a mock. What was **not** real was the
  *data store* it queried against.
- **Were requests sent through the real V1 runtime and the real V2 runtime?** Yes for the
  application code (real Flask app, real route handlers, real V1 fetcher class, real V2 extension
  modules) — no for the data layer, which was shared between them.
- **Why this still provided some confidence, and why it wasn't sufficient on its own:** It fully
  validates *response shape/field compatibility* — V1's transform functions produce the same JSON
  structure regardless of which index backs them, since the transform code path doesn't change based
  on index content. It does **not** validate *behavioral correctness of V1's own query-matching logic
  against data and a mapping V1 was actually designed for* — a query clause that happens to accidentally
  match (or fail to match) against V2's schema by coincidence says nothing reliable about whether that
  same clause works against V1's real schema. This is exactly the gap you identified, and it mattered
  for two specific conclusions: category browsing and brand filtering (see §2 and §3 below).
- **What was validated vs not validated, stated explicitly:** Validated: JSON field names, types,
  nesting, presence/absence, meta shape, pagination shape, variant/suggestion/recommendation payload
  shapes — all of this is *pure code output*, unaffected by which index. Not validated (until this
  pass): whether V1's actual category-filter and brand-filter query clauses genuinely match real
  documents against V1's real mapping, and whether the "V1 bug" conclusions were real bugs or
  artifacts of a schema mismatch.

---

## 2. True V1 validation — performed

Found the container: **`elastic-local`** (not "elasticsearch-local" — matches your "approximately"
caveat), image `docker.elastic.co/elasticsearch/elasticsearch:8.15.0`, configured to bind host port
9200 (confirmed via `docker inspect .HostConfig.PortBindings` — identical to `opensearch-2-15`'s
port, exactly as you said).

**Exact steps taken:**
1. `docker stop opensearch-2-15` (data preserved — no volume needed; stopping ≠ removing, and Docker
   retains a stopped container's writable-layer filesystem intact).
2. `docker start elastic-local` (reused its existing port-9200 binding — no container recreation, no
   data risk).
3. Confirmed it came up as real Elasticsearch 8.15.0 and inventoried its indices:
   `products-v3`, 6,476 real documents — **this is V1's genuine original index**, never touched by
   any part of this migration.
4. Started a **third** server process (port 8082) with `SEARCH_ENGINE=v1`, `ES_URL=http://localhost:9200`,
   `ELASTIC_INDEX=products-v3`, and `SEARCH_V2_INDEX_NAME=""` (unset, to disable the override in §1) —
   this is the first time in this entire migration engagement that V1 has been tested against its own
   real index rather than V2's.
5. Ran the same requests (main search, category browsing by leaf id, catalogue, suggestions, simple
   search, products search, best-selling, curated) against this true-V1 server, captured full JSON.
6. Compared field shapes against the earlier V2 captures, and traced two specific behavioral claims
   (category browsing, brand filtering) down to exact query clauses using direct Elasticsearch calls
   against `products-v3`.
7. Restored the environment: `docker stop elastic-local`, `docker start opensearch-2-15`, confirmed
   OpenSearch 2.15 back up and the V2 index intact at 6,510 documents (verified by count).

**Result of the true validation:**

- **Field-shape parity conclusions from the previous audit are all confirmed unaffected.** V1's
  response JSON structure (meta fields, product-card keys, diagnostic flags, pagination shape) from
  the true-V1 server is byte-identical in *shape* to what was captured against the wrong index in the
  previous pass. This makes sense: `transform_to_product_card()`'s code doesn't change based on what
  data comes back. The 7 fixes made in the previous turn remain valid and necessary.
- **Two behavioral conclusions needed real re-investigation — done in §3 below, with corrected root
  causes.** The *conclusions themselves* (category browsing broken, brand filtering broken) turned out
  to still be **true** — but for different, more precise reasons than originally stated, now backed by
  direct evidence against V1's real engine and real index rather than inferred from a mismatched test.

---

## 3. Corrected root-cause findings (supersedes the previous audit's explanations)

### 3a. Category browsing bug — real root cause corrected

**Previous (incorrect) explanation across this migration:** "`category_paths.keyword` doesn't
exist." **This is wrong** — verified directly against `products-v3`: `category_paths.keyword` exists
and a direct `term` query against it returns 353 real hits for
`f_and_b/food/light_bites/chips_and_crisps`.

**Actual root cause, traced to the exact code and confirmed with direct queries against the real V1
index:** `search_products_unified()`'s subcategory filter (used by `/rs/v1/search`'s V1 fallback
path) builds this clause:

```python
filter_clauses.append({
    "bool": {
        "should": [
            {"term": {"category_hierarchies": leaf_subcat}},
            {"term": {"category_hierarchies.keyword": leaf_subcat}},
            {"wildcard": {"category_paths": {"value": f"*/{leaf_subcat}"}}},
        ],
        "minimum_should_match": 1
    }
})
```

`category_hierarchies`'s real mapping is `{"properties": {"segments": {"type": "text", "fields":
{"keyword": ...}}}}` — an object whose actual searchable value lives at
`category_hierarchies.segments` / `category_hierarchies.segments.keyword`, **not** at bare
`category_hierarchies`. Verified directly:

```
term on category_hierarchies (as V1 codes it):          0 hits
term on category_hierarchies.segments.keyword (correct): 318 hits
wildcard on category_paths "*/savory_namkeen":            0 hits (analyzed field, tokenized on "/")
```

All three should-clauses V1 actually builds return zero, confirmed by combining them exactly as the
real code does — **0 hits, reproduced against the real V1 engine and real V1 index.** This is a
genuine, pre-existing bug in V1's own code: a missing `.segments` path segment, unrelated to which
index it's pointed at.

### 3b. Brand filter bug — real root cause corrected

**Previous (incomplete) explanation:** attributed to "brittle tokenization" from a code comment,
without pinpointing the exact defect.

**Actual root cause, found reading the surrounding code precisely:**

```python
# 2) Domain-specific filters (skin/personal care)
if str(p.get("category_group") or "").strip() == "personal_care":
    ...
    # Brand filter
    if isinstance(p.get("brands"), list) and p.get("brands"):
        filters.append({"terms": {"brand": p["brands"]}})
```

**The brand filter is nested inside a `category_group == "personal_care"` check.** For any
food-and-beverage query (the overwhelming majority of the catalog, including the "milk"/"amul" test
case), this block never executes — `brands` is silently ignored, not "brittle," **completely
inert**. Verified twice: (1) a raw `terms` query on bare `brand` against `products-v3` actually
returns exactly-correct results (217/217 Amul) when issued directly, proving the field itself isn't
the problem; (2) the real application endpoint, hitting the real V1 index, still returns
Provilac/PROATHLIX/Cadbury for `brands=amul&query=milk` — proving the category-group gate is the
actual defect, confirmed against the real engine.

**Both conclusions (category browsing and brand filtering are broken in V1) are correct and now
confirmed against V1's real index and real code — the previous audit's underlying conclusions were
right, but this pass corrects *why*, with direct evidence rather than an inference drawn from a
mismatched-index test.**

---

## 4. What did not change

Every field-level fix from the previous audit (in_stock, currency, macro_tags, nutrition, scheduled,
category_group on suggestions, diagnostic meta flag consistency) remains correct and unaffected by
this discovery — confirmed by capturing the true-V1 server's responses and checking field shapes
match exactly. No further compatibility fix was required as a result of this deeper investigation;
the existing 7 fixes already cover everything found.

No code was changed in this pass beyond what was already in place — this was a validation-only
exercise (per your framing: "perform one final compatibility audit," not "make more changes"),
except for the transparent correction of the root-cause explanations recorded here and in
`V1_V2_FINAL_PARITY_AUDIT.md` (§2 of that document should be read alongside this correction).

## 5. Files changed in this pass

**None.** This was a read-only validation exercise against a temporarily-repurposed Docker container.
The only actions taken: stopping/starting two existing containers (reversed at the end), and running
requests against three server processes (all stopped at the end). No source file was modified.

## 6. Regression confirmation

`shopping_bot/tests/` + `search_v2/tests/`: 192 passed, 1 skipped — identical to every prior run this
session, confirming this validation pass introduced no code changes and no regressions.
