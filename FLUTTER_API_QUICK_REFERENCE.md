# Flean Flutter App - API Quick Reference

> **Base URL:** `http://api.flean.ai`
> **All endpoints prefix:** `/rs`

---

## Quick Reference Table

| Screen Section | Method | Endpoint | Purpose |
|---------------|--------|----------|---------|
| Home - Banners | `GET` | `/rs/api/v1/home/banners` | Promotional carousel |
| Home - Categories | `GET` | `/rs/api/v1/home/categories` | Category grid (4 items) |
| Home - Categories All | `GET` | `/rs/api/v1/home/categories?all=true` | All categories |
| Home - Best Selling | `GET` | `/rs/api/v1/home/best-selling` | Featured products (4) |
| Home - Curated | `GET` | `/rs/api/v1/home/curated` | 4 random curated |
| Home - Curated All | `GET` | `/rs/api/v1/home/curated/all` | All curated products |
| Home - Why Flean | `GET` | `/rs/api/v1/home/why-flean` | Value proposition cards |
| Home - Collaborations | `GET` | `/rs/api/v1/home/collaborations` | Partner brands |
| Search | `POST` | `/rs/search` | Search + Sort + Filter |
| **PDP** | `GET` | `/rs/api/v1/product/{id}` | **Pre-parsed product detail** |
| **Alternatives** | `GET` | `/rs/api/v1/product/{id}/alternatives` | **5 healthier alternatives** |
| **Scanner** | `POST` | `/rs/api/v1/scanner` | **Top 3 product cards from image** |
| Catalogue | `GET` | `/rs/api/v1/catalogue?subcategory=X` | Products by category |
| **Catalogue Mapping** | `GET` | `/rs/api/v1/catalogue/mapping` | **Category-to-ES-path map** |
| Refresh Cache | `POST` | `/rs/api/v1/home/refresh` | Clear cached data |

---

## STANDARDIZED PRODUCT CARD

All listing APIs (Search, Catalogue, Scanner top 3, Best Selling, Curated, Alternatives) return the **same product card shape**:

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
GET /rs/api/v1/home/best-selling
```
Returns 4 random product cards from a curated pool.

### [4] Curated

```
GET /rs/api/v1/home/curated      # 4 random product cards
GET /rs/api/v1/home/curated/all  # all curated product cards
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

## SEARCH SCREEN

```
POST /rs/search
Content-Type: application/json
```

### Sort Options

| Value | Description |
|-------|-------------|
| `relevance` | Default - Flean quality ranking |
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

### Example Request

```json
{
  "query": "protein bars",
  "sort_by": "protein_desc",
  "filters": {
    "price_range": "100_249",
    "flean_score": "8_plus",
    "preferences": ["no_added_sugar"],
    "dietary": ["gluten_free"]
  }
}
```

### Response

```json
{
  "products": [ /* array of product cards */ ],
  "total_hits": 26,
  "returned": 20,
  "sort_by": "protein_desc",
  "filters_applied": { "price_range": "100_249", "flean_score": "8_plus", "preferences": ["no_added_sugar"], "dietary": ["gluten_free"] }
}
```

---

## PDP (Product Detail Page) -- PRE-PARSED

```
GET /rs/api/v1/product/{product_id}
```

Returns sectioned, pre-parsed data ready for direct Flutter widget mapping.

### Response Structure

```json
{
  "success": true,
  "data": {
    "product_info": {
      "id": "01K1B...",
      "name": "Yoga Bar 20g Protein Bar",
      "brand": "Yoga Bar",
      "price": 100.0,
      "mrp": 120.0,
      "currency": "INR",
      "image_url": "https://cdn.flean.ai/640/...",
      "image_urls": { "640": "https://...", "1080": "https://..." },
      "qty": "60 g",
      "description": "A delicious high-protein snack bar...",
      "category_group": "f_and_b",
      "category_paths": ["f_and_b/food/light_bites/energy_bars"]
    },

    "safety_badge": {
      "text": "100% Safe",
      "level": "safe",
      "flean_score": 2.5,
      "flean_percentile": 85.2
    },

    "score_cards": [
      { "type": "flean_rank", "title": "Flean Rank", "value": "Top 14.8%", "subtitle": "Energy Bars", "raw_percentile": 85.2 },
      { "type": "protein", "title": "Protein", "value": "Top 1.7%", "subtitle": "Efficiency", "raw_percentile": 98.3 },
      { "type": "fiber", "title": "Fiber", "value": "Top 12.0%", "subtitle": "Efficiency", "raw_percentile": 88.0 },
      { "type": "sweeteners", "title": "Sweeteners", "value": "Low", "subtitle": "Percentile: 82", "raw_percentile": 82.0 },
      { "type": "oils", "title": "Oils", "value": "Low", "subtitle": "Percentile: 75", "raw_percentile": 75.0 },
      { "type": "calories", "title": "Calories", "value": "225 kcal", "subtitle": "60 g", "raw_percentile": 60.0 }
    ],

    "highlights": {
      "brand": "Yoga Bar",
      "product_name": "Yoga Bar 20g Protein Bar",
      "weight_volume": "60 g",
      "unit": "",
      "packaging_type": "",
      "dietary_preference": "Vegetarian",
      "allergen_info": "Contains nuts, soy",
      "storage_instructions": "Store in cool dry place",
      "health_claims": ["High Protein", "No Trans Fat"]
    },

    "ingredients": {
      "list": ["Whey Protein", "Oats", "Almonds", "Cocoa Powder"],
      "raw_text": "Whey Protein, Oats, Almonds, Cocoa Powder"
    },

    "nutritional_table": [
      { "nutrient": "Energy", "value": "225", "unit": "kcal" },
      { "nutrient": "Protein", "value": "20", "unit": "g" },
      { "nutrient": "Carbohydrates", "value": "28", "unit": "g" },
      { "nutrient": "Sugars", "value": "12", "unit": "g" },
      { "nutrient": "Total Fat", "value": "8", "unit": "g" },
      { "nutrient": "Fiber", "value": "3", "unit": "g" },
      { "nutrient": "Sodium", "value": "150", "unit": "mg" }
    ],

    "nutritional_basis": "60 g",

    "additional_info": {
      "disclaimer": "Product packaging may change...",
      "seller_name": "",
      "manufacturer_name": "",
      "country_of_origin": "India",
      "shelf_life": ""
    },

    "macro_tags": [
      {"label": "20 gms of Protein", "nutrient": "protein", "value": 20, "unit": "g"},
      {"label": "225 Calories", "nutrient": "calories", "value": 225, "unit": "kcal"}
    ]
  }
}
```

### Safety Badge Levels

| `level` | `text` | Condition |
|---------|--------|-----------|
| `safe` | 100% Safe | percentile >= 70 |
| `caution` | Use with Caution | percentile 40-69 |
| `warning` | Not Recommended | percentile < 40 |
| `unknown` | Not Rated | no percentile data |

### Score Card Types

| `type` | Source |
|--------|--------|
| `flean_rank` | `stats.adjusted_score_percentiles.subcategory_percentile` |
| `protein` | `stats.protein_percentiles.subcategory_percentile` |
| `fiber` | `stats.fiber_percentiles.subcategory_percentile` |
| `sweeteners` | `stats.sweetener_penalty_percentiles.subcategory_percentile` |
| `oils` | `stats.oil_penalty_percentiles.subcategory_percentile` |
| `calories` | Energy value + `stats.calories_penalty_percentiles` |
| `watch_out` | Shown only if `empty_food_penalty_percentile < 40` |

---

## SCANNER FLOW (3 Steps)

```
Step 1: POST /rs/api/v1/scanner             -> Top 3 product cards
Step 2: GET  /rs/api/v1/product/{id}         -> Full PDP of selected product
Step 3: GET  /rs/api/v1/product/{id}/alternatives -> 5 healthier options
```

### Step 1: Scan Image (Top 3 Cards)

```
POST /rs/api/v1/scanner
Content-Type: application/json
```

**Request:**
```json
{
  "image": "data:image/jpeg;base64,/9j/4AAQSkZ..."
}
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
    "products": [
      { /* product card 1 */ },
      { /* product card 2 */ },
      { /* product card 3 */ }
    ]
  }
}
```

Show the 3 product cards to the user. When user selects one, call PDP (Step 2).

### Step 2: Get PDP for Selected Product

```
GET /rs/api/v1/product/{selected_product_id}
```

Returns full PDP data as documented above.

### Step 3: Get Healthier Alternatives

```
GET /rs/api/v1/product/{selected_product_id}/alternatives
```

**Response:**
```json
{
  "success": true,
  "data": {
    "source_product": { /* product card of selected product */ },
    "alternatives": [
      { /* product card 1 - healthiest */ },
      { /* product card 2 */ },
      { /* product card 3 */ },
      { /* product card 4 */ },
      { /* product card 5 */ }
    ]
  }
}
```

Alternatives are from the same subcategory, sorted by Flean percentile (healthiest first).

---

## CATALOGUE SCREEN

### Get Products by Subcategory

```
GET /rs/api/v1/catalogue?subcategory={es_path}&page=0&size=20&sort=flean_score
```

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `subcategory` | Yes | - | ES path from mapping API |
| `page` | No | 0 | Page number (0-indexed) |
| `size` | No | 20 | Items per page (1-100) |
| `sort` | No | `flean_score` | `flean_score` or `price` |

**Example:**
```
GET /rs/api/v1/catalogue?subcategory=f_and_b/food/light_bites/energy_bars&page=0&size=20
```

**Response:**
```json
{
  "success": true,
  "data": {
    "products": [ /* array of product cards */ ]
  },
  "meta": {
    "total": 298,
    "page": 0,
    "size": 20,
    "total_pages": 15,
    "has_next": true,
    "has_prev": false
  }
}
```

### Get Category Mapping (for hardcoding subcategory paths)

```
GET /rs/api/v1/catalogue/mapping
```

Returns the full mapping of **display names** to **ES paths** the developer needs to pass as the `subcategory` parameter.

**Response:**
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
          { "display_name": "Dry Fruits & Nuts", "es_path": "f_and_b/food/light_bites/dry_fruit_and_nut_snacks" },
          { "display_name": "Energy Bars", "es_path": "f_and_b/food/light_bites/energy_bars" },
          { "display_name": "Nachos", "es_path": "f_and_b/food/light_bites/nachos" },
          { "display_name": "Popcorn", "es_path": "f_and_b/food/light_bites/popcorn" },
          { "display_name": "Savory Namkeen", "es_path": "f_and_b/food/light_bites/savory_namkeen" }
        ]
      },
      {
        "display_name": "Dairy & Bakery",
        "es_path": "f_and_b/food/dairy_and_bakery",
        "subcategories": [
          { "display_name": "Bread & Buns", "es_path": "f_and_b/food/dairy_and_bakery/bread_and_buns" },
          { "display_name": "Butter", "es_path": "f_and_b/food/dairy_and_bakery/butter" },
          { "display_name": "Cheese", "es_path": "f_and_b/food/dairy_and_bakery/cheese" }
        ]
      }
    ],
    "popular_searches": ["High Protein Snacks", "No Added Sugar Peanut Butter", "High Protein Yogurt", "Millet Protein Cookies"],
    "search_by_category": [
      { "display_name": "Dairy & Bakery", "es_path": "f_and_b/food/dairy_and_bakery" },
      { "display_name": "Smart Snacks", "es_path": "f_and_b/food/light_bites" },
      { "display_name": "Power Breakfast", "es_path": "f_and_b/food/breakfast_essentials" },
      { "display_name": "Sweet Treats", "es_path": "f_and_b/food/sweet_treats" }
    ]
  }
}
```

---

## ERROR HANDLING

All errors follow this format:

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

## CURL EXAMPLES

```bash
BASE="http://api.flean.ai"

# --- SEARCH ---
curl -X POST "$BASE/rs/search" -H "Content-Type: application/json" \
  -d '{"query": "protein bars"}'

curl -X POST "$BASE/rs/search" -H "Content-Type: application/json" \
  -d '{"query": "chips", "sort_by": "price_asc", "filters": {"price_range": "below_99"}}'

# --- HOME PAGE ---
curl "$BASE/rs/api/v1/home/banners"
curl "$BASE/rs/api/v1/home/categories"
curl "$BASE/rs/api/v1/home/best-selling"
curl "$BASE/rs/api/v1/home/curated"
curl "$BASE/rs/api/v1/home/curated/all"
curl "$BASE/rs/api/v1/home/why-flean"
curl "$BASE/rs/api/v1/home/collaborations"

# --- PDP (pre-parsed) ---
curl "$BASE/rs/api/v1/product/01K1B1BPGN2WAXFB5DNSGXX4W3"

# --- ALTERNATIVES ---
curl "$BASE/rs/api/v1/product/01K1B1BPGN2WAXFB5DNSGXX4W3/alternatives"

# --- CATALOGUE ---
curl "$BASE/rs/api/v1/catalogue?subcategory=f_and_b/food/light_bites/energy_bars&page=0&size=20"

# --- CATALOGUE MAPPING ---
curl "$BASE/rs/api/v1/catalogue/mapping"

# --- SCANNER ---
curl -X POST "$BASE/rs/api/v1/scanner" -H "Content-Type: application/json" \
  -d '{"image": "data:image/jpeg;base64,/9j/4AAQ..."}'

# --- REFRESH CACHE ---
curl -X POST "$BASE/rs/api/v1/home/refresh"
```

---

## API FLOW DIAGRAM

```
HOME SCREEN
  |-- Banners     -> GET /home/banners
  |-- Categories  -> GET /home/categories
  |-- Best Sell   -> GET /home/best-selling         -> [Product Cards]
  |-- Curated     -> GET /home/curated              -> [Product Cards]
  |-- Why Flean   -> GET /home/why-flean
  |-- Collabs     -> GET /home/collaborations

SEARCH
  |-- POST /search  -> [Product Cards]

CATALOGUE
  |-- GET /catalogue/mapping                        -> Category paths
  |-- GET /catalogue?subcategory=X                  -> [Product Cards]

[Product Card] tapped
  |-- GET /product/{id}                             -> Full PDP

SCANNER
  |-- POST /scanner                                 -> Top 3 [Product Cards]
  |-- User selects one -> GET /product/{id}         -> Full PDP
  |-- GET /product/{id}/alternatives                -> 5 [Product Cards]
```

---

*Last updated: Feb 2026*
