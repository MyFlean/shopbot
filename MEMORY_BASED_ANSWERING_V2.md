# Memory-Based Answering V2: Rich XML Product Storage

## Problem Statement

User query: `"thanks ,can you do a nutri breakdown of winsgreens"`

**Expected**: Use memory path (product was just recommended)
**Actual**: Classified as `data_strategy=es_fetch`, triggering a fresh ES query

## Root Cause Analysis

### Issue 1: Missing Product Context in LLM Classifier
The `classify_and_assess` LLM had no access to product names from `last_recommendation`, only truncated bot replies (120 chars max). It couldn't match "winsgreens" to "Wingreens Farms Tomato Ketchup".

### Issue 2: Deterministic Heuristics (Removed per user request)
Previous implementation used keyword matching ("those", "these", "above"). User explicitly requested **pure LLM-driven detection** with no cheap tricks.

### Issue 3: Sparse Product Storage
`last_recommendation` only stored basic fields:
```python
{
    "title": "...",
    "brand": "...",
    "price": 123,
    "rating": 4.5,
    "image_url": "..."
}
```

No nutritional data → LLM couldn't answer "nutri breakdown" queries from memory.

---

## Solution Implemented

### 1. Rich Product Storage (bot_core.py)

**Location**: `shopping_bot/bot_core.py` → `_store_last_recommendation()`

**Change**: Extract and store complete nutritional data from ES v5 mapping:

```python
for product in (products or [])[:8]:
    # Extract nutritional data from nutri_breakdown_updated
    nutri_breakdown = {}
    nutri_raw = product.get("category_data", {}).get("nutritional", {}).get("nutri_breakdown_updated", {})
    if isinstance(nutri_raw, dict):
        for nutrient_key, value in nutri_raw.items():
            if value is not None:
                nutri_breakdown[nutrient_key] = value
    
    products_snapshot.append({
        "id": product.get("id", ""),
        "name": product.get("name") or product.get("title", "Unknown Product"),
        "brand": product.get("brand", ""),
        "price": product.get("price"),
        "mrp": product.get("mrp"),
        # ... other fields ...
        # ✨ NEW: Rich nutritional data
        "nutritional_breakdown": nutri_breakdown,
        "nutritional_qty": product.get("category_data", {}).get("nutritional", {}).get("qty", ""),
        "bonus_percentiles": product.get("bonus_percentiles", {}),
        "penalty_percentiles": product.get("penalty_percentiles", {}),
    })
```

**Stored nutrients** (example for ketchup):
- `protein g`, `total fat g`, `saturated fat g`, `trans fat g`
- `total sugar g`, `added sugar g`
- `sodium mg`, `energy kcal`
- All other nutrients from `nutri_breakdown_updated`

---

### 2. LLM Context Enhancement (llm_service.py)

**Location**: `shopping_bot/llm_service.py` → `classify_and_assess()`

**Change**: Add `last_recommended_products` to context summary:

```python
context_summary = {
    "has_history": False,
    "last_intent": None,
    "last_category": None,
    "last_slots": {},
    "recent_turns": [],
    "last_recommended_products": []  # ✨ NEW
}

# Extract product names/brands from last_recommendation
last_rec = ctx.session.get("last_recommendation", {}) or {}
if isinstance(last_rec, dict) and last_rec.get("products"):
    products = last_rec.get("products", [])
    for p in products[:8]:
        if isinstance(p, dict):
            name = p.get("name", "")
            brand = p.get("brand", "")
            if name or brand:
                context_summary["last_recommended_products"].append({
                    "name": str(name)[:80] if name else "",
                    "brand": str(brand)[:40] if brand else ""
                })
```

**LLM now sees**:
```json
{
  "last_recommended_products": [
    {"name": "Wingreens Farms Tomato Ketchup", "brand": "Wingreens Farms"},
    {"name": "Tops Tomato Ketchup", "brand": "Tops"},
    ...
  ]
}
```

---

### 3. Updated Prompt Rules (No Deterministic Heuristics)

**Location**: `shopping_bot/llm_service.py` → `classify_and_assess()` prompt

**Old** (deterministic):
```
**Detection heuristics:**
- If query contains: "those", "these", "above" → likely "memory_only"
- If query is fresh → "es_fetch"
```

**New** (LLM-driven):
```
**data_strategy = "memory_only"**
- User asks about previously recommended products (check last_recommended_products in context)
- User wants details, breakdown, nutrition info, comparison of products already shown
- User mentions a product name or brand that appears in the context's last_recommended_products list
- Examples: 
  * "nutri breakdown of [product name]" - if product name matches any in last_recommended_products
  * "tell me about [brand]" - if brand matches any in last_recommended_products

**IMPORTANT**: Use your reasoning to determine if the user is asking about products from 
context/memory vs. requesting a fresh search. Look at last_recommended_products list and 
recent conversation turns to make this determination.
```

**No keyword matching**. Pure LLM reasoning.

---

### 4. XML-Formatted Memory for LLM Answer Generation

**Location**: `shopping_bot/llm_service.py` → `generate_memory_based_answer()`

**Change**: Format products as rich XML with complete nutritional data:

```xml
<product position="1">
  <id>01K1B1BQRXGW276WVE1H8HQEM6</id>
  <name>Wingreens Farms Tomato Ketchup</name>
  <brand>Wingreens Farms</brand>
  <price>65</price>
  <mrp>75</mrp>
  <rating>4.2</rating>
  <flean_score>58.5</flean_score>
  <flean_percentile value="78" note="Higher is better (top percentile in category)" />
  <description>Tangy tomato ketchup with no preservatives...</description>
  <serving_size>100g</serving_size>
  <nutritional_breakdown>
    <nutrient name="energy kcal" value="110" />
    <nutrient name="protein g" value="1.2" />
    <nutrient name="total fat g" value="0.3" />
    <nutrient name="saturated fat g" value="0" />
    <nutrient name="trans fat g" value="0" />
    <nutrient name="total sugar g" value="24.5" />
    <nutrient name="added sugar g" value="7.2" />
    <nutrient name="sodium mg" value="350" />
  </nutritional_breakdown>
  <percentile_scores>
    <bonus nutrient="protein" percentile="45" />
    <penalty nutrient="sugar" percentile="82" />
    <penalty nutrient="sodium" percentile="65" />
  </percentile_scores>
</product>
```

**LLM Prompt Updated**:
```
**For nutritional queries** (breakdown, macros, nutrition):
- Extract all relevant nutrients from the <nutritional_breakdown> section
- Present them clearly (e.g., "Protein: 5g, Carbs: 20g, Fat: 2g, Sodium: 350mg")
- Mention serving_size for context
- Use flean_percentile to explain overall quality
```

---

## Expected Flow (After Fix)

### Query: `"want ketchup with 0 sat fat"`
1. ✅ LLM classifies as `data_strategy=es_fetch` (new search)
2. ✅ ES query with macro filter: `saturated fat g lte 0`
3. ✅ Returns 5 products
4. ✅ **Rich storage**: Each product stored with complete `nutri_breakdown_updated` data
5. ✅ User sees MPM with top 3 ketchups

### Query: `"thanks, can you do a nutri breakdown of winsgreens"`
1. ✅ LLM sees `last_recommended_products` includes "Wingreens Farms Tomato Ketchup"
2. ✅ LLM classifies as `data_strategy=memory_only` (no keyword matching, pure reasoning)
3. ✅ `generate_memory_based_answer()` called
4. ✅ Formats products as XML with full nutritional data
5. ✅ LLM extracts nutrients from XML and answers:
   ```
   Wingreens Farms Tomato Ketchup (100g serving):
   - Energy: 110 kcal
   - Protein: 1.2g
   - Total Fat: 0.3g
   - Saturated Fat: 0g ✅
   - Trans Fat: 0g
   - Total Sugar: 24.5g
   - Added Sugar: 7.2g
   - Sodium: 350mg
   
   This ketchup ranks in the top 78th percentile for quality in its category!
   ```

---

## Key Design Principles

1. **No Deterministic Heuristics**: LLM decides memory vs. ES using reasoning, not keywords
2. **Rich Data Storage**: Store everything the LLM might need (nutrition, scores, percentiles)
3. **LLM-Friendly Format**: XML structure for easy parsing and extraction
4. **Modular & Extensible**: Easy to add more fields (ingredients, claims, etc.) later

---

## Testing Checklist

- [ ] Query "want ketchup" → ES fetch, rich storage
- [ ] Query "nutri breakdown of wingreens" → memory path (LLM detects product name)
- [ ] Query "compare first two" → memory path (positional reference)
- [ ] Query "tell me about tops" → memory path (brand match)
- [ ] Query "want low sodium chips" → ES fetch (new search, not memory)
- [ ] Verify XML preview log: `🧠 XML_MEMORY_PREVIEW | length=X chars`
- [ ] Verify nutritional data stored: Check Redis `last_recommendation.products[0].nutritional_breakdown`

---

## Files Modified

1. **`shopping_bot/bot_core.py`** (lines 1112-1147)
   - Enhanced `_store_last_recommendation()` to extract and store `nutri_breakdown_updated`

2. **`shopping_bot/llm_service.py`** (lines 1873-1919, 1962-1975, 2337-2440)
   - Added `last_recommended_products` to classifier context
   - Removed deterministic heuristics from prompt
   - Built rich XML product format for memory answering
   - Updated prompt with nutritional query guidance

---

## Next Steps

1. **Deploy & Test** on local, then AWS
2. **Monitor logs**:
   - `🔀 CLASSIFY_RESULT | data_strategy=memory_only` for product references
   - `🧠 XML_MEMORY_PREVIEW | products_formatted=X` for memory answers
3. **Iterate** on XML structure if LLM needs different formatting
4. **Add ingredients** (if user requests ingredient breakdowns)

---

**Status**: ✅ Implemented, ready for testing
**User approval**: Required before deploy to main/master

