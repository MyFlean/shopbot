# Shopping Bot Product Search API - Developer Documentation

## Table of Contents
1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Environment Setup](#environment-setup)
4. [API Reference](#api-reference)
5. [Integration Examples](#integration-examples)
6. [Testing](#testing)
7. [Deployment](#deployment)
8. [Troubleshooting](#troubleshooting)
9. [Changelog](#changelog)

---

## Overview

The Shopping Bot Product Search API provides a comprehensive product search solution built on Elasticsearch. It offers advanced filtering capabilities including dietary restrictions, ingredient avoidance, brand filtering, price ranges, and quality-based ranking.

### Key Features
- ✅ **Advanced Search**: Multi-field search with fuzzy matching
- ✅ **Comprehensive Filtering**: Price, dietary, ingredients, brands, categories
- ✅ **Quality Ranking**: Flean percentile-based product ranking
- ✅ **Healthy Filtering**: Built-in healthy product detection
- ✅ **Pagination**: Configurable result sizes
- ✅ **Fallback Strategies**: Intelligent query relaxation on zero results
- ✅ **RESTful API**: Clean, documented endpoints
- ✅ **Health Monitoring**: Built-in health checks

### Supported Product Types
- **Food & Beverages** (`f_and_b`): Snacks, packaged foods, beverages
- **Personal Care** (`personal_care`): Skincare, hair care, cosmetics

---

## Architecture

### System Components

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Flutter App   │────│  Shopping Bot    │────│  Elasticsearch  │
│                 │    │   API Server     │    │     Cluster     │
│ • Search UI     │    │                  │    │                 │
│ • Filter UI     │    │ • Route Handler  │    │ • Product Index │
│ • Results List  │    │ • Param Parser   │    │ • Query Engine  │
└─────────────────┘    │ • ES Client      │    │ • Scoring       │
                       │ • Response       │    │                 │
                       │   Formatter      │    └─────────────────┘
                       └──────────────────┘
```

### Data Flow

1. **User Query** → Flutter app sends search request
2. **Parameter Validation** → Backend validates and normalizes parameters
3. **Query Building** → Constructs optimized Elasticsearch query
4. **Search Execution** → Queries Elasticsearch with filtering and scoring
5. **Result Processing** → Applies flean percentile re-ranking
6. **Response Formatting** → Returns structured JSON response
7. **Fallback Handling** → Relaxes filters if no results found

### Key Technologies
- **Backend**: Python Flask 1.1.2
- **Search Engine**: Elasticsearch 7.x+
- **Cache**: Redis (optional)
- **Serialization**: JSON
- **Deployment**: Docker/Gunicorn

---

## Environment Setup

### Prerequisites
- Python 3.8+
- Elasticsearch 7.x+
- Redis (optional, for session management)
- Docker (for containerized deployment)

### Local Development Setup

#### 1. Clone Repository
```bash
git clone <repository-url>
cd shopbot
```

#### 2. Install Dependencies
```bash
# Create virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install requirements
pip install -r requirements.txt
```

#### 3. Environment Configuration

Create `.env` file in project root:

```bash
# Elasticsearch Configuration
ES_URL=https://your-elasticsearch-cluster.com:443
ELASTIC_INDEX=products-v2
ES_API_KEY=your-elasticsearch-api-key

# Optional: Redis for session management
REDIS_HOST=localhost
REDIS_PORT=6379

# Optional: Anthropic API (for chat features)
ANTHROPIC_API_KEY=your-anthropic-key

# Server Configuration
HOST=127.0.0.1
PORT=8080
APP_ENV=development
```

#### 4. Start Services

**Option A: Using Docker Compose**
```bash
# If you have docker-compose.yml
docker-compose up -d elasticsearch redis
```

**Option B: Local Services**
```bash
# Start Elasticsearch (if not using Docker)
# Start Redis (if not using Docker)
```

#### 5. Run Application
```bash
# Development server
python3 run.py

# Or with Gunicorn for production
gunicorn --bind 0.0.0.0:8080 --workers 4 run:app
```

#### 6. Verify Setup
```bash
# Test health endpoint
curl http://localhost:8080/api/v1/products/health

# Test search endpoint
curl -X POST http://localhost:8080/api/v1/products/search \
  -H "Content-Type: application/json" \
  -d '{"query": "chips"}'
```

### Data Requirements

#### Elasticsearch Index Structure
Your Elasticsearch index must contain products with these fields:

**Required Fields:**
- `id`: Unique product identifier
- `name`: Product name
- `brand`: Brand name
- `price`: Current price (numeric)
- `mrp`: Maximum retail price (numeric)

**Quality & Scoring Fields:**
- `flean_score.adjusted_score`: Quality score (numeric)
- `stats.adjusted_score_percentiles.subcategory_percentile`: Quality percentile (0-100)

**Categorization Fields:**
- `category_group`: "f_and_b" or "personal_care"
- `category_paths`: Array of category hierarchies

**Content Fields:**
- `description`: Product description
- `package_claims.dietary_labels`: Array of dietary labels
- `package_claims.health_claims`: Array of health claims
- `ingredients.raw_text`: Ingredient list

**Media Fields:**
- `hero_image.*`: Image URLs at different resolutions

---

## API Reference

### Endpoints

#### `POST /api/v1/products/search`
Search for products with advanced filtering.

**Request Headers:**
```
Content-Type: application/json
```

**Request Body:**
```json
{
  "query": "chips",
  "category_group": "f_and_b",
  "price_min": 50,
  "price_max": 200,
  "dietary_terms": ["GLUTEN FREE", "ORGANIC"],
  "avoid_ingredients": ["palm oil"],
  "brands": ["Lays", "Pringles"],
  "healthy_only": true,
  "size": 20
}
```

**Response:**
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
      "query": "chips",
      "category_group": "f_and_b",
      "healthy_only": true
    }
  }
}
```

#### `GET /api/v1/products/health`
Health check endpoint.

**Response:**
```json
{
  "status": "healthy",
  "elasticsearch": "connected",
  "version": "1.0.0"
}
```

### Parameter Reference

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `query` | string | ✅ Yes | - | Search keyword |
| `category_group` | string | No | - | "f_and_b" or "personal_care" |
| `category_paths` | string[] | No | - | Category hierarchies |
| `price_min` | number | No | - | Minimum price (INR) |
| `price_max` | number | No | - | Maximum price (INR) |
| `dietary_terms` | string[] | No | - | Dietary filters |
| `avoid_ingredients` | string[] | No | - | Ingredients to exclude |
| `brands` | string[] | No | - | Brand filters |
| `healthy_only` | boolean | No | false | Quality filter (≥70 percentile) |
| `min_flean_percentile` | number | No | 0 | Custom quality threshold |
| `size` | number | No | 20 | Results per page (1-50) |

### Supported Dietary Terms

```
GLUTEN FREE, VEGAN, VEGETARIAN, PALM OIL FREE,
SUGAR FREE, LOW SODIUM, LOW SUGAR, ORGANIC,
NO ADDED SUGAR, DAIRY FREE, NUT FREE, SOY FREE,
KETO, HIGH PROTEIN, LOW FAT, WHOLE GRAIN,
NO PRESERVATIVES, NO ARTIFICIAL COLORS, NON GMO
```

### Quality Tiers

| Tier | Flean Percentile Range |
|------|------------------------|
| `excellent` | ≥ 80 |
| `good` | 60 - 79 |
| `average` | 40 - 59 |
| `below_average` | < 40 |

### Error Responses

**Validation Error:**
```json
{
  "success": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "'query' is required and must be a non-empty string"
  }
}
```

**Server Error:**
```json
{
  "success": false,
  "error": {
    "code": "INTERNAL_ERROR",
    "message": "An unexpected error occurred"
  }
}
```

---

## Integration Examples

### Flutter/Dart Integration

#### Basic Search
```dart
class ProductSearchService {
  final String baseUrl;

  Future<List<Product>> searchProducts(String query) async {
    final response = await http.post(
      Uri.parse('$baseUrl/api/v1/products/search'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'query': query}),
    );

    if (response.statusCode == 200) {
      final data = jsonDecode(response.body);
      return (data['data']['products'] as List)
          .map((json) => Product.fromJson(json))
          .toList();
    } else {
      throw Exception('Search failed');
    }
  }
}
```

#### Advanced Search with Filters
```dart
Future<List<Product>> searchWithFilters({
  required String query,
  double? minPrice,
  double? maxPrice,
  List<String>? dietaryTerms,
  bool? healthyOnly,
}) async {
  final body = {
    'query': query,
    if (minPrice != null) 'price_min': minPrice,
    if (maxPrice != null) 'price_max': maxPrice,
    if (dietaryTerms != null) 'dietary_terms': dietaryTerms,
    if (healthyOnly != null) 'healthy_only': healthyOnly,
  };

  final response = await http.post(
    Uri.parse('$baseUrl/api/v1/products/search'),
    headers: {'Content-Type': 'application/json'},
    body: jsonEncode(body),
  );

  // Handle response...
}
```

#### React Native Integration
```javascript
const searchProducts = async (query, filters = {}) => {
  const response = await fetch(`${BASE_URL}/api/v1/products/search`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      query,
      ...filters,
    }),
  });

  const data = await response.json();
  return data.data.products;
};

// Usage
const results = await searchProducts('chips', {
  healthy_only: true,
  price_max: 100,
  dietary_terms: ['GLUTEN FREE']
});
```

### Product Model (Flutter)
```dart
class Product {
  final String id;
  final String name;
  final String brand;
  final double price;
  final double? mrp;
  final String currency;
  final String? imageUrl;
  final String? description;
  final String category;
  final double? fleanScore;
  final double? fleanPercentile;
  final String? qualityTier;
  final List<String> dietaryLabels;
  final List<String> healthClaims;
  final bool inStock;

  Product({
    required this.id,
    required this.name,
    required this.brand,
    required this.price,
    this.mrp,
    required this.currency,
    this.imageUrl,
    this.description,
    required this.category,
    this.fleanScore,
    this.fleanPercentile,
    this.qualityTier,
    required this.dietaryLabels,
    required this.healthClaims,
    required this.inStock,
  });

  factory Product.fromJson(Map<String, dynamic> json) {
    return Product(
      id: json['id'],
      name: json['name'],
      brand: json['brand'],
      price: json['price'],
      mrp: json['mrp'],
      currency: json['currency'],
      imageUrl: json['image_url'],
      description: json['description'],
      category: json['category'],
      fleanScore: json['flean_score'],
      fleanPercentile: json['flean_percentile'],
      qualityTier: json['quality_tier'],
      dietaryLabels: List<String>.from(json['dietary_labels'] ?? []),
      healthClaims: List<String>.from(json['health_claims'] ?? []),
      inStock: json['in_stock'] ?? true,
    );
  }
}
```

---

## Testing

### Unit Tests
```bash
# Run specific test
python3 -m pytest shopping_bot/tests/test_product_search.py -v

# Run all tests
python3 -m pytest
```

### API Testing with curl

#### Basic Functionality Test
```bash
# Test health endpoint
curl "http://localhost:8080/api/v1/products/health"

# Test basic search
curl -X POST "http://localhost:8080/api/v1/products/search" \
  -H "Content-Type: application/json" \
  -d '{"query": "chips"}' | jq '.success'

# Test filtering
curl -X POST "http://localhost:8080/api/v1/products/search" \
  -H "Content-Type: application/json" \
  -d '{"query": "chips", "healthy_only": true}' | jq '.data.products[0].quality_tier'
```

#### Load Testing
```bash
# Simple load test with ab (Apache Bench)
ab -n 100 -c 10 -p test_data.json -T application/json \
  http://localhost:8080/api/v1/products/search
```

#### Error Testing
```bash
# Test validation
curl -X POST "http://localhost:8080/api/v1/products/search" \
  -H "Content-Type: application/json" \
  -d '{}' | jq '.error.message'

# Test invalid parameters
curl -X POST "http://localhost:8080/api/v1/products/search" \
  -H "Content-Type: application/json" \
  -d '{"query": "", "size": 100}' | jq '.error.message'
```

### Integration Testing
```python
import requests
import pytest

BASE_URL = "http://localhost:8080"

def test_basic_search():
    response = requests.post(f"{BASE_URL}/api/v1/products/search",
                           json={"query": "chips"})
    assert response.status_code == 200
    data = response.json()
    assert data["success"] == True
    assert "products" in data["data"]

def test_healthy_filter():
    response = requests.post(f"{BASE_URL}/api/v1/products/search",
                           json={"query": "chips", "healthy_only": True})
    assert response.status_code == 200
    data = response.json()
    # Verify all returned products have high quality
    for product in data["data"]["products"]:
        if "quality_tier" in product:
            assert product["quality_tier"] in ["excellent", "good"]

def test_price_filter():
    response = requests.post(f"{BASE_URL}/api/v1/products/search",
                           json={"query": "chips", "price_max": 50})
    assert response.status_code == 200
    data = response.json()
    # Verify all products are within price range
    for product in data["data"]["products"]:
        assert product["price"] <= 50
```

---

## Deployment

### Docker Deployment

#### Dockerfile
```dockerfile
FROM python:3.9-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8080/api/v1/products/health || exit 1

# Run application
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "4", "run:app"]
```

#### Docker Compose
```yaml
version: '3.8'

services:
  app:
    build: .
    ports:
      - "8080:8080"
    environment:
      - ES_URL=https://your-es-cluster.com:443
      - ELASTIC_INDEX=products-v2
      - ES_API_KEY=your-api-key
      - REDIS_HOST=redis
    depends_on:
      - redis
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/api/v1/products/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  redis:
    image: redis:6-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data

volumes:
  redis_data:
```

### Production Deployment

#### Environment Variables
```bash
# Production environment
APP_ENV=production
HOST=0.0.0.0
PORT=8080

# Elasticsearch (production cluster)
ES_URL=https://prod-es-cluster.company.com:443
ELASTIC_INDEX=products-v2
ES_API_KEY=production-api-key

# Redis (production instance)
REDIS_HOST=prod-redis.company.com
REDIS_PORT=6379

# Monitoring
SENTRY_DSN=https://your-sentry-dsn@sentry.io/project
LOG_LEVEL=WARNING
```

#### Nginx Configuration
```nginx
upstream shopping_bot {
    server app:8080;
}

server {
    listen 80;
    server_name api.yourcompany.com;

    location / {
        proxy_pass http://shopping_bot;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Health check endpoint (no logging)
    location /api/v1/products/health {
        proxy_pass http://shopping_bot;
        access_log off;
    }
}
```

#### SSL/TLS Configuration
```nginx
server {
    listen 443 ssl http2;
    server_name api.yourcompany.com;

    ssl_certificate /etc/ssl/certs/your-cert.pem;
    ssl_certificate_key /etc/ssl/private/your-key.pem;

    # SSL configuration...
}
```

### Monitoring & Logging

#### Health Checks
```bash
# Application health
curl https://api.yourcompany.com/api/v1/products/health

# Elasticsearch connectivity
curl https://api.yourcompany.com/api/v1/products/health | jq '.elasticsearch'
```

#### Log Aggregation
```python
import logging
from logging.handlers import SysLogHandler

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
    handlers=[
        SysLogHandler(address='/dev/log'),
        logging.StreamHandler()
    ]
)
```

#### Metrics Collection
```python
from flask import g, request
import time

@app.before_request
def start_timer():
    g.start_time = time.time()

@app.after_request
def log_request(response):
    duration = time.time() - g.start_time
    # Log metrics to your monitoring system
    log.info(f"Request: {request.method} {request.path} | Duration: {duration:.3f}s | Status: {response.status_code}")
    return response
```

---

## Troubleshooting

### Common Issues

#### 1. "Cannot POST /api/v1/products/search"
**Problem**: Route not found
**Solutions**:
- Check if server is running: `curl http://localhost:8080/api/v1/products/health`
- Verify route registration in `shopping_bot/__init__.py`
- Check Flask app logs for import errors

#### 2. Elasticsearch Connection Failed
**Problem**: Cannot connect to Elasticsearch
**Solutions**:
- Verify `ES_URL` and `ES_API_KEY` environment variables
- Check Elasticsearch cluster status
- Test connection: `curl -H "Authorization: ApiKey $ES_API_KEY" $ES_URL/_cluster/health`

#### 3. Empty Search Results
**Problem**: Queries return no results
**Solutions**:
- Check if products exist in Elasticsearch index
- Verify index name and mapping
- Test basic query: `curl "$ES_URL/$ELASTIC_INDEX/_search?q=name:chips"`

#### 4. High Latency
**Problem**: Slow response times
**Solutions**:
- Check Elasticsearch cluster performance
- Monitor query execution time in response metadata
- Consider pagination and result size limits

#### 5. Memory Issues
**Problem**: Application running out of memory
**Solutions**:
- Reduce `size` parameter default
- Implement result caching
- Monitor Redis memory usage
- Adjust Gunicorn worker count

### Debug Commands

#### Check Application Status
```bash
# Process status
ps aux | grep python

# Flask routes
curl http://localhost:8080/ | grep -i route

# Health check
curl http://localhost:8080/api/v1/products/health
```

#### Elasticsearch Diagnostics
```bash
# Cluster health
curl -H "Authorization: ApiKey $ES_API_KEY" "$ES_URL/_cluster/health?pretty"

# Index info
curl -H "Authorization: ApiKey $ES_API_KEY" "$ES_URL/_cat/indices/$ELASTIC_INDEX?v"

# Sample documents
curl -H "Authorization: ApiKey $ES_API_KEY" "$ES_URL/$ELASTIC_INDEX/_search?size=1&pretty"
```

#### Network Diagnostics
```bash
# Test connectivity
telnet your-es-cluster.com 443

# DNS resolution
nslookup your-es-cluster.com

# SSL certificate
openssl s_client -connect your-es-cluster.com:443 -servername your-es-cluster.com
```

### Performance Optimization

#### Query Optimization
- Use appropriate analyzers for text fields
- Implement field-level boosting
- Consider query result caching
- Optimize source field selection

#### Infrastructure Tuning
```bash
# Gunicorn configuration for production
gunicorn \
  --bind 0.0.0.0:8080 \
  --workers 4 \
  --worker-class gthread \
  --threads 2 \
  --max-requests 1000 \
  --max-requests-jitter 50 \
  run:app
```

#### Caching Strategy
```python
from flask_caching import Cache

cache = Cache(app, config={'CACHE_TYPE': 'redis'})

@cache.memoize(timeout=300)  # 5 minute cache
def search_products(query, filters):
    # Expensive search operation
    return perform_search(query, filters)
```

---

## Changelog

### v1.0.0 (Current)
- ✅ Initial release
- ✅ Comprehensive product search API
- ✅ Advanced filtering (dietary, ingredients, brands, price)
- ✅ Quality-based ranking with flean percentile
- ✅ Healthy product filtering
- ✅ Fallback strategies for zero results
- ✅ RESTful API design
- ✅ Health monitoring endpoints
- ✅ Comprehensive documentation

### Future Enhancements
- [ ] User personalization
- [ ] Advanced analytics
- [ ] Real-time inventory updates
- [ ] Multi-language support
- [ ] Voice search integration
- [ ] Image-based search
- [ ] Recommendation engine
- [ ] A/B testing framework

---

## Support

For technical support or questions:
- 📧 Email: dev-support@yourcompany.com
- 📚 Documentation: [Internal Wiki]
- 🚨 Issues: [GitHub Issues]
- 💬 Slack: #api-support

---

*This documentation is maintained by the Shopping Bot development team. Last updated: December 30, 2025*
