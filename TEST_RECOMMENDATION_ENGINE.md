# Recommendation Engine Test Cases

This document provides test cases and curl commands to test all 3 LLMs in the recommendation engine.

## The 3 LLMs

1. **LLM1: Classification & Assessment** (`classify_and_assess`)
   - Classifies user queries into routes (product/support/general)
   - Determines product domain (f_and_b/personal_care)
   - Identifies product intent (is_this_good/which_is_better/show_me_alternate/show_me_options)
   - Generates contextual questions (ask_slots)

2. **LLM2: ES Parameter Extraction** (`extract_search_params` / `plan_es_search`)
   - Extracts search query (`q`)
   - Identifies category paths from taxonomy
   - Extracts filters (dietary_terms, brands, price_min/price_max)
   - Handles follow-up queries with delta logic
   - Extracts nutritional constraints (macro_filters)

3. **LLM3: Final Response Generation** (`generate_final_answer_unified`)
   - Generates summary message with product recommendations
   - Creates UX components (DPL, quick_replies, product_ids)
   - Formats response for SPM (Single Product Mode) or MPM (Multiple Product Mode)
   - Includes product descriptions with nutritional info

---

## Prerequisites

```bash
# Set your server URL
export BASE_URL="http://localhost:5000"  # or your server URL
export USER_ID="test_user_$(date +%s)"
export SESSION_ID="$USER_ID"
```

---

## Test Group 1: LLM1 - Classification & Assessment

### Test 1.1: Food Product Query
Tests if LLM1 correctly classifies a simple food product query as `route: "product"`, `domain: "f_and_b"`

```bash
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "I want chips"
  }'
```

**Expected**: `route: "product"`, `domain: "f_and_b"`, `product_intent: "show_me_options"`

---

### Test 1.2: Personal Care Query
Tests if LLM1 correctly classifies personal care queries with `domain: "personal_care"`

```bash
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "show me shampoo"
  }'
```

**Expected**: `route: "product"`, `domain: "personal_care"`, 4 ask_slots for personal care

---

### Test 1.3: Support Query
Tests routing to support

```bash
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "where is my order"
  }'
```

**Expected**: `route: "support"`, `simple_response.response_type: "support_routing"`

---

### Test 1.4: General Query
Tests general routing

```bash
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "hello"
  }'
```

**Expected**: `route: "general"`, `simple_response.response_type: "friendly_chat"` or `"bot_identity"`

---

### Test 1.5: Out of Category Query
Tests handling of out-of-category products

```bash
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "I need a laptop"
  }'
```

**Expected**: `route: "general"`, `simple_response.response_type: "out_of_category"`

---

### Test 1.6: Product Intent - "is_this_good"
Tests single product evaluation intent

```bash
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "is Veeba ketchup good?"
  }'
```

**Expected**: `product_intent: "is_this_good"`, `route: "product"`

---

### Test 1.7: Product Intent - "which_is_better"
Tests product comparison intent

```bash
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "which is better, Lays or Kurkure chips?"
  }'
```

**Expected**: `product_intent: "which_is_better"`

---

### Test 1.8: Product Intent - "show_me_alternate"
Tests alternative product request

```bash
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "show me alternatives to this shampoo"
  }'
```

**Expected**: `product_intent: "show_me_alternate"`

---

### Test 1.9: Product Intent - "show_me_options"
Tests product exploration intent

```bash
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "what are my options for protein bars?"
  }'
```

**Expected**: `product_intent: "show_me_options"`, should have ask_slots

---

## Test Group 2: LLM2 - ES Parameter Extraction

### Test 2.1: Basic Category Extraction
Tests if LLM2 extracts category_path and category_group

```bash
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "show me organic chips"
  }'
```

**Expected**: Response should include ES search with `category_path: "f_and_b/food/light_bites/chips_and_crisps"`, `q: "organic chips"`

---

### Test 2.2: Dietary Requirements Extraction
Tests extraction of dietary terms

```bash
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "gluten free noodles without palm oil"
  }'
```

**Expected**: ES params should include `dietary_terms: ["GLUTEN FREE", "PALM OIL FREE"]`

---

### Test 2.3: Price Range Extraction
Tests budget/price extraction

```bash
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "chips under 100 rupees"
  }'
```

**Expected**: `price_max: 100` in ES params

---

### Test 2.4: Brand Filter Extraction
Tests brand extraction

```bash
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "show me Lays chips"
  }'
```

**Expected**: `brands: ["Lays"]` in ES params

---

### Test 2.5: Complex Multi-Filter Query
Tests extraction of multiple filters together

```bash
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "vegan protein bars under 200 rupees from Quest"
  }'
```

**Expected**: ES params should have `dietary_terms: ["VEGAN"]`, `price_max: 200`, `brands: ["Quest"]`

---

### Test 2.6: Personal Care with Compatibility
Tests personal care specific parameter extraction

```bash
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "shampoo for oily hair with dandruff"
  }'
```

**Expected**: Should extract `hair_types`, `efficacy_terms: ["anti-dandruff"]`, `category_group: "personal_care"`

---

### Test 2.7: Follow-up Constraint (Delta Logic)
Tests if LLM2 maintains anchor and applies delta

```bash
# First, send initial query
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "I want chips"
  }'

# Then follow-up
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "make it organic"
  }'
```

**Expected**: Second request should maintain `q: "chips"` and add `dietary_terms: ["ORGANIC"]`

---

### Test 2.8: Generic Follow-up (Category Carry-over)
Tests category carry-over for generic follow-ups

```bash
# Initial query
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "show me breakfast cereals"
  }'

# Generic follow-up
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "cheaper options"
  }'
```

**Expected**: Should carry over `category_path` from first query and apply `price_max`

---

### Test 2.9: Nutritional Constraints (Macro Filters)
Tests extraction of macro nutritional filters

```bash
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "protein bars with more than 20g protein and less than 200mg sodium"
  }'
```

**Expected**: Should extract `macro_filters` with `nutrient_name: "protein g"`, `operator: "gt"`, `value: 20` and similar for sodium

---

## Test Group 3: LLM3 - Final Response Generation

### Test 3.1: MPM Response (Multiple Products)
Tests if LLM3 generates proper MPM response with hero product

```bash
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "show me options for healthy snacks"
  }'
```

**Expected**: Response should include:
- `response_type: "final_answer"`
- `summary_message` with 3 parts
- `product_ids` array (multiple products)
- `hero_product_id` (first product)
- `ux.ux_surface: "MPM"`
- `ux.dpl_runtime_text`
- `ux.quick_replies` (3-4 items)

---

### Test 3.2: SPM Response (Single Product)
Tests single product focused response

```bash
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "is this Himalayan chips product good?"
  }'
```

**Expected**: 
- `ux.ux_surface: "SPM"`
- `product_ids` with single or limited products
- Focused summary message

---

### Test 3.3: Product Recommendations with UX
Tests complete UX generation

```bash
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "recommend some breakfast cereals"
  }'
```

**Expected**: Full response with products, summary, DPL, and quick replies

---

### Test 3.4: Quick Replies Generation
Tests quick reply generation

```bash
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "what chocolate options do you have?"
  }'
```

**Expected**: `ux.quick_replies` array with 3-4 actionable options

---

### Test 3.5: Enriched Product Details
Tests if response includes nutritional/enriched details

```bash
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "show me high protein snacks"
  }'
```

**Expected**: Summary should mention protein content, flean scores, nutritional info

---

## Test Group 4: End-to-End Flow (All 3 LLMs)

### Test 4.1: Complete Flow - Initial Query

```bash
# Step 1: Initial query (triggers all 3 LLMs)
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "I want chips"
  }' | jq '.'
```

**Flow**: 
1. LLM1 classifies as `route: "product"`, `domain: "f_and_b"`, generates ask_slots
2. LLM2 extracts ES params (`q: "chips"`, `category_path`)
3. LLM3 generates final response with products

---

### Test 4.2: Follow-up with Slot Answer

```bash
# Step 2: Answer slot question
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "under 100"
  }' | jq '.'
```

**Flow**:
1. LLM1 detects follow-up
2. LLM2 applies delta (adds `price_max: 100`)
3. LLM3 regenerates response with filtered products

---

### Test 4.3: Further Refinement

```bash
# Step 3: Add another constraint
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "gluten free only"
  }' | jq '.'
```

**Flow**: All 3 LLMs work together to refine the search

---

### Test 4.4: Personal Care Complete Flow

```bash
# Personal care initial query
NEW_USER_ID="test_user_pc_$(date +%s)"
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$NEW_USER_ID'",
    "session_id": "'$NEW_USER_ID'",
    "message": "face cream for dry skin"
  }' | jq '.'

# Follow-up
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$NEW_USER_ID'",
    "session_id": "'$NEW_USER_ID'",
    "message": "under 500 rupees"
  }' | jq '.'
```

---

## Test Group 5: Edge Cases

### Test 5.1: Empty Message

```bash
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": ""
  }'
```

**Expected**: 400 error

---

### Test 5.2: Special Characters (Unicode)

```bash
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "मुझे नमकीन चाहिए"
  }'
```

**Expected**: Should handle unicode gracefully

---

### Test 5.3: Ambiguous Query

```bash
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "'$USER_ID'",
    "session_id": "'$SESSION_ID'",
    "message": "something healthy"
  }'
```

**Expected**: Should ask clarifying questions

---

## Running the Full Test Suite

You can run the complete test suite using the bash script:

```bash
# Make script executable
chmod +x test_recommendation_engine.sh

# Run all tests
export BASE_URL="http://localhost:5000"
bash test_recommendation_engine.sh
```

---

## Verification Checklist

After running tests, verify:

### LLM1 (Classification)
- [ ] Correctly routes to product/support/general
- [ ] Identifies domain (f_and_b/personal_care)
- [ ] Classifies product intent (is_this_good/which_is_better/show_me_alternate/show_me_options)
- [ ] Generates appropriate ask_slots (2 for food, 4 for personal care)

### LLM2 (ES Extraction)
- [ ] Extracts search query (`q`)
- [ ] Identifies category_path from taxonomy
- [ ] Extracts dietary_terms (uppercase)
- [ ] Extracts price_min/price_max
- [ ] Extracts brands
- [ ] Handles follow-ups with delta logic
- [ ] Carries over category for generic follow-ups
- [ ] Extracts macro_filters for nutritional constraints

### LLM3 (Response Generation)
- [ ] Generates summary_message with 3 parts
- [ ] Includes product_ids array
- [ ] Selects hero_product_id for MPM
- [ ] Generates UX components (DPL, quick_replies)
- [ ] Correctly sets ux_surface (SPM/MPM)
- [ ] Includes nutritional/enriched details

### End-to-End
- [ ] All 3 LLMs work together seamlessly
- [ ] Follow-up queries maintain context
- [ ] Response quality is good

---

## Debugging

To see what's happening behind the scenes, check the server logs for:

- `CLASSIFY_AND_ASSESS_LLM` - LLM1 calls
- `ES_PARAMS_EXTRACTION` - LLM2 calls  
- `GENERATE_RESPONSE` - LLM3 calls
- `UNIFIED_ES_PARAMS` - ES parameter extraction
- `FINAL_ANSWER` - Response generation

You can also check the response metadata for debug information:

```bash
curl -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "test_user",
    "message": "chips"
  }' | jq '.metadata'  # or '.debug'
```
