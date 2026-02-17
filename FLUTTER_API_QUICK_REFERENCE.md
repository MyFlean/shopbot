# Flean Flutter App - API Quick Reference

> **Base URL:** `http://flean-services-alb-806741654.ap-south-1.elb.amazonaws.com`  
> **All endpoints prefix:** `/rs`

---

## Quick Reference Table

| Screen Section | Method | Endpoint | Purpose |
|---------------|--------|----------|---------|
| Home - Banners | `GET` | `/rs/api/v1/home/banners` | Promotional carousel |
| Home - Categories | `GET` | `/rs/api/v1/home/categories` | Category grid (4 items) |
| Home - Categories All | `GET` | `/rs/api/v1/home/categories?all=true` | All categories |
| Home - Best Selling | `GET` | `/rs/api/v1/home/best-selling` | Featured products |
| Home - Curated | `GET` | `/rs/api/v1/home/curated` | 4 random curated |
| Home - Curated All | `GET` | `/rs/api/v1/home/curated/all` | All curated products |
| Home - Why Flean | `GET` | `/rs/api/v1/home/why-flean` | Value proposition cards |
| Home - Collaborations | `GET` | `/rs/api/v1/home/collaborations` | Partner brands |
| Search | `POST` | `/rs/search` | Search + Sort + Filter |
| PDP | `GET` | `/rs/api/v1/product/{id}` | Full product details |
| Scanner | `POST` | `/rs/api/v1/scanner` | Image-based lookup |
| Catalogue | `GET` | `/rs/api/v1/catalogue?subcategory=X` | Products by category |

---

## HOME SCREEN

```
┌─────────────────────────────────────────────────────────┐
│  [1] BANNERS CAROUSEL                                   │
├─────────────────────────────────────────────────────────┤
│  [2] CATEGORIES          [See All →]                    │
│  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐                       │
│  │Smart│ │Dairy│ │Power│ │Sweet│                       │
│  │Snack│ │Bakry│ │Brkft│ │Treat│                       │
│  └─────┘ └─────┘ └─────┘ └─────┘                       │
├─────────────────────────────────────────────────────────┤
│  [3] BEST SELLING                                       │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐           │
│  │Product │ │Product │ │Product │ │Product │           │
│  │  Card  │ │  Card  │ │  Card  │ │  Card  │           │
│  └────────┘ └────────┘ └────────┘ └────────┘           │
├─────────────────────────────────────────────────────────┤
│  [4] CURATED FOR YOU     [See All →]                    │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐           │
│  │Product │ │Product │ │Product │ │Product │           │
│  └────────┘ └────────┘ └────────┘ └────────┘           │
├─────────────────────────────────────────────────────────┤
│  [5] WHY FLEAN                                          │
│  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐                       │
│  │Card1│ │Card2│ │Card3│ │Card4│                       │
│  └─────┘ └─────┘ └─────┘ └─────┘                       │
├─────────────────────────────────────────────────────────┤
│  [6] EXCLUSIVE COLLABORATIONS                           │
│  [Brand1] [Brand2] [Brand3] [Brand4] [Brand5]          │
└─────────────────────────────────────────────────────────┘
```

---

### [1] Banners Carousel

```bash
GET /rs/api/v1/home/banners
```

**Response:**
```json
{
  "success": true,
  "data": {
    "banners": [
      {
        "id": "banner_1",
        "main_heading": "Healthy Snacking Made Easy",
        "sub_heading_1": "Discover 500+ healthy products",
        "button_text": "Shop Now",
        "image_url": "https://flean-product-images.s3.../1+(2).png",
        "background_color": "#F5F5DC"
      }
    ]
  }
}
```

---

### [2] Categories Grid

```bash
# Home page (4 items)
GET /rs/api/v1/home/categories

# See All page (all items)
GET /rs/api/v1/home/categories?all=true
```

**Response:**
```json
{
  "success": true,
  "data": {
    "categories": [
      {
        "id": "smart_snacks",
        "name": "Smart Snacks",
        "icon_url": "https://flean-product-images.s3.../Smart+Snacks.png",
        "deep_link": "flean://category/smart_snacks"
      }
    ],
    "has_more": true,
    "total_count": 8
  }
}
```

---

### [3] Best Selling

```bash
GET /rs/api/v1/home/best-selling
```

**Response:**
```json
{
  "success": true,
  "data": {
    "products": [
      {
        "id": "01K1B1BPGN2WAXFB5DNSGXX4W3",
        "name": "Yoga Bar Protein Bar",
        "brand": "Yoga Bar",
        "price": 100.0,
        "mrp": 120.0,
        "currency": "INR",
        "image_url": "https://cdn.flean.ai/...",
        "macro_tags": [
          {"label": "20 gms of Protein", "nutrient": "protein", "value": 20, "unit": "g"}
        ],
        "flean_score": 2.5,
        "flean_percentile": 85.2,
        "in_stock": true
      }
    ],
    "section_title": "Best Selling"
  }
}
```

---

### [4] Curated For You

```bash
# Home page (4 random items)
GET /rs/api/v1/home/curated

# See All page (all items)
GET /rs/api/v1/home/curated/all
```

**Response (Home):**
```json
{
  "success": true,
  "data": {
    "products": [/* 4 products */],
    "section_title": "Curated For You",
    "has_more": true,
    "total_in_pool": 25
  }
}
```

---

### [5] Why Flean

```bash
GET /rs/api/v1/home/why-flean
```

**Response:**
```json
{
  "success": true,
  "data": {
    "cards": [
      {
        "id": "why_1",
        "main_heading": "AI-Powered Analysis",
        "text_body": "Every product is analyzed by our AI...",
        "icon_url": "https://flean-product-images.s3.../1+(1).png"
      }
    ],
    "section_title": "Why Flean"
  }
}
```

---

### [6] Collaborations

```bash
GET /rs/api/v1/home/collaborations
```

**Response:**
```json
{
  "success": true,
  "data": {
    "brands": [
      {"id": "collab_1", "name": "Everaw", "logo_url": "https://...Everaw.png"},
      {"id": "collab_2", "name": "Hoyi", "logo_url": "https://...Hoyi.png"}
    ],
    "section_title": "Exclusive Collaborations"
  }
}
```

---

## SEARCH SCREEN

```
┌─────────────────────────────────────────────────────────┐
│  🔍 [Search Bar]                                        │
├─────────────────────────────────────────────────────────┤
│  Sort: [Price ▼] [Protein] [Fiber] [Fat]               │
├─────────────────────────────────────────────────────────┤
│  Filters: [Price] [Flean Score] [Preferences] [Dietary]│
├─────────────────────────────────────────────────────────┤
│  Results: 2486 products                                 │
│  ┌────────┐ ┌────────┐ ┌────────┐                      │
│  │Product │ │Product │ │Product │ ...                  │
│  └────────┘ └────────┘ └────────┘                      │
└─────────────────────────────────────────────────────────┘
```

### Search API

```bash
POST /rs/search
Content-Type: application/json
```

### Sort Options

| Value | Description |
|-------|-------------|
| `relevance` | Default - Flean quality ranking |
| `price_asc` | Price: Low → High |
| `price_desc` | Price: High → Low |
| `protein_desc` | Protein: High → Low |
| `fiber_desc` | Fiber: High → Low |
| `fat_asc` | Fat: Low → High |

### Filter Options

| Category | Values | Type |
|----------|--------|------|
| **price_range** | `below_99`, `100_249`, `250_499`, `above_500` | Single |
| **flean_score** | `10`, `9_plus`, `8_plus`, `7_plus` | Single |
| **preferences** | `no_palm_oil`, `no_added_sugar`, `no_additives` | Array |
| **dietary** | `dairy_free`, `gluten_free` | Array |

### Example: Combined Search

**Request:**
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

**Response:**
```json
{
  "products": [
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
        "protein_g": 20,
        "carbs_g": 28,
        "fat_g": 8,
        "fiber_g": 3,
        "calories": 225
      },
      "flean_score": 2.5,
      "flean_percentile": 85.2,
      "in_stock": true
    }
  ],
  "total_hits": 26,
  "returned": 20,
  "sort_by": "protein_desc",
  "filters_applied": {
    "price_range": "100_249",
    "flean_score": "8_plus",
    "preferences": ["no_added_sugar"],
    "dietary": ["gluten_free"]
  }
}
```

---

## PRODUCT SCREENS

### PDP (Product Detail Page)

```bash
GET /rs/api/v1/product/{product_id}
```

**Example:**
```bash
GET /rs/api/v1/product/01K1B1BPGN2WAXFB5DNSGXX4W3
```

**Response:** Full raw Elasticsearch document with ALL product fields:
- `id`, `name`, `brand`, `price`, `mrp`
- `hero_image` (multiple resolutions)
- `category_group`, `category_paths`
- `package_claims` (dietary_labels, health_claims)
- `category_data.nutritional` (qty, nutri_breakdown)
- `flean_score`, `stats` (percentiles)
- `ingredients`

---

### Scanner (Camera Lookup)

```bash
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
    "products": [/* Matching products from ES */]
  }
}
```

---

### Catalogue (Category Listing)

```bash
GET /rs/api/v1/catalogue?subcategory={path}&page={n}&size={n}
```

**Example:**
```bash
GET /rs/api/v1/catalogue?subcategory=chips&page=0&size=20
```

**Response:**
```json
{
  "success": true,
  "data": {
    "products": [/* Array of products */]
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

---

## ERROR HANDLING

All errors follow this format:

```json
{
  "success": false,
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable description"
  }
}
```

| Code | HTTP Status | Meaning |
|------|-------------|---------|
| `MISSING_IMAGE` | 400 | Scanner: No image provided |
| `INVALID_IMAGE` | 400 | Scanner: Bad format/size |
| `MISSING_SUBCATEGORY` | 400 | Catalogue: No subcategory |
| `PRODUCT_NOT_FOUND` | 404 | PDP: Invalid product ID |
| `INTERNAL_ERROR` | 500 | Server error |

---

## UTILITY ENDPOINTS

### Refresh Cache (Admin)
```bash
POST /rs/api/v1/home/refresh
```
Clears cached JSON data. Use after updating home page content.

### Health Check
```bash
GET /rs/api/v1/home/health
```
Returns status of all data files.

---

## CURL EXAMPLES

```bash
# Set base URL
BASE="http://flean-services-alb-806741654.ap-south-1.elb.amazonaws.com"

# Basic search
curl -X POST "$BASE/rs/search" \
  -H "Content-Type: application/json" \
  -d '{"query": "protein bars"}'

# Search with filters
curl -X POST "$BASE/rs/search" \
  -H "Content-Type: application/json" \
  -d '{"query": "chips", "filters": {"price_range": "below_99", "flean_score": "8_plus"}}'

# Home page sections
curl "$BASE/rs/api/v1/home/banners"
curl "$BASE/rs/api/v1/home/categories"
curl "$BASE/rs/api/v1/home/best-selling"
curl "$BASE/rs/api/v1/home/curated"
curl "$BASE/rs/api/v1/home/why-flean"
curl "$BASE/rs/api/v1/home/collaborations"

# PDP
curl "$BASE/rs/api/v1/product/01K1B1BPGN2WAXFB5DNSGXX4W3"

# Catalogue
curl "$BASE/rs/api/v1/catalogue?subcategory=chips&page=0&size=20"
```

---

*Last updated: Feb 2026*

