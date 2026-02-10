# Home Page API - cURL Commands for Postman Testing

## Base URL
```
http://localhost:8080
```

---

## 1. Banners/Ads API

### Get All Banners
```bash
curl -X GET "http://localhost:8080/api/v1/home/banners" \
  -H "Content-Type: application/json"
```

**Expected Response:**
- Returns all active promotional banners with main_heading, sub_heading, button_text, image_url, etc.

---

## 2. Categories API

### Get 4 Categories (Default)
```bash
curl -X GET "http://localhost:8080/api/v1/home/categories" \
  -H "Content-Type: application/json"
```

### Get All Categories
```bash
curl -X GET "http://localhost:8080/api/v1/home/categories?all=true" \
  -H "Content-Type: application/json"
```

**Expected Response:**
- Default: Returns 4 categories with `has_more: true`
- With `?all=true`: Returns all 8 categories with `has_more: false`

---

## 3. Best Selling Products API

### Get 4 Random Products (Default)
```bash
curl -X GET "http://localhost:8080/api/v1/home/best-selling" \
  -H "Content-Type: application/json"
```

### Get Custom Count (1-10)
```bash
curl -X GET "http://localhost:8080/api/v1/home/best-selling?count=6" \
  -H "Content-Type: application/json"
```

**Expected Response:**
- Returns 4 random products from pool of 10 (or custom count)
- Each request may return different products due to randomization

---

## 4. Curated Products API

### Get 4 Random Products (Default)
```bash
curl -X GET "http://localhost:8080/api/v1/home/curated" \
  -H "Content-Type: application/json"
```

### Get Custom Count (1-25)
```bash
curl -X GET "http://localhost:8080/api/v1/home/curated?count=8" \
  -H "Content-Type: application/json"
```

**Expected Response:**
- Returns 4 random products from pool of 25 (or custom count)
- Each request may return different products due to randomization

---

## 5. Why Flean API

### Get Value Proposition Cards
```bash
curl -X GET "http://localhost:8080/api/v1/home/why-flean" \
  -H "Content-Type: application/json"
```

**Expected Response:**
- Returns 4 cards with main_heading, text_body, icon_url, and display_order

---

## 6. Collaborations API

### Get Brand Partnerships
```bash
curl -X GET "http://localhost:8080/api/v1/home/collaborations" \
  -H "Content-Type: application/json"
```

**Expected Response:**
- Returns 4-5 brand collaboration objects with brand_name, logo_url, and description

---

## Bonus Endpoints

### Health Check
```bash
curl -X GET "http://localhost:8080/api/v1/home/health" \
  -H "Content-Type: application/json"
```

**Expected Response:**
- Returns status of all JSON data files and overall health status

### Reload Cache (Force Data Reload)
```bash
curl -X POST "http://localhost:8080/api/v1/home/reload" \
  -H "Content-Type: application/json"
```

**Expected Response:**
- Clears the cache so data will be reloaded from JSON files on next request
- Useful after updating JSON files without restarting the server

---

## Postman Collection Format

If you want to import these into Postman, here's a quick reference:

1. **Create a new Collection**: "Flean Home Page APIs"
2. **Base URL Variable**: `{{base_url}}` = `http://localhost:8080`
3. **Add requests** for each endpoint above

### Example Postman Request Setup:
- **Method**: GET
- **URL**: `{{base_url}}/api/v1/home/banners`
- **Headers**: 
  - `Content-Type: application/json`

---

## Testing Tips

1. **Randomization**: Best Selling and Curated endpoints return different products on each call. Test multiple times to verify randomization works.

2. **Categories**: Test both default (4 items) and `?all=true` (all items) to verify pagination logic.

3. **Error Handling**: Try invalid parameters like `?count=100` to see error handling (should cap at max allowed).

4. **Cache**: After updating JSON files, use the `/reload` endpoint to clear cache without restarting the server.

5. **Health Check**: Use health endpoint to verify all data files are loading correctly.

---

## Production URLs

For production, replace `localhost:8080` with your production domain:
```bash
# Example
curl -X GET "https://api.flean.com/api/v1/home/banners" \
  -H "Content-Type: application/json"
```

