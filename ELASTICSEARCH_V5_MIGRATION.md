# Elasticsearch Index Migration: v4 → v5

## ✅ Migration Complete

All Elasticsearch index references have been updated from `flean-v4` to `flean-v5`.

---

## 📝 Files Updated

### 1. **shopping_bot/data_fetchers/es_products.py**
```python
# Before
ELASTIC_INDEX = os.getenv("ELASTIC_INDEX", "flean-v4")

# After
ELASTIC_INDEX = os.getenv("ELASTIC_INDEX", "flean-v5")
```

### 2. **shopping_bot/config.py**
```python
# Before
ELASTIC_INDEX: str = os.getenv("ELASTIC_INDEX", "flean-v4")

# After
ELASTIC_INDEX: str = os.getenv("ELASTIC_INDEX", "flean-v5")
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
  "value": "flean-v5"
}
```

### 4. **testest.py** (Test file)
```python
# Before
ELASTIC_INDEX = "flean-v4"

# After
ELASTIC_INDEX = "flean-v5"
```

---

## 🔍 Verification

✅ No remaining `flean-v4` references in code  
✅ All 4 files updated successfully  
✅ Default index now `flean-v5`  
ℹ️ One reference in `PERSONAL_CARE_STRICT_MATCHING_RCA.md` (documentation - left as historical reference)

---

## 🚀 Deployment Notes

### Environment Variables
If you're using environment variables, ensure `ELASTIC_INDEX` is set correctly:

```bash
# .env or environment
ELASTIC_INDEX=flean-v5
```

### Docker/ECS
The `ecs-task-definition.json` has been updated with the new index name. When you deploy, the container will automatically use `flean-v5`.

### Local Development
No additional changes needed. The code now defaults to `flean-v5` if no environment variable is set.

---

## ✅ Testing Checklist

Before deploying to production:

- [ ] Verify `flean-v5` index exists in Elasticsearch
- [ ] Confirm index mapping matches expected schema (with `nutri_breakdown_updated`)
- [ ] Test a simple product search query
- [ ] Test macro filtering queries (if applicable)
- [ ] Monitor ES query logs for any errors

---

## 📊 Index Schema Verification

The `flean-v5` index should have the new nutritional structure:

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
**Impact**: All product searches will now use `flean-v5` index

