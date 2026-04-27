# Elasticsearch Index Migration: v4 → products_master

## ✅ Migration Complete

All Elasticsearch index references have been updated from `flean-v4` to `products_master`.

---

## 📝 Files Updated

### 1. **shopping_bot/data_fetchers/es_products.py**
```python
# Before
ELASTIC_INDEX = os.getenv("ELASTIC_INDEX", "flean-v4")

# After
ELASTIC_INDEX = os.getenv("ELASTIC_INDEX", "products_master")
```

### 2. **shopping_bot/config.py**
```python
# Before
ELASTIC_INDEX: str = os.getenv("ELASTIC_INDEX", "flean-v4")

# After
ELASTIC_INDEX: str = os.getenv("ELASTIC_INDEX", "products_master")
```

### 3. **ecs-task-definition.json**
```json
// Before
{
  "name": "ELASTIC_INDEX",
  "value": "flean-v4"
}

// After
{
  "name": "ELASTIC_INDEX",
  "value": "products_master"
}
```

### 4. **testest.py** (Test file)
```python
# Before
ELASTIC_INDEX = "flean-v4"

# After
ELASTIC_INDEX = "products_master"
```

---

## 🔍 Verification

✅ No remaining `flean-v4` references in code  
✅ All 4 files updated successfully  
✅ Default index now `products_master`  
ℹ️ One reference in `PERSONAL_CARE_STRICT_MATCHING_RCA.md` (documentation - left as historical reference)

---

## 🚀 Deployment Notes

### Environment Variables
If you're using environment variables, ensure `ELASTIC_INDEX` is set correctly:

```bash
# .env or environment
ELASTIC_INDEX=products_master
```

### Docker/ECS
The `ecs-task-definition.json` has been updated with the new index name. When you deploy, the container will automatically use `products_master`.

### Local Development
No additional changes needed. The code now defaults to `products_master` if no environment variable is set.

---

## ✅ Testing Checklist

Before deploying to production:

- [ ] Verify `products_master` index exists in Elasticsearch
- [ ] Confirm index mapping matches expected schema (with `nutri_breakdown_updated`)
- [ ] Test a simple product search query
- [ ] Test macro filtering queries (if applicable)
- [ ] Monitor ES query logs for any errors

---

## 📊 Index Schema Verification

The `products_master` index should have the new nutritional structure:

```json
{
  "category_data": {
    "nutritional": {
      "nutri_breakdown_updated": {
        "protein g": float,
        "sodium mg": float,
        "saturated fat g": float,
        // ... other nutrients
      },
      "qty": string,
      "raw_text": string
    }
  }
}
```

This enables the new **macro-aware product search** functionality.

---

## 🔄 Rollback Plan

If you need to rollback to v4:

```bash
# Set environment variable
export ELASTIC_INDEX=flean-v4

# Or update the files back to v4
# (keep this file as reference)
```

---

**Migration Date**: January 10, 2025  
**Status**: ✅ COMPLETE  
**Impact**: All product searches will now use `products_master` index

