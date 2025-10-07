# Personal Care Path - Root Cause Analysis & Optimization Plan

**Date**: 2025-10-07  
**Purpose**: Deep analysis of personal care code path vs food path for 2025 optimization

---

## **Executive Summary**

Personal care uses a **parallel but inferior** architecture compared to the optimized food path:
- ❌ Old prompt style (verbose, no 2025 best practices)
- ❌ No anchor noun refinement
- ❌ No mandatory keyword extraction
- ❌ No health-first suggestions
- ❌ Separate tool schemas for initial vs follow-up (complexity)
- ✅ Has domain-specific fields (skin_types, efficacy_terms, avoid_terms)

**Recommendation**: Create `_generate_skin_es_params_2025()` following same pattern as food.

---

## **1. Code Branching Analysis**

### **Divergence Points**

| Component | Food Path | Personal Care Path | Branch Location |
|-----------|-----------|-------------------|-----------------|
| **LLM Param Extraction** | `generate_unified_es_params_2025()` | `generate_skin_es_params()` | `data_fetchers/es_products.py:1456-1485` |
| **Tool Schema** | `UNIFIED_ES_PARAMS_TOOL` (single) | `INITIAL_SKIN_PARAMS_TOOL` + `FOLLOWUP_SKIN_PARAMS_TOOL` (dual) | `llm_service.py:496-583 vs 101-166` |
| **ES Query Builder** | `_build_enhanced_es_query()` | `_build_skin_es_query()` | `data_fetchers/es_products.py:1123-1127` |
| **Scoring** | `function_score` with category-specific | `function_score` with review-based | Lines 479-542 vs 654-677 |
| **Redis Persistence** | `debug.last_search_params` | `debug.last_skin_search_params` | Lines 1514 vs 1482 |

### **Shared Components**
- Redis context manager
- Conversation history building
- Follow-up classification
- Slot storage (`store_user_answer`)
- Assessment flow

---

## **2. Parameter Extraction Comparison**

### **Food (2025 Optimized)**

```python
# llm_service.py:3010-3217
async def _generate_unified_es_params_2025(ctx, current_text):
    # Schema: UNIFIED_ES_PARAMS_TOOL
    Output Fields:
    - anchor_product_noun ✅ (required)
    - category_group ✅ (enum)
    - category_paths ✅ (max 3)
    - dietary_terms ✅ (enum, max 5)
    - price_min/max ✅
    - brands ✅ (max 5)
    - keywords ✅ (max 4, MANDATORY)
    - must_keywords ✅ (max 3)
    - size ✅
    - reasoning ✅ (1 sentence)
    
    Prompt Style:
    ✅ 2025 best practices
    ✅ Progressive disclosure
    ✅ Contrastive examples
    ✅ Explicit decision trees
    ✅ 7 priority rules
    ✅ Category-specific catalogs
    ✅ Generic anchor refinement
    ✅ Health-first suggestions
    ✅ Keyword extraction mandates
```

### **Personal Care (Legacy)**

```python
# llm_service.py:4214-4398
async def generate_skin_es_params(ctx):
    # Dual schemas: INITIAL_SKIN_PARAMS_TOOL vs FOLLOWUP_SKIN_PARAMS_TOOL
    Output Fields:
    - q ✅
    - anchor_product_noun ✅
    - category_group ✅ (fixed: "personal_care")
    - subcategory ❓ (not in food)
    - category_paths ✅
    - skin_types ✅ (domain-specific)
    - hair_types ✅ (domain-specific)
    - efficacy_terms ✅ (domain-specific, ~keywords for food)
    - avoid_terms ✅ (domain-specific, negatives)
    - avoid_ingredients ✅
    - product_types ❓ (serum, cream, oil)
    - brands ✅
    - price_min/max ✅
    - keywords ❓ (exists but unclear usage)
    - phrase_boosts ❓
    - prioritize_concerns ❓ (boolean)
    - min_review_count ❓
    - size ✅
    - reasoning ❌ (NOT present)
    
    Prompt Style:
    ❌ Old style (verbose, no structure)
    ❌ No progressive disclosure
    ❌ No contrastive examples
    ❌ Vague guidance ("use judgment, not rigid rules")
    ❌ No decision trees
    ❌ No priority rules
    ❌ No category catalogs
```

---

## **3. Elasticsearch Query Building**

### **Food (_build_enhanced_es_query)**

```javascript
{
  "query": {
    "function_score": {
      "query": {
        "bool": {
          "must": [
            {"multi_match": {"query": q_text, "fuzziness": "AUTO"}},
            // Hard filters for must_keywords with fuzzy
          ],
          "should": [
            {"multi_match": {"query": "dietary_term", "fuzziness": "AUTO"}},  // Fuzzy dietary
            {"multi_match": {"query": keyword, "fuzziness": "AUTO"}},  // Soft keywords
          ],
          "filter": [
            {"terms": {"category_paths": [paths]}},
            {"range": {"price": {min, max}}},
          ]
        }
      },
      "functions": [
        // Category-specific scoring (protein, fiber, wholefood bonuses)
        // Penalty avoidance (sugar, sodium, trans fat)
      ]
    }
  }
}
```

**Key Features:**
- Fuzzy matching on MUST and SHOULD ✅
- Category-specific quality scoring ✅
- Dietary labels as SHOULD (boost) ✅
- must_keywords for flavor filtering ✅
- keywords for soft ranking ✅

### **Personal Care (_build_skin_es_query)**

```javascript
{
  "query": {
    "function_score": {
      "query": {
        "bool": {
          "must": [
            {"multi_match": {"query": q_text, "fuzziness": "AUTO"}},
          ],
          "should": [
            // skin_compatibility nested (boost 5.0)
            {"nested": {"path": "skin_compatibility", "query": {"term": {"skin_type": type}}}},
            // efficacy nested (boost 2.0-3.0)
            {"nested": {"path": "efficacy", "query": {"terms": {"aspect_name": efficacy_terms}}}},
          ],
          "must_not": [
            // avoid_ingredients and avoid_terms
            {"nested": {"path": "side_effects", ...}},
            {"terms": {"cons_list": avoid_terms}},
          ],
          "filter": [
            {"terms": {"category_paths": [paths]}},
            {"range": {"price": {min, max}}},
          ]
        }
      },
      "functions": [
        // Review-based scoring (avg_rating, total_reviews)
        // NO quality scoring (no flean equivalent)
      ]
    }
  }
}
```

**Key Features:**
- Nested queries for skin_compatibility ✅
- Nested queries for efficacy (like keywords) ✅
- Nested queries for side_effects (avoidance) ✅
- Review-based ranking ✅
- ❌ NO fuzzy matching on efficacy_terms
- ❌ NO categorical quality scoring
- ❌ NO dietary_terms support
- ❌ NO must_keywords/keywords distinction

---

## **4. Parameter Field Mapping**

| Food Field | Personal Care Equivalent | Purpose | Status |
|------------|-------------------------|---------|--------|
| `anchor_product_noun` | `anchor_product_noun` | Product noun | ✅ Present (but not refined) |
| `category_paths` | `category_paths` | Taxonomy navigation | ✅ Present |
| `dietary_terms` | ❌ N/A | Health filters | ❌ Not used (could add for vegan/organic) |
| `keywords` | `efficacy_terms` | Soft ranking boost | ⚠️ Different semantics |
| `must_keywords` | ❌ N/A | Hard flavor filters | ❌ Not used (could add for variants) |
| `brands` | `brands` | Brand filtering | ✅ Present |
| `price_min/max` | `price_min/max` | Price range | ✅ Present |
| `reasoning` | ❌ N/A | Explain LLM decision | ❌ Missing |
| ❌ N/A | `skin_types` | Skin suitability | ✅ Domain-specific |
| ❌ N/A | `hair_types` | Hair suitability | ✅ Domain-specific |
| ❌ N/A | `skin_concerns` | User concerns | ✅ Domain-specific |
| ❌ N/A | `hair_concerns` | User concerns | ✅ Domain-specific |
| ❌ N/A | `avoid_terms` | Negative signals | ✅ Domain-specific |
| ❌ N/A | `avoid_ingredients` | Ingredient exclusions | ✅ Domain-specific |
| ❌ N/A | `product_types` | serum/cream/oil/gel | ✅ Domain-specific |
| ❌ N/A | `prioritize_concerns` | Boolean flag | ✅ Domain-specific |

---

## **5. Prompting Style Comparison**

### **Food (2025 Style)**

```
✅ Structured XML-style tags
✅ Progressive disclosure (most important first)
✅ 7 explicit priority rules
✅ Category-specific extraction catalogs
✅ 6-step decision flowcharts
✅ Contrastive examples (❌ vs ✅)
✅ Mandatory field generation
✅ Reasoning field
```

### **Personal Care (Legacy Style)**

```
❌ Plain text instructions
❌ No structured rules
❌ "Use judgment, not rigid rules" (too vague)
❌ No category catalogs
❌ No decision flowcharts
❌ No contrastive examples
❌ Optional fields (many empty)
❌ No reasoning field
```

**Prompt Excerpt** (Current):
```
"Deliberate silently step-by-step to extract robust parameters. 
Do not output your reasoning; OUTPUT ONLY a tool call.
Task: Emit normalized ES params for personal_care (skin or hair). 
Keep q as product noun (no price/concern words).
Extraction guidance (use judgment, not rigid rules)..."
```

---

## **6. Critical Gaps in Personal Care**

### **Gap 1: No Anchor Refinement**
- **Issue**: Generic anchors like "skin care items" not refined to "face serum"
- **Impact**: Poor ES relevance
- **Food Solution**: CATEGORY_PATH_TO_NOUNS mapping + Rule Priority 5

### **Gap 2: No Mandatory Keyword Extraction**
- **Issue**: `efficacy_terms` often empty; no distinction between hard/soft signals
- **Impact**: Weak ranking signals
- **Food Solution**: Keywords + must_keywords with category catalogs

### **Gap 3: No Health-First Suggestions**
- **Issue**: Doesn't proactively suggest clean ingredients, paraben-free, sulfate-free
- **Impact**: Misses product vision (healthier alternatives)
- **Food Solution**: Priority 6 rule with category-specific health priorities

### **Gap 4: Dual Tool Schemas**
- **Issue**: INITIAL vs FOLLOWUP tools add complexity
- **Impact**: Harder to maintain, test, prompt engineer
- **Food Solution**: Single unified tool with is_follow_up context

### **Gap 5: No Fuzzy Matching on Nested Queries**
- **Issue**: Exact match on efficacy_terms and skin_types
- **Impact**: "anti-aging" won't match "anti-ageing", "antiaging"
- **Food Solution**: Fuzzy on all SHOULD/MUST clauses

### **Gap 6: No Redis Persistence of Domain Fields**
- **Issue**: `skin_concerns`, `hair_types` not promoted to session-level
- **Impact**: Lost context across turns
- **Food Solution**: Promote all extracted fields to session

---

## **7. ES Query Field Usage**

### **Food Fields in ES**
| Param | ES Clause | Type | Fuzziness | Boost/Weight |
|-------|-----------|------|-----------|--------------|
| `q` | MUST | multi_match | AUTO | name^4 |
| `dietary_terms` | SHOULD | multi_match | AUTO | 3.0x |
| `keywords` | SHOULD | multi_match | AUTO | name^4 |
| `must_keywords` | MUST | multi_match | AUTO | name^6 |
| `category_paths` | FILTER | terms/wildcard | N/A | N/A |
| `brands` | FILTER | terms/wildcard | N/A | N/A |
| `price_min/max` | FILTER | range | N/A | N/A |

### **Personal Care Fields in ES**
| Param | ES Clause | Type | Fuzziness | Boost/Weight |
|-------|-----------|------|-----------|--------------|
| `q` | MUST | multi_match | AUTO | name^4 |
| `skin_types` | SHOULD | nested (exact term) | ❌ NO | 5.0x |
| `efficacy_terms` | SHOULD | nested (exact terms) | ❌ NO | 2.0-3.0x |
| `avoid_terms` | MUST_NOT | nested (exact match) | ❌ NO | Exclusion |
| `avoid_ingredients` | MUST_NOT | nested (exact match) | ❌ NO | Exclusion |
| `category_paths` | FILTER | terms | N/A | N/A |
| `brands` | FILTER | terms/wildcard | N/A | N/A |
| `price_min/max` | FILTER | range | N/A | N/A |

**Key Difference**: Personal care uses nested queries with exact matching; food uses flat multi_match with fuzzy.

---

## **8. Moving Parts - Personal Care Pipeline**

### **Entry Point**
```
User: "dry scalp shampoo"
  ↓
routes/chat.py (domain detection)
  ↓
bot_core.process_query()
  ↓
domain == "personal_care"
  ↓
data_fetchers/build_search_params() detects personal_care
  ↓
llm_service.generate_skin_es_params()
```

### **LLM Extraction Flow**
```
1. Read session (current_text, history, profile_hints, candidate_subcats)
2. Classify follow-up (via classify_follow_up LLM)
3. Build conversation history (last 5-10 turns)
4. Select tool: INITIAL vs FOLLOWUP
5. Call LLM with tool-choice
6. Extract params from tool output
7. Normalize lists (skin_types, efficacy_terms, etc.)
8. Persist to debug.last_skin_search_params
9. Return params
```

### **ES Query Building**
```
1. Router detects category_group == "personal_care"
2. Calls _build_skin_es_query(params)
3. Builds query:
   - FILTER: category_paths, price, brands
   - MUST: q (product noun)
   - SHOULD: skin_compatibility nested, efficacy nested
   - MUST_NOT: side_effects nested, cons_list
4. Function score: review-based (avg_rating, total_reviews)
5. Execute ES search
6. Transform results
```

---

## **9. What Gets Extracted**

### **Core Fields** (like food)
- `q`: Product noun ("shampoo", "face serum")
- `anchor_product_noun`: Same as q (redundant currently)
- `category_paths`: ["personal_care/hair/shampoo"]
- `brands`: ["Dove", "L'Oreal"]
- `price_min/max`: INR range
- `size`: 10 max (personal care default)

### **Domain-Specific Fields** (unique to personal care)

**Skin/Hair Type Filters:**
- `skin_types`: ["oily", "dry", "combination", "sensitive", "normal"]
- `hair_types`: ["dry", "oily", "curly", "straight", "wavy"]
- **ES Usage**: Nested query on `skin_compatibility.skin_type` with sentiment≥0.6
- **Purpose**: Match products suitable for user's skin/hair type

**Positive Signals (Like keywords):**
- `efficacy_terms`: ["anti-dandruff", "hydration", "brightening", "anti-aging"]
- `skin_concerns`: ["acne", "pigmentation", "dryness", "dullness"]
- `hair_concerns`: ["dandruff", "hair fall", "frizz", "split ends"]
- **ES Usage**: Nested query on `efficacy.aspect_name` with sentiment≥0.7
- **Purpose**: Boost products with proven efficacy for concerns

**Negative Signals (Like must_not):**
- `avoid_terms`: ["fragrance", "sulfates", "parabens", "harsh"]
- `avoid_ingredients`: ["alcohol", "SLS", "mineral oil"]
- **ES Usage**: Nested query on `side_effects.effect_name` with severity≥0.3 (exclusion)
- **Purpose**: Filter out products with unwanted ingredients/effects

**Product Form:**
- `product_types`: ["serum", "cream", "lotion", "oil", "gel", "foam", "mask"]
- **ES Usage**: ❓ Not clearly used in query builder
- **Purpose**: Filter by product form factor

---

## **10. Query Building Logic**

### **Food Logic**
```
ES Query Structure:
  MUST:
    - q (main search term)
    - must_keywords (flavor filters)
  
  SHOULD (ranking boosts):
    - dietary_terms (3.0x boost)
    - keywords (name^4 boost)
    - phrase_boosts (custom boost)
  
  FILTER:
    - category_paths (exact)
    - price range
    - brands
    
  SCORING:
    - Category-specific function_score
    - Protein/fiber/wholefood bonuses
    - Sugar/sodium/trans-fat penalties
```

### **Personal Care Logic**
```
ES Query Structure:
  MUST:
    - q (main search term)
  
  SHOULD (ranking boosts):
    - skin_compatibility nested (5.0x boost)
      → skin_type matches with sentiment≥0.6
    - efficacy nested (2.0-3.0x boost)
      → efficacy_terms matches with sentiment≥0.7
  
  MUST_NOT (exclusions):
    - side_effects nested
      → avoid_terms with severity≥0.3
    - cons_list terms
      → avoid_terms keyword exclusion
    - avoid_ingredients nested
  
  FILTER:
    - category_paths (exact terms)
    - price range
    - brands
    - min_review_count (optional)
    
  SCORING:
    - Review-based function_score
    - avg_rating (1.2x with sqrt modifier)
    - total_reviews (log1p)
```

---

## **11. Slot Values Persistence**

### **Food**
```python
# Lines 3173-3213 in llm_service.py
Promotes to session:
- category_group
- category_paths (+ category_path = first)
- brands
- dietary_requirements (MERGE logic)
- price_min/max
- size_hint

Persists to debug:
- unified_es_params (full snapshot)
- last_search_params (curated)
- last_params_updated_at
```

### **Personal Care**
```python
# Line 1482 in data_fetchers/es_products.py
Persists to debug ONLY:
- last_skin_search_params (full snapshot)

❌ NO session-level promotion
❌ NO merge logic
❌ skin_types/concerns not preserved across turns
```

**Issue**: User says "oily skin" → stored in params → NOT promoted to session → next turn LLM doesn't see it!

---

## **12. Redis State Comparison**

### **Food Session State**
```json
{
  "session": {
    "category_group": "f_and_b",
    "category_paths": ["f_and_b/food/light_bites/chips_and_crisps"],
    "dietary_requirements": ["LOW SODIUM", "VEGAN"],  // Merged
    "brands": ["Lays"],
    "price_max": 100,
    "debug": {
      "unified_es_params": {...},
      "last_search_params": {...},
      "last_params_updated_at": "2025-10-07T21:00:00Z"
    }
  }
}
```

### **Personal Care Session State**
```json
{
  "session": {
    "domain": "personal_care",
    "candidate_subcategories": ["shampoo", "conditioner"],
    "domain_subcategory": "shampoo",
    // ❌ NO skin_types promoted
    // ❌ NO skin_concerns promoted
    // ❌ NO efficacy_terms promoted
    "debug": {
      "last_skin_search_params": {...}  // Isolated, not reused
    }
  }
}
```

---

## **13. Critical Issues Identified**

### **Issue 1: No 2025 Prompt Engineering**
- **Severity**: HIGH
- **Impact**: Inconsistent extraction, empty fields, vague guidance
- **Solution**: Rewrite prompt with priority rules, catalogs, contrastive examples

### **Issue 2: No Anchor Refinement**
- **Severity**: MEDIUM
- **Impact**: Generic queries like "skin care items" don't resolve to "face serum"
- **Solution**: Add path→noun mapping like food

### **Issue 3: No Fuzzy Matching on Domain Fields**
- **Severity**: MEDIUM
- **Impact**: "anti-aging" won't match "anti-ageing", "antiaging"
- **Solution**: Change nested exact match to fuzzy multi_match

### **Issue 4: No Session-Level Persistence**
- **Severity**: HIGH
- **Impact**: User's skin type lost across turns
- **Solution**: Promote skin_types, concerns, efficacy to session like food

### **Issue 5: Dual Tool Schemas**
- **Severity**: LOW
- **Impact**: Maintenance burden, schema drift risk
- **Solution**: Merge into single tool with is_follow_up context

### **Issue 6: No Health-First Suggestions**
- **Severity**: MEDIUM
- **Impact**: Doesn't suggest paraben-free, sulfate-free by default
- **Solution**: Add proactive clean-ingredient suggestions

### **Issue 7: No Keyword Distinction**
- **Severity**: MEDIUM
- **Impact**: No hard vs soft filtering for variants (e.g., "rose water toner" variant)
- **Solution**: Add must_keywords for product variants

---

## **14. Proposed 2025 Optimization Plan**

### **Phase 1: Create Unified Personal Care Tool** (Priority: HIGH)
```python
PERSONAL_CARE_ES_PARAMS_TOOL_2025 = {
    "name": "generate_personal_care_es_params",
    "required": [
        "anchor_product_noun", 
        "category_group",
        "category_paths",
        "skin_types",  // Can be empty
        "efficacy_terms",  // MANDATORY (at least 1)
        "avoid_terms",  // Can be empty
        "keywords",  // MANDATORY (at least 1)
        "must_keywords"  // Can be empty
    ]
}
```

### **Phase 2: Add Priority Rules** (Like Food)
1. **Anchor Composition** (product noun extraction)
2. **Field Separation** (concerns vs ingredients vs attributes)
3. **Category Mapping** (skin/hair/oral/body)
4. **Skin Type Detection** (oily/dry/combination/sensitive)
5. **Generic Anchor Refinement** (items → serum)
6. **Health-First Clean Ingredients** (paraben-free, sulfate-free default)
7. **Keyword Extraction** (efficacy_terms hard, keywords soft)

### **Phase 3: Add Category Catalogs**
```
Shampoo:
  - efficacy_terms: anti-dandruff, volumizing, strengthening, moisturizing
  - avoid_terms: sulfates, parabens, silicones, harsh
  - keywords: gentle, nourishing, hydrating
  - must_keywords: dry-scalp (if mentioned)

Face Serum:
  - efficacy_terms: brightening, anti-aging, hydration, firming
  - avoid_terms: fragrance, alcohol, comedogenic
  - keywords: lightweight, fast-absorbing, non-greasy

Moisturizer:
  - efficacy_terms: hydration, barrier-repair, soothing, nourishing
  - avoid_terms: heavy, greasy, sticky, pore-clogging
  - keywords: lightweight, non-comedogenic, SPF
```

### **Phase 4: Add Session Persistence**
```python
# Promote to session (like food):
if params.get("skin_types"):
    ctx.session["skin_types"] = params.get("skin_types")
if params.get("skin_concerns"):
    ctx.session["skin_concerns"] = params.get("skin_concerns")
if params.get("efficacy_terms"):
    ctx.session["efficacy_terms"] = params.get("efficacy_terms")
```

### **Phase 5: Add Fuzzy Matching**
```python
# Change from nested exact to fuzzy multi_match
# For efficacy_terms matching
shoulds.append({
    "multi_match": {
        "query": efficacy_term,
        "fields": ["efficacy.aspect_name^3.0"],
        "fuzziness": "AUTO"
    }
})
```

---

## **15. Comparative Example**

### **Food Query: "chips"**

**LLM2 Output**:
```python
{
  "anchor_product_noun": "chips",
  "category_paths": ["f_and_b/food/light_bites/chips_and_crisps"],
  "dietary_terms": ["LOW SODIUM"],  // Health-first
  "keywords": ["crunchy"],  // Mandatory inference
  "must_keywords": [],
  "reasoning": "New chips query; suggesting LOW SODIUM + inferred 'crunchy' keyword"
}
```

### **Personal Care Query: "shampoo"**

**Current LLM Output**:
```python
{
  "q": "shampoo",
  "anchor_product_noun": "shampoo",
  "category_paths": ["personal_care/hair/shampoo"],
  "skin_types": [],  // Often empty
  "efficacy_terms": [],  // Often empty ❌
  "avoid_terms": [],  // Often empty ❌
  "keywords": [],  // Often empty ❌
  // No reasoning field
}
```

**Desired 2025 Output**:
```python
{
  "anchor_product_noun": "shampoo",
  "category_paths": ["personal_care/hair/shampoo"],
  "skin_types": [],  // OK to be empty (not specified)
  "efficacy_terms": ["gentle cleansing", "moisturizing"],  // Inferred ✅
  "avoid_terms": ["sulfates", "parabens"],  // Health-first ✅
  "keywords": ["nourishing"],  // Mandatory ✅
  "must_keywords": [],
  "reasoning": "Shampoo query; suggesting sulfate-free/paraben-free + gentle efficacy"
}
```

---

## **16. Implementation Roadmap**

### **Step 1: Understand Current State** ✅ (This RCA)
- Map all moving parts
- Compare food vs personal care
- Identify gaps

### **Step 2: Design Unified Schema**
- Merge INITIAL + FOLLOWUP tools
- Add required fields (keywords, must_keywords, reasoning)
- Keep domain-specific fields (skin_types, efficacy_terms, avoid_terms)

### **Step 3: Write 2025 Prompt**
- 7 priority rules
- Category-specific catalogs (shampoo, serum, moisturizer, etc.)
- Contrastive examples
- Decision flowcharts

### **Step 4: Add Session Persistence**
- Promote skin_types, concerns, efficacy_terms
- Implement merge logic (like dietary)
- Track user-provided vs AI-suggested

### **Step 5: Add Fuzzy Matching**
- Change nested exact match to fuzzy
- Apply to efficacy_terms, avoid_terms, skin_types

### **Step 6: Test & Validate**
- Cross-product pollution tests
- Follow-up merge tests
- Fuzzy matching tests

---

## **Next Steps**

**Should I proceed with:**
1. ✅ Creating `PERSONAL_CARE_ES_PARAMS_TOOL_2025` (unified schema)?
2. ✅ Writing `_generate_personal_care_es_params_2025()` (new method)?
3. ✅ Building `_build_personal_care_optimized_prompt()` (2025 style)?
4. ✅ Adding session persistence for domain fields?
5. ✅ Adding fuzzy matching to nested queries?

**Estimated Complexity**: Similar to food path (5-6 file edits, ~500 lines of prompts/logic)

---

**RCA Complete. Awaiting your approval to proceed with implementation.**

