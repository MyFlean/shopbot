# Automated Postman Regression

Date: 2026-07-23. `postman_regression_runner.py` (repo root) executes every request in
`Flean_HomePage_Search_APIs.postman_collection.json` automatically and reports pass/fail, response
code, latency, and — for a pair of runs — a full response diff including a V1-vs-V2 comparison.

## Why two runs, not one, for V1 vs V2

`SEARCH_ENGINE` is read from the **server process's** environment, not anything a client can send
per-request. A genuine V1-vs-V2 comparison therefore means starting the server twice (once per
`SEARCH_ENGINE` value) and diffing two saved result files — there is no way for a single script
invocation against one already-running server to do this without adding a request-level engine
override to the production app, which was deliberately not done (it would be new, untested
production-facing behavior, out of scope for a regression tool).

## Usage

```bash
# One run against whatever's currently serving (pass/fail, status, latency for every request):
python postman_regression_runner.py \
    --collection Flean_HomePage_Search_APIs.postman_collection.json \
    --base-url http://localhost:8080 --out results_auto.json

# V1-vs-V2 comparison: start the server once per engine, run once each, then diff:
SEARCH_ENGINE=v1 python run.py &   # in one terminal
python postman_regression_runner.py --collection ... --base-url http://localhost:8080 --label v1 --out results_v1.json

SEARCH_ENGINE=v2 python run.py &   # restart with v2
python postman_regression_runner.py --collection ... --base-url http://localhost:8080 --label v2 --out results_v2.json

python postman_regression_runner.py --diff results_v1.json results_v2.json
# → prints + writes postman_regression_diff.json
```

## Missing endpoints found and added

The collection covered 18 requests (Search API ×5, Home Page APIs ×10, Product APIs ×3 including
pagination duplicates) against a real registered-route count of ~46 across the app. Cross-referenced
every `@bp.route(...)` in `shopping_bot/routes/*.py` against the collection; within the
collection's own stated scope (Search / Home Page / Product APIs — its name and description say so
explicitly), **21 registered routes were missing**. Added as a new "4. Search V2 Migration —
Previously Missing Endpoints" folder (collection is now 40 requests total):

`/rs/v1/search` (query, subcategory, filters-only variants), `/rs/v1/search/suggest`,
`/rs/v2/search/suggest`, `/rs/api/v1/home/supplements`, `/rs/api/v1/home/validation-candidates`,
`/rs/api/v1/home/flean-picks` (`source=see_all`/`home`), `/rs/api/v1/home/flean-picks/<key>`,
`/rs/api/v1/home/unified`, `/rs/api/v1/flean-score`, `/rs/api/v1/product/<id>/alternatives`,
`/rs/api/v1/product/<id>/recommended`, `/rs/api/v1/products/pdp/batch`,
`/rs/api/v1/catalogue/mapping`, `/rs/api/v1/products` (query + filters-only),
`/rs/api/v1/products/search`, `/rs/api/v1/products/health`, `/rs/health`.

**Deliberately not added** (out of this collection's stated scope — a different feature surface,
not "Search/Home/Product APIs"): `/rs/chat*`, `/rs/flow/*`, `/rs/reset`, `/rs/chat/ui`,
`/rs/api/v1/admin/cards-config/reload`, `/rs/api/v1/home/reload`, `/rs/__routes`,
`/rs/redis-health`. If you want these covered too, say so and they can be added the same way.

## Results — `auto` mode (production's actual `SEARCH_ENGINE` setting), local stack

**39/40 passed.** The one failure (`Product APIs / Scanner (Image Lookup)`) is **not a regression**:
the collection's own placeholder request body contains truncated, invalid base64
(`"...AAD..."` — deliberately shortened, not a real image), which correctly fails base64 decoding
(`INVALID_IMAGE: Incorrect padding`) regardless of engine or migration state. Confirmed by running
the same request with real image bytes during earlier phases of this migration (Scanner was
verified working there). Not fixed here since fabricating fake image bytes wouldn't test anything
real — flagging it as a pre-existing collection data-quality note instead.

## Results — V1 vs V2 comparison, local stack

Ran the full 40-request collection once with `SEARCH_ENGINE=v1` and once with `SEARCH_ENGINE=v2`,
diffed the two result sets (`postman_regression_runner.py --diff`):

- **19/40 requests are byte-for-byte identical in behavior between engines**: everything
  id-keyed/deterministic (PDP, Batch PDP, Flean Score, Catalogue Mapping, Flean Picks by collection
  key, Supplements, Validation Candidates) plus everything with no search dependency at all
  (Banners, Categories, Why Flean, Collaborations, Health Check, Refresh Cache) plus both
  Suggestions endpoints.
- **21/40 requests show real, expected differences** — every one of them is a free-text-query or
  broad-filter-driven request (Basic Search, Search with Filters/Sorting/Dietary Filters/All
  Options, Best Sellers, Curated, Catalogue by bare `subcategory=chips`, Alternatives, Recommended,
  Unified Search/Products in all their query/filter-only forms). This is **expected and correct**,
  not a bug: V1 and V2 are genuinely different retrieval algorithms (V1's legacy ES query vs V2's
  hybrid lexical+semantic pipeline with business ranking) — different-but-reasonable result sets and
  rankings for the same free-text query is exactly what every prior phase of this migration already
  established and validated (see `FINAL_MIGRATION_REPORT.md` §3's product-parity table,
  `MIGRATION_STATUS.md`'s category-browsing validation). No pass/fail or status-code differences
  were found on any of the 21 — only product-set/ranking differences, i.e. both engines return
  valid 200s with different (both defensible) product orderings.
- Latency is reported separately (`latency_comparison` in the diff output) and excluded from the
  "has a difference" determination, since wall-clock timing always varies run-to-run regardless of
  behavior — it would otherwise trivially flag all 40 requests as "different."

## Final pre-production results — `auto` vs strict `SEARCH_ENGINE=v2`

After the final pass removed every remaining exception-based auto-fallback-to-V1 (see
`V1_FALLBACK_AUDIT.md`'s update and `ENDPOINT_PARITY_REPORT.md`), the collection was run again —
once under `auto` (production's real setting) and once under strict `SEARCH_ENGINE=v2` — and
diffed:

- **40/40 requests are byte-for-byte identical between `auto` and `v2`.** Zero differences of any
  kind — no status-code, pass/fail, product-set, ranking, or meta differences on any request.
- This is the strongest available evidence that Search V1 does not execute anywhere in the default
  (`auto`) configuration anymore: if any auto-fallback-to-V1 path still existed and could fire, this
  diff would have caught it, since `v2` mode makes V1 completely unreachable while `auto` doesn't.
  Identical results across all 40 requests means `auto`'s behavior is now, in practice, `v2`'s
  behavior — `SEARCH_ENGINE=v1` is the only way to reach V1 anywhere in the application.
- 39/40 passed on each individual run (the same pre-existing Scanner placeholder-image issue,
  unrelated to engine or migration state).

## Files

- `postman_regression_runner.py` — the runner/differ (repo root)
- `Flean_HomePage_Search_APIs.postman_collection.json` — the collection, now with the added folder
- Result/diff JSON files are written wherever `--out`/the diff's default filename point — not
  checked into the repo (regenerate on demand; this document has the summarized findings)
