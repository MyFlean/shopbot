# Unified search API: `GET` / `POST` `/rs/v1/search`

This document describes the **unified search** endpoint implemented in [`shopping_bot/routes/unified_search.py`](../shopping_bot/routes/unified_search.py). It is registered with the Flask app under the `/rs` prefix, so the public path is:

```text
https://<host>/rs/v1/search
```

---

## Purpose

`/rs/v1/search` is a **single entry point** that covers the behaviour clients previously split across three routes:

| Legacy route | Role |
|--------------|------|
| `POST /rs/search` | Text search, filters, flat response |
| `GET /rs/api/v1/catalogue` | Browse by subcategory, pagination, catalogue sort naming |
| `GET` / `POST /rs/api/v1/products` | Unified search/browse with filters and wrapped response |

The new route:

- Accepts parameters compatible with **all three** (including alias names where historical clients differ).
- Returns exactly the **same JSON envelope** as `/rs/api/v1/products`: `{ "success", "data": { "products" }, "meta" }`.
- Uses the same backend query path as `/rs/api/v1/products`: `ElasticsearchProductsFetcher.search_products_unified(...)`.
- Does **not** remove or change the legacy routes; they remain available.

---

## HTTP methods

| Method | Body | Typical use |
|--------|------|-------------|
| `GET` | — | Query string only: shareable URLs, simple clients |
| `POST` | JSON | Rich `filters` object, mobile apps mirroring `/api/v1/products` |

Both methods enforce the same validation and return the same response shape.

---

## Required input

At least **one** of the following must be present (after trimming empty strings):

- `query` — free-text search
- `subcategory` — Elasticsearch category path (e.g. `f_and_b/food/light_bites/chips_and_crisps`)
- `filters` — at least one supported filter field (see below)

If none are provided, the API responds with **400** and code `MISSING_PARAMETER`.

---

## Parameters

### Common (GET query or POST JSON)

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `query` | string | omit / null | Full-text search across product fields (multi-match, fuzziness). Optional. |
| `subcategory` | string | omit / null | Filters to products whose `category_paths` match this path (term + wildcard). Optional. |
| `page` | integer | `0` | Zero-based page index. Invalid values fall back to `0`. |
| `size` | integer | `20` | Page size, clamped to **1–100**. Invalid values fall back to `20`. |
| `sort_by` | string | `relevance` | Primary sort parameter (canonical name). |
| `sort` | string | — | **Alias** for `sort_by`. If both are sent, behaviour depends on method (see below). |

**GET:** `sort_by` is read first; if absent, `sort` is used (`request.args.get("sort_by") or request.args.get("sort")`).

**POST:** If `sort_by` is present in the JSON body (including explicitly `null`), it wins; otherwise `sort` is used.

### Sort values and aliases

Canonical values (must match server set after alias resolution):

- `relevance` — default; Elasticsearch `_score` descending (when no other sort applies).
- `price_asc`, `price_desc`
- `protein_desc`, `fiber_desc`, `fat_asc`
- `flean_score_desc` — sort by `stats.adjusted_score_percentiles.subcategory_percentile` descending, then `_score`; aligns with **catalogue’s** default “flean” ordering when migrating from `GET /rs/api/v1/catalogue?sort=flean_score`.

**Catalogue compatibility aliases** (input only; response `meta.sort_by` reflects the **resolved** canonical value):

| Client sends (`sort` or `sort_by`) | Resolved to |
|-------------------------------------|---------------|
| `flean_score` | `flean_score_desc` |
| `price` | `price_asc` |

Omitted or empty sort → `relevance`.

Invalid sort → **400**, code `INVALID_SORT`.

---

## Filters

Filters are validated by the same logic as `/rs/api/v1/products` ([`_validate_filters`](../shopping_bot/routes/product_api.py)).

### POST: `filters` object

Nested object under key `filters`. Each field is optional; an empty object `{}` does **not** satisfy the “at least one of query / subcategory / filters” rule unless combined with `query` or `subcategory`.

Supported keys (after **alias normalization** on the server):

| Key in request | Normalized / validated as | Notes |
|----------------|---------------------------|--------|
| `price_range` | `price_range` | One of: `below_99`, `100_249`, `250_499`, `above_500` |
| `flean_score` | `flean_score` | One of: `10`, `9_plus`, `8_plus`, `7_plus` |
| `ingredient_preferences` **or** `preferences` | lists merged into validation as `ingredient_preferences` | Values from allowed set: `no_palm_oil`, `no_added_sugar`, `no_harmful_additives`, `preservative_free` |
| `dietary_preferences` **or** `dietary` | `dietary_preferences` | Values: `dairy_free`, `gluten_free`, `nut_free`, `pcos_friendly` |
| `food_type` | `food_type` | `veg` or `nonveg` |
| `nutrition` | `nutrition` | Object with optional `protein`, `carbs`, `fat` integers; allowed values are **stepped** (see below) |

If both `preferences` and `ingredient_preferences` are sent, **`ingredient_preferences` wins** and `preferences` is dropped.

If both `dietary` and `dietary_preferences` are sent, **`dietary_preferences` wins** and `dietary` is dropped.

### POST: top-level `food_type`

Matches **`POST /rs/search`**: you may send `food_type` at the **root** of the JSON body. If `filters.food_type` is not set, the server copies root-level `food_type` into `filters` before validation.

### GET: flat query parameters

For `GET`, filters are expressed as flat query keys (no nested `filters` object):

| Query param | Maps to filter |
|-------------|----------------|
| `price_range` | `price_range` |
| `flean_score` | `flean_score` |
| `preferences` **or** `ingredient_preferences` | comma-separated list → preference array |
| `dietary` **or** `dietary_preferences` | comma-separated list → dietary array |
| `food_type` | `food_type` |
| `protein`, `carbs`, `fat` | assembled into `nutrition.{protein|carbs|fat}` when any present |

Example:

```http
GET /rs/v1/search?query=bar&price_range=100_249&preferences=no_palm_oil,no_added_sugar&protein=20
```

### Nutrition sliders (allowed values)

Validation uses fixed steps (same as `/rs/api/v1/products`):

- **Protein:** `0` through `40`, step **10** (threshold “more than X g”).
- **Carbs** and **Fat:** `0` through `100`, step **25** (“less than X g” where applicable).

Values outside these sets return **400**, code `INVALID_FILTERS`, with a message naming the field.

**Note:** `POST /rs/search` historically used a **20** step for carbs/fat in its own validator. Clients migrating from `/rs/search` to `/rs/v1/search` must send nutrition values that match the **unified** rules above, or they will receive validation errors.

---

## Success response (200)

Shape matches `/rs/api/v1/products`:

```json
{
  "success": true,
  "data": {
    "products": [ /* standardized product cards */ ]
  },
  "meta": {
    "total": 0,
    "page": 0,
    "size": 20,
    "total_pages": 0,
    "has_next": false,
    "has_prev": false,
    "query": null,
    "subcategory": null,
    "sort_by": "relevance",
    "filters_applied": {}
  }
}
```

- `data.products` — each item is produced by `transform_to_product_card(...)` (same as other product list APIs).
- **`meta.sort_by`** — always the **resolved canonical** sort (e.g. after mapping `flean_score` → `flean_score_desc`). The server overwrites any `sort_by` coming back from OpenSearch with this value for consistency.
- Other `meta` fields are populated by `search_products_unified` (totals, pagination flags, echo of query/subcategory, applied filters, etc.), depending on the search backend.

---

## Error responses

Errors use the same wrapper as other Shopbot product APIs: `success: false` and an `error` object with `code` and `message`.

| HTTP | Code | When |
|------|------|------|
| 400 | `MISSING_PARAMETER` | None of `query`, `subcategory`, or `filters` provided |
| 400 | `INVALID_QUERY` | `POST`: `query` present but not a string |
| 400 | `INVALID_SUBCATEGORY` | `POST`: `subcategory` present but not a string |
| 400 | `INVALID_SORT` | Sort not in allowed set after alias resolution |
| 400 | `INVALID_FILTERS` | Filter validation failed, or `POST` with non-object `filters` |
| 500 | `SEARCH_ERROR` | OpenSearch returned an error in result `meta` |
| 500 | `INTERNAL_ERROR` | Unexpected exception (including misconfiguration of the search client) |

---

## Backend behaviour

1. Parse and normalize parameters (GET vs POST, sort aliases, filter key aliases, optional top-level `food_type` on POST).
2. Validate sort and filters.
3. Call `get_es_fetcher().search_products_unified(query, subcategory, page, size, sort_by=resolved, filters=validated)`.
4. Map raw hits through `transform_to_product_card`.
5. Return `_success_response({"products": cards}, meta)` with `meta["sort_by"]` set to the resolved sort.

OpenSearch / Elasticsearch configuration (URL, index, API key vs IAM for AOSS) is shared with the rest of the service via `ElasticsearchProductsFetcher` and environment variables; it is not specific to this route.

---

## Examples

### Text search + sort (GET)

```bash
curl -sS "https://api.flean.ai/rs/v1/search?query=protein+bars&sort_by=price_asc&page=0&size=20"
```

### Catalogue-style browse (GET), using catalogue sort names

```bash
curl -sS "https://api.flean.ai/rs/v1/search?subcategory=f_and_b/food/light_bites/chips_and_crisps&sort=flean_score&page=0&size=20"
```

Resolved `meta.sort_by` will be `flean_score_desc`.

### Simple-search style filters (POST), preferences alias

```bash
curl -sS -X POST "https://api.flean.ai/rs/v1/search" \
  -H "Content-Type: application/json" \
  -d '{"query":"chips","filters":{"preferences":["no_palm_oil"]}}'
```

### Products-style body (POST)

```bash
curl -sS -X POST "https://api.flean.ai/rs/v1/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "milk",
    "page": 0,
    "size": 20,
    "sort_by": "relevance",
    "filters": {
      "price_range": "below_99",
      "dietary_preferences": ["gluten_free"],
      "nutrition": {"protein": 20}
    }
  }'
```

---

## Related source files

| File | Role |
|------|------|
| [`shopping_bot/routes/unified_search.py`](../shopping_bot/routes/unified_search.py) | Route, sort/filter alias handling |
| [`shopping_bot/routes/product_api.py`](../shopping_bot/routes/product_api.py) | `VALID_SORT_OPTIONS`, `_validate_filters`, `_success_response` |
| [`shopping_bot/data_fetchers/es_products.py`](../shopping_bot/data_fetchers/es_products.py) | `search_products_unified`, `_build_sort_config` (including `flean_score_desc`) |
| [`shopping_bot/__init__.py`](../shopping_bot/__init__.py) | Blueprint registration: `unified_search` with `url_prefix='/rs'` |

Production search backend (AOSS, `ES_URL`, IAM): [`docs/opensearch-serverless.md`](opensearch-serverless.md).
