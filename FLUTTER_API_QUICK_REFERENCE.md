# Flean Flutter App - API Quick Reference

> **Base URL (Production):** `http://flean-services-alb-806741654.ap-south-1.elb.amazonaws.com`
> **All endpoints prefix:** `/rs`

---

## Quick Reference Table

| Screen / Feature | Method | Endpoint | Purpose |
|-----------------|--------|----------|---------|
| Home - Banners | `GET` | `/rs/api/v1/home/banners` | Promotional carousel |
| Home - Categories | `GET` | `/rs/api/v1/home/categories` | Category grid (4 items) |
| Home - Categories All | `GET` | `/rs/api/v1/home/categories?all=true` | All categories |
| Home - Best Selling | `GET` | `/rs/api/v1/home/best-selling` | Category-path based top picks (6) |
| Home - Curated | `GET`/`POST` | `/rs/api/v1/home/curated` | 4 random curated products |
| Home - Curated All | `GET`/`POST` | `/rs/api/v1/home/curated/all` | All curated products |
| Home - Why Flean | `GET` | `/rs/api/v1/home/why-flean` | Value proposition cards |
| Home - Collaborations | `GET` | `/rs/api/v1/home/collaborations` | Partner brands |
| Refresh Cache | `POST` | `/rs/api/v1/home/refresh` | Clear cached data |
| **Unified Products** | `GET`/`POST` | `/rs/api/v1/products` | **Search + Catalogue + Pagination + Filters** |
| **Product Search** | `GET`/`POST` | `/rs/api/v1/products/search` | Search + Filters + Pagination |
| Search (legacy) | `POST` | `/rs/search` | Search + Sort + Filter (no pagination) |
| Catalogue (legacy) | `GET` | `/rs/api/v1/catalogue?subcategory=X` | Products by category |
| Catalogue Mapping | `GET` | `/rs/api/v1/catalogue/mapping` | Category-to-ES-path map |
| **PDP** | `GET` | `/rs/api/v1/product/{id}` | Pre-parsed product detail |
| **PDP Batch** | `POST` | `/rs/api/v1/products/pdp/batch` | Multiple PDPs in one request |
| **Alternatives** | `GET` | `/rs/api/v1/product/{id}/alternatives` | 5 healthier alternatives |
| **Recommended** | `GET` | `/rs/api/v1/product/{id}/recommended` | 8 recommended products for PDP |
| **Scanner** | `POST` | `/rs/api/v1/scanner` | Top 3 product cards from image |

---

## STANDARDIZED PRODUCT CARD

All listing APIs (Search, Catalogue, Scanner, Best Selling, Curated, Alternatives, Recommended) return the **same product card shape**:

```json
{
  "id": "01K1B1BPGN2WAXFB5DNSGXX4W3",
  "name": "Yoga Bar 20g Protein Bar",
  "brand": "Yoga Bar",
  "price": 100.0,
  "mrp": 120.0,
  "currency": "INR",
  "qty": "60 g",
  "image_url": "https://cdn.flean.ai/...",
  "macro_tags": [
    {"label": "20 gms of Protein", "nutrient": "protein", "value": 20, "unit": "g"},
    {"label": "225 Calories", "nutrient": "calories", "value": 225, "unit": "kcal"}
  ],
  "nutrition": {
    "protein_g": 20.0, "carbs_g": 28.0, "fat_g": 8.0, "fiber_g": 3.0, "calories": 225.0
  },
  "flean_score": 2.5,
  "flean_percentile": 85.2,
  "in_stock": true
}
```

---

## HOME SCREEN

```
+----------------------------------------------------------+
|  [1] BANNERS CAROUSEL                                     |
|----------------------------------------------------------|
|  [2] CATEGORIES          [See All]                        |
|  [Smart] [Dairy] [Power] [Sweet]                          |
|----------------------------------------------------------|
|  [3] BEST SELLING                                         |
|  [Card] [Card] [Card] [Card]                              |
|----------------------------------------------------------|
|  [4] CURATED FOR YOU     [See All]                        |
|  [Card] [Card] [Card] [Card]                              |
|----------------------------------------------------------|
|  [5] WHY FLEAN                                            |
|  [Card1] [Card2] [Card3] [Card4]                          |
|----------------------------------------------------------|
|  [6] EXCLUSIVE COLLABORATIONS                             |
|  [Brand1] [Brand2] [Brand3] [Brand4] [Brand5]            |
+----------------------------------------------------------+
```

### [1] Banners

```
GET /rs/api/v1/home/banners
```

### [2] Categories

```
GET /rs/api/v1/home/categories           # 4 items
GET /rs/api/v1/home/categories?all=true  # all items
```

### [3] Best Selling

```
GET /rs/api/v1/home/best-selling   # top 2 from each fixed category, total up to 6
```

### [4] Curated (+ "Customize Your Feed")

```
GET  /rs/api/v1/home/curated      # 4 random product cards
POST /rs/api/v1/home/curated      # same (accepts preferences body, see below)
GET  /rs/api/v1/home/curated/all  # all curated product cards
POST /rs/api/v1/home/curated/all  # same (accepts preferences body)
```

The "Customize Your Feed" bottom sheet sends preferences via POST. The backend **accepts** the body but returns **random curated products regardless** -- preferences are stored for future use but do not filter results.

**"Save & Refresh Feed" flow:**
```
POST /rs/api/v1/home/curated
Content-Type: application/json

{
  "ingredient_preferences": ["no_palm_oil", "no_added_sugar"],
  "daily_macros": {"protein": 15, "carbs": 0, "fat": 0},
  "dietary_restrictions": ["dairy_free", "nut_free", "low_fat"]
}
```

Response is the same as GET -- 4 random curated product cards:
```json
{
  "success": true,
  "data": {
    "products": [ /* 4 product cards */ ],
    "section_title": "Curated For You",
    "has_more": true,
    "total_in_pool": 25
  }
}
```

### [5] Why Flean

```
GET /rs/api/v1/home/why-flean
```

### [6] Collaborations

```
GET /rs/api/v1/home/collaborations
```

---

## SEARCH & CATALOGUE (UNIFIED PRODUCTS API)

Use `/rs/api/v1/products` for **both** Search Screen and Catalogue Screen.
Supports **GET** (query params) and **POST** (JSON body).

### GET Request (recommended for pagination)

```
GET /rs/api/v1/products?query=protein+bars&page=0&size=20&sort_by=relevance
GET /rs/api/v1/products?subcategory=f_and_b/food/light_bites&page=0&size=20
GET /rs/api/v1/products?query=chips&price_range=below_99&flean_score=8_plus
GET /rs/api/v1/products?query=snacks&preferences=no_palm_oil,no_added_sugar&dietary=gluten_free
```

| Query Parameter | Type | Description |
|-----------------|------|-------------|
| `query` | string | Text search (optional) |
| `subcategory` | string | ES category path (optional) |
| `page` | int | 0-indexed page number (default 0) |
| `size` | int | Items per page, 1-100 (default 20) |
| `sort_by` | string | Sort option (default "relevance") |
| `price_range` | string | Price filter |
| `flean_score` | string | Flean score filter |
| `preferences` | string | Comma-separated preference filters |
| `dietary` | string | Comma-separated dietary filters |

### POST Request

```
POST /rs/api/v1/products
Content-Type: application/json
```

```json
{
  "query": "protein bars",              // optional - text search
  "subcategory": "f_and_b/food/...",    // optional - ES category path
  "page": 0,                            // optional - 0-indexed (default 0)
  "size": 20,                           // optional - 1-100 (default 20)
  "sort_by": "relevance",               // optional - see sort options
  "filters": {                          // optional - all filter types
    "price_range": "below_99",
    "flean_score": "9_plus",
    "preferences": ["no_palm_oil"],
    "dietary": ["gluten_free"]
  }
}
```

### Behavior Matrix

| Request Type | query | subcategory | Use Case |
|--------------|-------|-------------|----------|
| Search | yes | no | Full-text search with filters |
| Browse Category | no | yes | List subcategory products with filters |
| Search in Category | yes | yes | Search within specific subcategory |
| Invalid | no | no | Returns 400 error |

### Sort Options

| Value | Description |
|-------|-------------|
| `relevance` | Default - ES relevance score |
| `price_asc` | Price: Low to High |
| `price_desc` | Price: High to Low |
| `protein_desc` | Protein: High to Low |
| `fiber_desc` | Fiber: High to Low |
| `fat_asc` | Fat: Low to High |

### Filter Options

| Category | Values | Type |
|----------|--------|------|
| **price_range** | `below_99`, `100_249`, `250_499`, `above_500` | Single |
| **flean_score** | `10`, `9_plus`, `8_plus`, `7_plus` | Single |
| **preferences** | `no_palm_oil`, `no_added_sugar`, `no_additives` | Array |
| **dietary** | `dairy_free`, `gluten_free` | Array |

### Response

```json
{
  "success": true,
  "data": {
    "products": [ /* array of product cards */ ]
  },
  "meta": {
    "total": 245,
    "page": 0,
    "size": 20,
    "total_pages": 13,
    "has_next": true,
    "has_prev": false,
    "query": "protein bars",
    "subcategory": null,
    "sort_by": "relevance",
    "filters_applied": { "price_range": "100_249" }
  }
}
```

### Standardized Pagination Meta

All paginated endpoints (Unified Products, Catalogue, Product Search) return the **same meta shape**:

| Field | Type | Description |
|-------|------|-------------|
| `total` | int | Total matching products |
| `page` | int | Current page (0-indexed) |
| `size` | int | Items per page |
| `total_pages` | int | Ceiling of total/size |
| `has_next` | bool | More pages available? |
| `has_prev` | bool | Previous pages exist? |

Use `has_next` to drive infinite scroll / "Load More" in Flutter.

---

## CATALOGUE MAPPING

To know which `subcategory` value to pass to the Unified Products API:

```
GET /rs/api/v1/catalogue/mapping
```

Returns **display names to ES paths** mapping:

```json
{
  "success": true,
  "data": {
    "categories": [
      {
        "display_name": "Smart Snacks",
        "es_path": "f_and_b/food/light_bites",
        "subcategories": [
          { "display_name": "Chips & Crisps", "es_path": "f_and_b/food/light_bites/chips_and_crisps" },
          { "display_name": "Energy Bars", "es_path": "f_and_b/food/light_bites/energy_bars" }
        ]
      }
    ],
    "popular_searches": ["High Protein Snacks", "No Added Sugar Peanut Butter", "High Protein Yogurt"],
    "search_by_category": [
      { "display_name": "Dairy & Bakery", "es_path": "f_and_b/food/dairy_and_bakery" },
      { "display_name": "Smart Snacks", "es_path": "f_and_b/food/light_bites" }
    ]
  }
}
```

---

## PDP (Product Detail Page)

```
GET /rs/api/v1/product/{product_id}
```

Returns optimized key-value data for direct Flutter widget mapping. No complex parsing required.

### Response Structure

```json
{
  "success": true,
  "data": {
    "product_info": {
      "id": "01K1B...",
      "name": "Provilac High Protein Milk",
      "brand": "Provilac",
      "price": 139,
      "mrp": 160,
      "currency": "INR",
      "image_url": "https://cdn.flean.ai/640/...",
      "image_urls": { "640": "https://...", "1080": "https://..." },
      "qty": "500 ml",
      "description": "..."
    },
    "flean_badge": {
      "score": 10,
      "score_display": "10/10",
      "level": "safe",
      "level_text": "100% Safe"
    },
    "score_cards": {
      "flean_rank": { "title": "Flean Rank", "value": "Top 6.2%", "subtitle": "Tea-time snack", "percentile": 93.8, "status": "good" },
      "protein": { "title": "Protein", "value": "Top 1.7%", "subtitle": "Efficiency", "percentile": 98.3, "status": "good" },
      "fiber": { "title": "Fiber", "value": "Top 12.0%", "subtitle": "Efficiency", "percentile": 88.0, "status": "good" },
      "sweeteners": { "title": "Sweeteners", "value": "Low", "subtitle": "Percentile: 82", "percentile": 82.0, "status": "good" },
      "oils": { "title": "Oils", "value": "Medium", "subtitle": "Percentile: 55", "percentile": 55.0, "status": "caution" },
      "watch_outs": { "title": "Watch-outs", "value": "Ultra-Processed", "subtitle": "Caution", "percentile": 35.0, "status": "warning", "visible": true },
      "calories": { "title": "Calories", "value": "80 kcal", "subtitle": "100 Gram", "percentile": 60.0, "status": "neutral" }
    },
    "notes": {
      "criteria_note": "Per 100 g labels reflect Flean Criteria.",
      "ranking_note": "Note: Overall ranking considers multiple factors."
    },
    "highlights": [
      { "label": "Brand", "value": "Provilac" },
      { "label": "Product Name", "value": "Provilac High Protein Milk" },
      { "label": "Weight / Volume", "value": "500 ml" },
      { "label": "Dietary Preference", "value": "Vegetarian" }
    ],
    "ingredients": ["Toned Milk", "Milk Solids", "Whey Protein Concentrate"],
    "nutrition": {
      "basis": "per 100 ml",
      "items": [
        { "nutrient": "Energy", "value": "72 kcal" },
        { "nutrient": "Protein", "value": "8 g" },
        { "nutrient": "Carbohydrates", "value": "5 g" }
      ]
    },
    "additional_info": [
      { "label": "Seller Name", "value": "FreshKart Retail Pvt. Ltd." },
      { "label": "Country of Origin", "value": "India" }
    ],
    "macro_tags": [
      {"label": "8 gms of Protein", "nutrient": "protein", "value": 8, "unit": "g"},
      {"label": "72 Calories", "nutrient": "calories", "value": 72, "unit": "kcal"}
    ]
  }
}
```

### Score Cards Status Color Mapping

| `status` | Color | Condition |
|----------|-------|-----------|
| `good` | Green | percentile >= 70 |
| `caution` | Yellow | percentile 40-69 |
| `warning` | Red | percentile < 40 |
| `neutral` | Gray | Informational only |

### Flean Badge Levels

| `level` | `level_text` | Condition |
|---------|--------------|-----------|
| `safe` | 100% Safe | percentile >= 70 |
| `caution` | Use with Caution | percentile 40-69 |
| `warning` | Not Recommended | percentile < 40 |
| `unknown` | Not Rated | no percentile data |

---

## PDP BATCH (Multiple Products)

```
POST /rs/api/v1/products/pdp/batch
Content-Type: application/json

{ "ids": ["id1", "id2", "id3"] }
```

Returns PDP data for all requested IDs. Max 50 IDs per request. Same structure as single PDP per product.

```json
{
  "success": true,
  "data": {
    "products": [ { /* full PDP 1 */ }, { /* full PDP 2 */ } ],
    "not_found": ["id3"]
  },
  "meta": { "requested": 3, "returned": 2, "not_found_count": 1 }
}
```

---

## SCANNER FLOW (3 Steps)

```
Step 1: POST /rs/api/v1/scanner                      -> Top 3 product cards
Step 2: GET  /rs/api/v1/product/{id}                  -> Full PDP of selected product
Step 3: GET  /rs/api/v1/product/{id}/alternatives     -> 5 healthier options
```

### Step 1: Scan Image

```
POST /rs/api/v1/scanner
Content-Type: application/json

{ "image": "data:image/jpeg;base64,/9j/4AAQSkZ..." }
```

**Response:**
```json
{
  "success": true,
  "data": {
    "extracted": {
      "product_name": "Yoga Bar Protein Bar Almond Fudge",
      "brand_name": "Yoga Bar",
      "ocr_text": "YOGA BAR\n20g PROTEIN\nAlmond Fudge",
      "category_group": "f_and_b"
    },
    "products": [ /* top 3 product cards */ ]
  }
}
```

### Step 2: Get PDP for Selected Product

```
GET /rs/api/v1/product/{selected_product_id}
```

### Step 3: Get Healthier Alternatives

```
GET /rs/api/v1/product/{selected_product_id}/alternatives
```

```json
{
  "success": true,
  "data": {
    "source_product": { /* product card of selected product */ },
    "alternatives": [ /* up to 5 product cards, healthiest first */ ]
  }
}
```

### Step 4 (Optional): Recommended Products for PDP

```
GET /rs/api/v1/product/{id}/recommended
GET /rs/api/v1/product/{id}/recommended?limit=6
```

```json
{
  "success": true,
  "data": {
    "products": [ /* up to 8 product cards, healthiest first */ ],
    "section_title": "You May Also Like",
    "source_product_id": "01K1B...",
    "subcategory": "f_and_b/food/light_bites/energy_bars"
  },
  "meta": { "total_in_subcategory": 45, "returned": 8 }
}
```

---

## ERROR HANDLING

```json
{ "success": false, "error": { "code": "ERROR_CODE", "message": "Human-readable description" } }
```

| Code | HTTP | Meaning |
|------|------|---------|
| `MISSING_IMAGE` | 400 | Scanner: no image provided |
| `INVALID_IMAGE` | 400 | Scanner: bad format/size |
| `MISSING_SUBCATEGORY` | 400 | Catalogue: no subcategory |
| `INVALID_ID` | 400 | PDP/Alternatives: empty product ID |
| `PRODUCT_NOT_FOUND` | 404 | PDP/Alternatives: invalid product ID |
| `VISION_ERROR` | 500 | Scanner: Claude Vision failed |
| `NOT_FOUND` | 404 | Mapping file not found |
| `INTERNAL_ERROR` | 500 | Server error |

---

## ALL CURL EXAMPLES

```bash
BASE="http://flean-services-alb-806741654.ap-south-1.elb.amazonaws.com"

# ─── HOME PAGE ────────────────────────────────────────────
curl "$BASE/rs/api/v1/home/banners"
curl "$BASE/rs/api/v1/home/categories"
curl "$BASE/rs/api/v1/home/categories?all=true"
curl "$BASE/rs/api/v1/home/best-selling"
curl "$BASE/rs/api/v1/home/curated"
curl "$BASE/rs/api/v1/home/curated/all"
curl "$BASE/rs/api/v1/home/why-flean"
curl "$BASE/rs/api/v1/home/collaborations"

# ─── CURATED WITH PREFERENCES (Save & Refresh Feed) ──────
curl -X POST "$BASE/rs/api/v1/home/curated" \
  -H "Content-Type: application/json" \
  -d '{"ingredient_preferences":["no_palm_oil","no_added_sugar"],"daily_macros":{"protein":15,"carbs":0,"fat":0},"dietary_restrictions":["dairy_free"]}'

# ─── UNIFIED PRODUCTS (Search + Catalogue) ────────────────
# GET (recommended for pagination)
curl "$BASE/rs/api/v1/products?query=protein+bars"
curl "$BASE/rs/api/v1/products?query=protein+bars&page=1&size=10"
curl "$BASE/rs/api/v1/products?subcategory=f_and_b/food/light_bites/chips_and_crisps&page=0&size=20"
curl "$BASE/rs/api/v1/products?query=chips&sort_by=price_asc&price_range=below_99&flean_score=8_plus"
curl "$BASE/rs/api/v1/products?query=snacks&preferences=no_palm_oil,no_added_sugar&dietary=gluten_free"

# POST (also supported)
curl -X POST "$BASE/rs/api/v1/products" -H "Content-Type: application/json" \
  -d '{"query": "protein bars"}'

curl -X POST "$BASE/rs/api/v1/products" -H "Content-Type: application/json" \
  -d '{"subcategory": "f_and_b/food/light_bites/chips_and_crisps", "page": 0, "size": 20}'

curl -X POST "$BASE/rs/api/v1/products" -H "Content-Type: application/json" \
  -d '{"query": "chips", "sort_by": "price_asc", "filters": {"price_range": "below_99", "flean_score": "8_plus"}}'

# ─── PRODUCT SEARCH (with pagination) ────────────────────
curl "$BASE/rs/api/v1/products/search?query=protein+bars&page=0&size=20"
curl "$BASE/rs/api/v1/products/search?query=chips&page=1&size=10&sort_by=price_asc"

# ─── SEARCH (Legacy - no pagination) ─────────────────────
curl -X POST "$BASE/rs/search" -H "Content-Type: application/json" \
  -d '{"query": "protein bars"}'

curl -X POST "$BASE/rs/search" -H "Content-Type: application/json" \
  -d '{"query": "chips", "sort_by": "price_asc", "filters": {"price_range": "below_99"}}'

# ─── PDP ──────────────────────────────────────────────────
curl "$BASE/rs/api/v1/product/01K1B1BPGN2WAXFB5DNSGXX4W3"

# ─── PDP BATCH ────────────────────────────────────────────
curl -X POST "$BASE/rs/api/v1/products/pdp/batch" \
  -H "Content-Type: application/json" \
  -d '{"ids":["01K1B1BPGN2WAXFB5DNSGXX4W3","01K1B1BPD07T3A1V4S6YGDD0EN"]}'

# ─── ALTERNATIVES ─────────────────────────────────────────
curl "$BASE/rs/api/v1/product/01K1B1BPGN2WAXFB5DNSGXX4W3/alternatives"

# ─── RECOMMENDED ──────────────────────────────────────────
curl "$BASE/rs/api/v1/product/01K1B1BPGN2WAXFB5DNSGXX4W3/recommended"
curl "$BASE/rs/api/v1/product/01K1B1BPGN2WAXFB5DNSGXX4W3/recommended?limit=6"

# ─── CATALOGUE (Legacy) ──────────────────────────────────
curl "$BASE/rs/api/v1/catalogue?subcategory=f_and_b/food/light_bites/energy_bars&page=0&size=20"
curl "$BASE/rs/api/v1/catalogue/mapping"

# ─── SCANNER ──────────────────────────────────────────────
curl -X POST "$BASE/rs/api/v1/scanner" -H "Content-Type: application/json" \
  -d '{"image": "data:image/jpeg;base64,/9j/4AAQ..."}'

# ─── REFRESH CACHE ────────────────────────────────────────
curl -X POST "$BASE/rs/api/v1/home/refresh"
```

---

## API FLOW DIAGRAM

```
HOME SCREEN
  |-- Banners      -> GET /home/banners
  |-- Categories   -> GET /home/categories
  |-- Best Sell    -> GET /home/best-selling           -> [Product Cards]
  |-- Curated      -> GET or POST /home/curated        -> [Product Cards]
  |-- Why Flean    -> GET /home/why-flean
  |-- Collabs      -> GET /home/collaborations

CUSTOMIZE YOUR FEED (bottom sheet)
  |-- Save & Refresh Feed -> POST /home/curated        -> [Product Cards] (random, preferences accepted)

SEARCH / CATALOGUE (use Unified Products API)
  |-- GET or POST /api/v1/products                     -> [Product Cards] with pagination & filters

[Product Card] tapped
  |-- GET /product/{id}                                -> Full PDP
  |-- GET /product/{id}/recommended                    -> "You May Also Like" [Product Cards]

SCANNER
  |-- POST /scanner                                    -> Top 3 [Product Cards]
  |-- User selects one -> GET /product/{id}            -> Full PDP
  |-- GET /product/{id}/alternatives                   -> 5 [Product Cards]
```

---

*Last updated: 14 Mar 2026*
