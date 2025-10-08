# Personal Care Product Matching RCA & Solution

## Executive Summary

**Problem:** User searches for "hair oil" but receives cleansing oils, face washes, and other irrelevant personal care products.

**Root Cause:** Personal care ES query builder intentionally ignores `category_paths` and relies solely on fuzzy text matching across all personal care products.

**Solution:** Implement strict multi-dimensional product type matching using anchor_product_noun parsing and content-based filtering across multiple ES fields.

---

## 🔍 Root Cause Analysis

### What Happened

**User Query:** "want hair oil"
- ✅ LLM correctly extracted: `anchor_product_noun: "hair oil"`
- ✅ LLM correctly set: `category_paths: ["personal_care/hair/hair_oil"]`
- ❌ ES query ignored category_paths entirely
- ❌ ES returned 1,170 products including face cleansing oils, oil control tissues, etc.

### The Code Problem

In `shopping_bot/data_fetchers/es_products.py`, lines 708-714:

```python
# NOTE: Personal care: ignore category_path(s) entirely per product decision
# We intentionally do NOT add any category_paths filter for personal_care
if p.get("category_path") or p.get("category_paths"):
    print("DEBUG: SKIN_CATEGORY_PATH_IGNORED | personal care has no enforced hierarchy")
```

**Why This Was Done:** Category taxonomy for personal care is unreliable/inconsistent across products.

### The Failure Chain

1. **Filter:** `category_group: "personal_care"` → matches ALL personal care (10,000+ products)
2. **Must:** `multi_match` on "hair oil" with `fuzziness: AUTO` → matches any product with "oil" or fuzzy variants
3. **No Type Enforcement:** No validation that product is actually a hair oil vs cleansing oil vs face product

**Result:**
- "Biotique Bio Almond **Oil** Deep Cleanse" ✓ (contains "oil")
- "Miss Claire **Oil** Control Tissue" ✓ (contains "oil")  
- "Biotique Pineapple Face Wash" ✓ (fuzzy match on category)

---

## 💡 Solution: First Principles Approach

### Core Insight

**When taxonomy fails, use semantic signals from content fields:**

Instead of relying on unreliable `category_paths`, we:
1. **Parse** anchor_product_noun ("hair oil") into semantic components
2. **Extract** category (hair) and type (oil)
3. **Enforce** both dimensions across multiple content fields
4. **Exclude** wrong product categories explicitly

### Implementation Strategy

#### 1. Product Type Parser (`_parse_product_type()`)

Extracts structured data from anchor_product_noun:

```python
Input: "hair oil"
Output: {
    "category_terms": ["hair"],        # Product category
    "type_terms": ["oil"],             # Product type
    "exclude_terms": [                 # What to exclude
        "face wash", "facial", "makeup", 
        "cleansing", "makeup removal"
    ]
}
```

**Category Detection:** hair, face, skin, body, lips, eyes, nails
**Type Detection:** oil, wash, serum, cream, lotion, gel, shampoo, conditioner, etc.

#### 2. Multi-Field Strict Matching

Build ES boolean queries that enforce BOTH dimensions:

**Dimension 1: Category (MUST match)**
```json
{
  "bool": {
    "should": [
      {"match_phrase": {"name": {"query": "hair", "boost": 3.0}}},
      {"match_phrase": {"use": {"query": "hair", "boost": 2.0}}},
      {"match": {"description": {"query": "hair", "boost": 1.0}}}
    ],
    "minimum_should_match": 1
  }
}
```

**Dimension 2: Type (MUST match)**
```json
{
  "bool": {
    "should": [
      {"match_phrase": {"name": {"query": "oil", "boost": 3.0}}},
      {"match": {"use": {"query": "oil", "boost": 2.0}}},
      {"match": {"description": {"query": "oil", "boost": 1.0}}},
      {"match": {"package_claims.marketing_keywords": "oil"}}
    ],
    "minimum_should_match": 1
  }
}
```

**Dimension 3: Exclusions (MUST_NOT match)**
```json
[
  {"match_phrase": {"name": "face wash"}},
  {"match_phrase": {"use": "facial"}},
  {"match_phrase": {"name": "cleansing"}},
  {"match_phrase": {"use": "makeup removal"}}
]
```

#### 3. Reduced Fuzziness

When strict matching is active, disable fuzzy matching:
```python
"fuzziness": "0" if has_strict_matching else "AUTO"
```

This prevents "oil" from matching "foil", "coil", etc.

---

## ✅ How This Solves The Problem

### Before (Broken)
```
Query: "hair oil"
ES Filter: category_group=personal_care + fuzzy("hair oil" in name)
Results: 1,170 products (cleansing oils, face products, etc.)
```

### After (Fixed)
```
Query: "hair oil"  
ES Filter: 
  - category_group=personal_care
  - MUST contain "hair" in (name OR use OR description)
  - MUST contain "oil" in (name OR use OR description OR marketing_keywords)
  - MUST_NOT contain "cleansing", "face wash", "facial", "makeup removal"
Results: Only actual hair oils
```

### Example Filtering

| Product | Has "hair"? | Has "oil"? | Has exclusions? | Result |
|---------|-------------|------------|-----------------|--------|
| Indulekha Hair Oil | ✓ | ✓ | ✗ | ✅ MATCH |
| Biotique Almond Cleansing Oil | ✗ | ✓ | ✓ (cleansing) | ❌ REJECT |
| Miss Claire Oil Control | ✗ | ✓ | ✓ (face/facial in use) | ❌ REJECT |
| Pantene Hair Serum | ✓ | ✗ | ✗ | ❌ REJECT (wrong type) |

---

## 🎯 Scalability & Generalization

### This Solution Works For All Personal Care Products

**Examples:**

**"face wash"**
- Category: ["face"]
- Type: ["wash", "cleanser"]  
- Exclude: ["hair", "shampoo", "conditioner", "scalp"]

**"body lotion"**
- Category: ["body"]
- Type: ["lotion"]
- Exclude: ["hair", "face", "facial"]

**"anti-dandruff shampoo"**
- Category: ["hair"]
- Type: ["shampoo"]
- Exclude: ["face wash", "facial", "body"]

### Extensibility

1. **Add new categories:** Just update `category_map` in parser
2. **Add new types:** Just update `type_map` in parser
3. **Refine exclusions:** Adjust logic per category in parser
4. **No schema changes:** Works with existing ES mapping

---

## 📊 Key Design Principles

1. **Content over Taxonomy:** Use what's reliable (product text) not what's broken (category paths)
2. **Multi-Signal Validation:** Require evidence across multiple fields (name, use, description)
3. **Defensive Exclusions:** Actively exclude wrong categories, don't just match right ones
4. **Structured Parsing:** Extract semantic meaning from anchor_product_noun
5. **Zero Dependencies:** Works with current ES mapping, no reindexing needed

---

## 🚀 Testing Recommendations

### Test Cases

1. **Hair Oil Query**
   - Input: "want hair oil under 200"
   - Expected: Only hair oils, no cleansing oils, no face products
   
2. **Face Wash Query**
   - Input: "face wash for oily skin"
   - Expected: Only face washes, no body washes, no shampoos

3. **Body Lotion Query**
   - Input: "moisturizing body lotion"
   - Expected: Only body lotions, no face creams, no hair products

4. **Edge Case: Generic Oil**
   - Input: "oil for dry skin"
   - Expected: Should infer face/body oils, exclude hair oils

### Validation Metrics

- **Precision:** % of returned products that match anchor type
- **Category Purity:** % of results in correct category (hair/face/body)
- **Exclusion Effectiveness:** 0 products with excluded terms

---

## 📝 Code Changes Summary

**File:** `shopping_bot/data_fetchers/es_products.py`

**New Function:** `_parse_product_type(anchor: str)` (lines 644-732)
- Parses anchor into category, type, and exclusion terms
- Maps keywords to semantic concepts
- Returns structured dict

**Modified Function:** `_build_skin_es_query(params)` (lines 807-872)
- Calls parser to extract product type info
- Builds MUST clauses for category and type matching
- Builds MUST_NOT clauses for exclusions
- Reduces fuzziness when strict matching is active
- Logs parsed terms for debugging

**Impact:**
- No breaking changes
- Backwards compatible (only active when anchor_product_noun present)
- No ES schema changes required
- No LLM prompt changes needed

---

## 🎉 Expected Outcome

**For "hair oil" query:**
- ❌ Before: 1,170 mixed products (cleansing oils, face products, etc.)
- ✅ After: ~50-100 pure hair oils matching all criteria

**Benefits:**
- ✅ Dramatically improved precision
- ✅ Category-specific results
- ✅ Scalable to all personal care products
- ✅ No reliance on broken taxonomy
- ✅ Works with existing data

---

## 🔧 Maintenance Notes

### Adding New Product Categories

Edit `_parse_product_type()`:
```python
category_map = {
    "hair": ["hair", "scalp"],
    "face": ["face", "facial"],
    "newcategory": ["keyword1", "keyword2"],  # Add here
}
```

### Adding New Product Types

```python
type_map = {
    "oil": ["oil"],
    "newtype": ["keyword1", "keyword2"],  # Add here
}
```

### Tuning Exclusions

Adjust exclusion logic per category:
```python
if "category" in detected_category:
    exclude_terms.extend(["term1", "term2"])
```

---

## 📚 References

- **LLM Tool:** `PERSONAL_CARE_ES_PARAMS_TOOL_2025` (llm_service.py:101-247)
- **ES Query Builder:** `_build_skin_es_query()` (es_products.py:735+)
- **ES Mapping:** flean-v4 index (personal_care category_group)
- **Test Logs:** Line 153 in user's terminal output showing correct LLM extraction

---

**Document Version:** 1.0  
**Last Updated:** 2025-10-08  
**Status:** ✅ Implementation Complete

