# 🚀 Macro Framework Quick Start Guide

## What You Have Now

✅ **Complete Framework Design** - First principles analysis, architecture, rationale
✅ **Category Profiles JSON** - 60+ categories with nutritional optimization rules  
✅ **MacroOptimizer Module** - Production-ready Python implementation  
✅ **Comprehensive Documentation** - Implementation guide, examples, test cases

## What You Need to Do

### Step 1: Update LLM Tool Schema (15 min)

**File**: `shopping_bot/llm_service.py`

**Location**: Add to `UNIFIED_ES_PARAMS_TOOL` schema (~line 826)

```python
# Add this field to the "properties" dict:
"macro_filters": {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "nutrient_name": {
                "type": "string",
                "description": (
                    "Standardized nutrient field name from nutri_breakdown_updated. "
                    "Examples: 'protein g', 'sodium mg', 'saturated fat g', 'caffeine mg', "
                    "'added sugar g', 'fiber g', 'calcium mg', 'vitamin d mcg'"
                )
            },
            "operator": {
                "type": "string",
                "enum": ["gte", "lte", "gt", "lt"],
                "description": "Comparison: gte (>=), lte (<=), gt (>), lt (<)"
            },
            "value": {
                "type": "number",
                "description": "Numeric threshold in nutrient's standard unit"
            },
            "priority": {
                "type": "string",
                "enum": ["hard", "soft"],
                "default": "hard",
                "description": "hard=must match (filter), soft=should prefer (boost)"
            }
        },
        "required": ["nutrient_name", "operator", "value"]
    },
    "maxItems": 5,
    "description": (
        "Nutritional constraints extracted from query. "
        "Examples:\n"
        "- 'protein bars with >20g protein' → [{nutrient_name: 'protein g', operator: 'gt', value: 20}]\n"
        "- 'low sodium chips <200mg' → [{nutrient_name: 'sodium mg', operator: 'lt', value: 200}]"
    )
}

# Also add to "required" array:
"required": ["anchor_product_noun", "category_group", "category_paths", 
             "dietary_terms", "price_min", "price_max", "brands",
             "keywords", "must_keywords", "size", "macro_filters"]  # ADD THIS
```

---

### Step 2: Add LLM Extraction Examples (20 min)

**File**: `shopping_bot/llm_service.py`

**Location**: Add after line ~823 (before `UNIFIED_ES_PARAMS_TOOL`)

```python
MACRO_EXTRACTION_EXAMPLES = """
<macro_extraction_examples>
<example>
User: "Show me protein bars with more than 20g protein"
macro_filters: [{"nutrient_name": "protein g", "operator": "gt", "value": 20, "priority": "hard"}]
</example>

<example>
User: "I want chips with less than 200mg sodium"
macro_filters: [{"nutrient_name": "sodium mg", "operator": "lt", "value": 200, "priority": "hard"}]
</example>

<example>
User: "High protein, low sugar snacks - at least 15g protein and under 5g sugar"
macro_filters: [
  {"nutrient_name": "protein g", "operator": "gte", "value": 15, "priority": "hard"},
  {"nutrient_name": "total sugar g", "operator": "lt", "value": 5, "priority": "hard"}
]
</example>

<example>
User: "Energy drinks with 80-150mg caffeine"
macro_filters: [
  {"nutrient_name": "caffeine mg", "operator": "gte", "value": 80, "priority": "hard"},
  {"nutrient_name": "caffeine mg", "operator": "lte", "value": 150, "priority": "hard"}
]
</example>

<example>
User: "Low calorie ice cream under 150 calories"
macro_filters: [{"nutrient_name": "energy kcal", "operator": "lt", "value": 150, "priority": "hard"}]
</example>

<example>
User: "I have high blood pressure, need low sodium snacks"
reasoning: Health condition implies sodium restriction
macro_filters: [{"nutrient_name": "sodium mg", "operator": "lte", "value": 140, "priority": "hard"}]
</example>

<example>
User: "Diabetes-friendly breakfast cereals"
reasoning: Diabetes implies low added sugar
macro_filters: [{"nutrient_name": "added sugar g", "operator": "lte", "value": 5, "priority": "hard"}]
</example>

<example>
User: "Show me chips"
reasoning: No explicit macro mentioned
macro_filters: []
note: Category defaults will auto-apply (low sodium, low sat fat)
</example>

<extraction_rules>
1. Extract EXPLICIT macro mentions with numbers → hard priority
2. Infer from health conditions (diabetes→low sugar, hypertension→low sodium) → hard priority
3. Infer from use cases (post-workout→high protein, weight loss→low calorie) → soft priority
4. Standardize nutrient names to match nutri_breakdown_updated fields
5. Convert units: "milligrams" → "mg", "grams" → "g", "calories" → "kcal"
6. If user says nothing about macros, return empty array (category defaults auto-apply)
</extraction_rules>
</macro_extraction_examples>
"""

# Then inject into LLM prompt in _generate_unified_es_params_2025() around line 3745:
prompt = self._build_optimized_prompt(...) + (
    "\n<fnb_taxonomy>\n" +
    json.dumps(fnb_taxonomy, ...) +
    "\n</fnb_taxonomy>\n\n" +
    TAXONOMY_CATEGORIZATION_EXAMPLES +
    "\n" + MACRO_EXTRACTION_EXAMPLES +  # ADD THIS LINE
    "\n<taxonomy_rule priority=\"CRITICAL\">..."
)
```

---

### Step 3: Modify ES Query Builder (30 min)

**File**: `shopping_bot/data_fetchers/es_products.py`

**Location**: In `_build_enhanced_es_query()` function, after the existing filter logic (~line 492)

```python
def _build_enhanced_es_query(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build ES query with improved brand handling and percentile-based ranking.
    NOW SUPPORTS: Macro-based filtering and ranking
    """
    p = params or {}
    
    # ... [ALL EXISTING CODE UNTIL LINE ~492] ...
    
    # ═══════════════════════════════════════════════════════════════
    # NEW: MACRO FILTERING SECTION
    # ═══════════════════════════════════════════════════════════════
    
    # Import macro optimizer
    from ..macro_optimizer import get_macro_optimizer
    optimizer = get_macro_optimizer()
    
    # Get user-specified macro_filters from params
    user_macro_filters = p.get("macro_filters", [])
    
    # Get first category_path for profile lookup
    category_path = None
    try:
        category_paths = p.get("category_paths", [])
        if isinstance(category_paths, list) and category_paths:
            category_path = category_paths[0]
        elif isinstance(p.get("category_path"), str):
            category_path = p.get("category_path")
    except Exception:
        pass
    
    # Merge user constraints with category defaults
    merged_constraints = optimizer.merge_constraints(
        user_constraints=user_macro_filters,
        category_path=category_path
    )
    
    hard_filters_list = merged_constraints.get("hard_filters", [])
    soft_boosts_list = merged_constraints.get("soft_boosts", [])
    
    # Apply hard filters as ES range queries
    for hf in hard_filters_list:
        nutrient = hf.get("nutrient")
        operator = hf.get("operator")
        value = hf.get("value")
        
        if not (nutrient and operator and value is not None):
            continue
        
        field_path = f"category_data.nutritional.nutri_breakdown_updated.{nutrient}"
        
        # Build range query based on operator
        range_query = {"range": {field_path: {}}}
        
        if operator == "gte":
            range_query["range"][field_path]["gte"] = value
        elif operator == "lte":
            range_query["range"][field_path]["lte"] = value
        elif operator == "gt":
            range_query["range"][field_path]["gt"] = value
        elif operator == "lt":
            range_query["range"][field_path]["lt"] = value
        else:
            continue  # Unknown operator
        
        filters.append(range_query)
        
        try:
            print(f"DEBUG: MACRO_HARD_FILTER | {nutrient} {operator} {value} (source: {hf.get('source')})")
        except Exception:
            pass
    
    # Store soft_boosts for function_score integration
    macro_scoring_functions = []
    
    for sb in soft_boosts_list:
        nutrient = sb.get("nutrient")
        operator = sb.get("operator")
        value = sb.get("value")
        weight = sb.get("weight", 1.2)
        
        if not (nutrient and operator and value is not None):
            continue
        
        field_path = f"category_data.nutritional.nutri_breakdown_updated.{nutrient}"
        
        # Build function_score boost
        macro_scoring_functions.append({
            "filter": {
                "range": {
                    field_path: {
                        operator: value
                    }
                }
            },
            "weight": weight
        })
        
        try:
            print(f"DEBUG: MACRO_SOFT_BOOST | {nutrient} {operator} {value} weight={weight} (source: {sb.get('source')})")
        except Exception:
            pass
    
    # ═══════════════════════════════════════════════════════════════
    # END MACRO FILTERING SECTION
    # ═══════════════════════════════════════════════════════════════
    
    # ... [CONTINUE WITH EXISTING CODE] ...
    
    # IMPORTANT: When building function_score (~line 551), merge macro functions:
    if shoulds or filters:
        # ... existing brand filter code ...
        
        # Get category-specific scoring functions (existing)
        scoring_functions = build_function_score_functions(subcategory, include_flean=True)
        
        # ADD: Merge macro-based scoring functions
        scoring_functions.extend(macro_scoring_functions)
        
        body["query"] = {
            "function_score": {
                "query": {"bool": bq},
                "functions": scoring_functions,
                "score_mode": "multiply",
                "boost_mode": "multiply"
            }
        }
        
        try:
            print(f"DEBUG: MACRO_SCORING | Added {len(macro_scoring_functions)} macro-based scoring functions")
        except Exception:
            pass
    
    # ... [REST OF EXISTING CODE] ...
```

---

### Step 4: Update ES Source Fields (5 min)

**File**: `shopping_bot/data_fetchers/es_products.py`

**Location**: In `_build_enhanced_es_query()`, update `_source.includes` (~line 163)

```python
"_source": {
    "includes": [
        "id", "name", "brand", "price", "mrp", "hero_image.*",
        "package_claims.*", "category_group", "category_paths", 
        "description", "use", "flean_score.*",
        "stats.adjusted_score_percentiles.*",
        "stats.wholefood_percentiles.*",
        "stats.protein_percentiles.*",
        "stats.fiber_percentiles.*",
        "stats.fortification_percentiles.*",
        "stats.simplicity_percentiles.*",
        "stats.sugar_penalty_percentiles.*",
        "stats.sodium_penalty_percentiles.*",
        "stats.trans_fat_penalty_percentiles.*",
        "stats.saturated_fat_penalty_percentiles.*",
        "stats.oil_penalty_percentiles.*",
        "stats.sweetener_penalty_percentiles.*",
        "stats.calories_penalty_percentiles.*",
        "stats.empty_food_penalty_percentiles.*",
        
        # NEW: Include nutritional data
        "category_data.nutritional.nutri_breakdown_updated.*",
        "category_data.nutritional.qty",
    ]
}
```

---

## Testing Your Implementation

### Quick Smoke Test

```python
# Test 1: MacroOptimizer standalone
from shopping_bot.macro_optimizer import get_macro_optimizer

optimizer = get_macro_optimizer()

# Test merge with user constraint
result = optimizer.merge_constraints(
    user_constraints=[{"nutrient_name": "protein g", "operator": "gt", "value": 20}],
    category_path="f_and_b/food/light_bites/chips_and_crisps"
)

print("Hard filters:", result["hard_filters"])
print("Soft boosts:", result["soft_boosts"])
# Expected: 1 hard filter (protein), multiple soft boosts (sodium, sat fat, etc.)

# Test 2: Category-only (no user input)
result2 = optimizer.merge_constraints(
    user_constraints=[],
    category_path="f_and_b/food/light_bites/chips_and_crisps"
)
print("Soft boosts (category only):", result2["soft_boosts"])
# Expected: Multiple soft boosts from category profile
```

### Integration Test Queries

```bash
# Test explicit macro query
curl -X POST http://localhost:5000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "wa_id": "123", "message": "protein bars with more than 20g protein"}'

# Test implicit category optimization
curl -X POST http://localhost:5000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "wa_id": "123", "message": "show me chips"}'

# Test health condition inference
curl -X POST http://localhost:5000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "wa_id": "123", "message": "I have diabetes, need breakfast cereals"}'
```

### Verify ES Queries

Check debug logs for:
```
DEBUG: MACRO_HARD_FILTER | protein g gt 20 (source: user)
DEBUG: MACRO_SOFT_BOOST | added sugar g lte 8.0 weight=0.8 (source: category_default)
DEBUG: MACRO_SCORING | Added 2 macro-based scoring functions
```

---

## Troubleshooting

### Issue: MacroOptimizer not found
**Solution**: Ensure `shopping_bot/macro_optimizer.py` is in the right location and `__init__.py` exists

### Issue: Category profiles not loading
**Solution**: Check `shopping_bot/taxonomies/category_macro_profiles.json` exists and is valid JSON

### Issue: No macro filters extracted by LLM
**Solution**: Verify `MACRO_EXTRACTION_EXAMPLES` is injected into LLM prompt

### Issue: ES query syntax error
**Solution**: Check field name matches: `category_data.nutritional.nutri_breakdown_updated.{nutrient}`

---

## Expected Behavior

### Query: "protein bars with more than 20g protein"

**LLM extracts**:
```json
{"macro_filters": [{"nutrient_name": "protein g", "operator": "gt", "value": 20}]}
```

**ES query includes**:
```json
{"range": {"category_data.nutritional.nutri_breakdown_updated.protein g": {"gt": 20}}}
```

**Result**: Only products with protein > 20g

---

### Query: "show me chips"

**LLM extracts**:
```json
{"macro_filters": []}
```

**MacroOptimizer adds** (soft boosts):
- Sodium ≤ 300mg → weight 0.8
- Sat fat ≤ 3g → weight 0.85
- Protein ≥ 5g → weight 1.3

**Result**: All chips, healthier ones ranked higher

---

## Performance Expectations

- **Query latency**: +5-10ms for macro optimization logic (negligible)
- **ES query complexity**: +1-5 function_score clauses per query
- **Result set size**: May reduce by 20-50% for hard filters (expected)
- **Ranking quality**: Significant improvement in health-relevance

---

## Rollout Strategy

### Phase 1: Silent Launch (Week 1)
- Deploy code
- Enable logging
- Monitor ES queries
- Don't announce feature

### Phase 2: Soft Launch (Week 2)
- A/B test: 10% traffic gets macro optimization
- Compare CTR, conversion, user satisfaction
- Monitor for bugs

### Phase 3: Full Launch (Week 3)
- Roll out to 100% if metrics positive
- Announce feature to users
- Gather feedback for threshold tuning

---

## Monitoring Metrics

Track in your analytics:
1. **macro_filters_extracted**: % of queries with user-specified macros
2. **category_defaults_applied**: % of queries using category optimization
3. **hard_filter_results**: Avg result count when hard filters applied
4. **macro_ctr_lift**: CTR improvement vs baseline

---

## Support

- **Framework docs**: See `MACRO_FILTERING_FRAMEWORK.md`
- **Design rationale**: See `MACRO_FRAMEWORK_SOLUTION_THESIS.md`
- **Category profiles**: Edit `shopping_bot/taxonomies/category_macro_profiles.json`
- **Code**: `shopping_bot/macro_optimizer.py`

---

**Estimated Total Implementation Time**: 1-2 hours

**Questions?** Ask for clarification on any step! 🚀

