# Flutter Product Search API Documentation

## Overview

This API provides a comprehensive product search endpoint for the Flutter app. It proxies Elasticsearch queries through a secure backend, eliminating the need for direct ES access from the mobile app.

## Base URL

```
Production: https://your-api-domain.com
Local Dev:  http://localhost:8080
```

---

## Endpoint

### `POST /api/v1/products/search`

Search for products with comprehensive filtering support.

---

## Request

### Headers

```
Content-Type: application/json
```

### Body Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | ✅ Yes | Search keyword (e.g., "chips", "protein powder") |
| `category_group` | string | No | Product category: `"f_and_b"` or `"personal_care"` |
| `category_paths` | string[] | No | Category hierarchy (e.g., `["f_and_b/food/snacks"]`) |
| `price_min` | number | No | Minimum price in INR |
| `price_max` | number | No | Maximum price in INR |
| `dietary_terms` | string[] | No | Dietary filters (e.g., `["GLUTEN FREE", "VEGAN"]`) |
| `avoid_ingredients` | string[] | No | Ingredients to exclude (e.g., `["palm oil", "maida"]`) |
| `brands` | string[] | No | Filter by brands (e.g., `["Lays", "Pringles"]`) |
| `healthy_only` | boolean | No | If `true`, only returns products with flean percentile ≥ 70 |
| `min_flean_percentile` | number | No | Custom quality threshold (0-100) |
| `size` | number | No | Number of results (1-50, default: 20) |

### Supported Dietary Terms

```
GLUTEN FREE, VEGAN, VEGETARIAN, PALM OIL FREE,
SUGAR FREE, LOW SODIUM, LOW SUGAR, ORGANIC,
NO ADDED SUGAR, DAIRY FREE, NUT FREE, SOY FREE,
KETO, HIGH PROTEIN, LOW FAT, WHOLE GRAIN,
NO PRESERVATIVES, NO ARTIFICIAL COLORS, NON GMO
```

---

## Response

### Success Response (200 OK)

```json
{
    "success": true,
    "data": {
        "products": [
            {
                "id": "prod_123",
                "name": "Organic Potato Chips",
                "brand": "Healthy Crunch",
                "price": 75.0,
                "mrp": 100.0,
                "currency": "INR",
                "image_url": "https://cdn.example.com/image.jpg",
                "description": "Made with organic potatoes...",
                "category": "f_and_b",
                "flean_score": 78.5,
                "flean_percentile": 85.2,
                "quality_tier": "excellent",
                "nutrition": {
                    "protein_g": 2.5,
                    "carbs_g": 15.0,
                    "fat_g": 8.0,
                    "calories": 120
                },
                "dietary_labels": ["ORGANIC", "GLUTEN FREE"],
                "health_claims": ["organic", "gluten free"],
                "rating": {
                    "average": 4.2,
                    "total_reviews": 156
                },
                "in_stock": true
            }
        ],
        "pagination": {
            "total_hits": 1250,
            "returned": 20,
            "size": 20
        }
    },
    "meta": {
        "took_ms": 45,
        "filters_applied": {
            "q": "chips",
            "category_group": "f_and_b",
            "price_max": 200,
            "dietary_terms": ["ORGANIC"]
        }
    }
}
```

### Quality Tiers

| Tier | Flean Percentile |
|------|------------------|
| `excellent` | ≥ 80 |
| `good` | 60-79 |
| `average` | 40-59 |
| `below_average` | < 40 |

### Error Response (400 Bad Request)

```json
{
    "success": false,
    "error": {
        "code": "VALIDATION_ERROR",
        "message": "'query' is required and must be a non-empty string"
    }
}
```

### Error Response (500 Internal Error)

```json
{
    "success": false,
    "error": {
        "code": "INTERNAL_ERROR",
        "message": "An unexpected error occurred while processing your request"
    }
}
```

---

## CURL Examples

### 1. Basic Search

```bash
curl -X POST "http://localhost:8080/api/v1/products/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "chips"
  }'
```

### 2. Search with Price Filter

```bash
curl -X POST "http://localhost:8080/api/v1/products/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "chips",
    "price_min": 50,
    "price_max": 150
  }'
```

### 3. Healthy Products Only

```bash
curl -X POST "http://localhost:8080/api/v1/products/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "chips",
    "healthy_only": true
  }'
```

### 4. Custom Quality Threshold

```bash
curl -X POST "http://localhost:8080/api/v1/products/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "protein powder",
    "min_flean_percentile": 80
  }'
```

### 5. No Palm Oil Filter

```bash
curl -X POST "http://localhost:8080/api/v1/products/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "chips",
    "avoid_ingredients": ["palm oil"]
  }'
```

### 6. Multiple Dietary Filters

```bash
curl -X POST "http://localhost:8080/api/v1/products/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "snacks",
    "dietary_terms": ["GLUTEN FREE", "VEGAN", "ORGANIC"]
  }'
```

### 7. Brand Filter

```bash
curl -X POST "http://localhost:8080/api/v1/products/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "chips",
    "brands": ["Lays", "Pringles", "Bingo"]
  }'
```

### 8. Category Filter

```bash
curl -X POST "http://localhost:8080/api/v1/products/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "face wash",
    "category_group": "personal_care"
  }'
```

### 9. Combined Filters (Complex Query)

```bash
curl -X POST "http://localhost:8080/api/v1/products/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "chips",
    "category_group": "f_and_b",
    "price_min": 30,
    "price_max": 100,
    "dietary_terms": ["PALM OIL FREE"],
    "avoid_ingredients": ["maida", "msg"],
    "healthy_only": true,
    "size": 10
  }'
```

### 10. Personal Care with Skin Concerns

```bash
curl -X POST "http://localhost:8080/api/v1/products/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "moisturizer for dry skin",
    "category_group": "personal_care",
    "avoid_ingredients": ["paraben", "sulfate"],
    "price_max": 500,
    "size": 15
  }'
```

---

## Health Check

### `GET /api/v1/products/health`

Check the health status of the product search API.

```bash
curl "http://localhost:8080/api/v1/products/health"
```

**Response:**

```json
{
    "status": "healthy",
    "elasticsearch": "connected",
    "version": "1.0.0"
}
```

---

## Flutter Integration Example

### Dart/Flutter Code

```dart
import 'dart:convert';
import 'package:http/http.dart' as http;

class ProductSearchService {
  final String baseUrl;
  
  ProductSearchService({required this.baseUrl});
  
  Future<ProductSearchResponse> searchProducts({
    required String query,
    String? categoryGroup,
    double? priceMin,
    double? priceMax,
    List<String>? dietaryTerms,
    List<String>? avoidIngredients,
    List<String>? brands,
    bool? healthyOnly,
    double? minFleanPercentile,
    int? size,
  }) async {
    final body = <String, dynamic>{
      'query': query,
    };
    
    if (categoryGroup != null) body['category_group'] = categoryGroup;
    if (priceMin != null) body['price_min'] = priceMin;
    if (priceMax != null) body['price_max'] = priceMax;
    if (dietaryTerms != null) body['dietary_terms'] = dietaryTerms;
    if (avoidIngredients != null) body['avoid_ingredients'] = avoidIngredients;
    if (brands != null) body['brands'] = brands;
    if (healthyOnly != null) body['healthy_only'] = healthyOnly;
    if (minFleanPercentile != null) body['min_flean_percentile'] = minFleanPercentile;
    if (size != null) body['size'] = size;
    
    final response = await http.post(
      Uri.parse('$baseUrl/api/v1/products/search'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode(body),
    );
    
    if (response.statusCode == 200) {
      return ProductSearchResponse.fromJson(jsonDecode(response.body));
    } else {
      throw ProductSearchException(
        code: jsonDecode(response.body)['error']['code'],
        message: jsonDecode(response.body)['error']['message'],
      );
    }
  }
}

// Usage Example
void main() async {
  final service = ProductSearchService(baseUrl: 'https://api.yourapp.com');
  
  // Basic search
  final results = await service.searchProducts(query: 'chips');
  
  // Healthy chips under ₹100
  final healthyChips = await service.searchProducts(
    query: 'chips',
    healthyOnly: true,
    priceMax: 100,
  );
  
  // Gluten-free snacks without palm oil
  final gfSnacks = await service.searchProducts(
    query: 'snacks',
    dietaryTerms: ['GLUTEN FREE'],
    avoidIngredients: ['palm oil'],
  );
}
```

---

## Rate Limits

Currently no rate limits are enforced. This may change in future versions.

---

## Changelog

### v1.0.0 (Current)
- Initial release
- Support for all filter types
- Quality-based re-ranking by flean percentile
- Fallback strategies for zero-result queries

