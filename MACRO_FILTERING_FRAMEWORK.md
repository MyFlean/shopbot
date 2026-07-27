# 🎯 Macro-Aware Product Search Framework

## Executive Summary

This framework enables **nutritional macro-based product filtering and ranking** by leveraging the new Elasticsearch nested `nutri_breakdown_updated` field structure. It seamlessly integrates with existing architecture while adding intelligent category-aware macro optimization.

---

## 🧠 First Principles Analysis

### Core Problem
Users want nutritionally optimized products but:
1. **Don't know** which nutrients matter for each category (e.g., "chips" → low sodium matters)
2. **Don't specify** macro constraints explicitly in most queries
3. **Expect intelligence**: "show me chips" should favor healthier variants automatically

### Solution Philosophy

```
┌─────────────────────────────────────────────────────────────┐
│  EXPLICIT (User-Specified) → HARD FILTERS                  │
│  "protein bars with >20g protein"                           │
│  ├─ Hard constraint: protein g >= 20                        │
│  └─ ES: range filter (must match)                           │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  IMPLICIT (Category-Aware) → SOFT RANKING                   │
│  "show me chips"                                             │
│  ├─ Soft optimization: favor low sodium, low sat fat        │
│  └─ ES: function_score boosts (affects ranking)             │
└─────────────────────────────────────────────────────────────┘
```

**Key Insight**: Different product categories have different "nutritional expectations"
- **Chips**: Minimize sodium, saturated fat, trans fat
- **Protein bars**: Maximize protein, minimize sugar
- **Yogurt**: Maximize protein + calcium, minimize added sugar
- **Energy drinks**: Optimize caffeine range (80-150mg), minimize sugar

---

## 📐 Architecture Overview

### Three-Layer System

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: LLM Extraction (llm_service.py)                   │
│  ├─ Extracts user-specified macro constraints                │
│  ├─ Tool: UNIFIED_ES_PARAMS_TOOL + macro_filters            │
│  └─ Output: {macro_filters: [{nutrient, operator, value}]}  │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  Layer 2: Category Intelligence (macro_optimizer.py)        │
│  ├─ Loads category_macro_profiles.json                       │
│  ├─ Maps L3 category → default macro optimizations           │
│  ├─ Merges user-specified with category defaults             │
│  └─ Output: {hard_filters, soft_boosts}                      │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  Layer 3: ES Query Builder (search_v2)                 │
│  ├─ Hard filters → ES range queries (bool.filter)            │
│  ├─ Soft boosts → ES function_score                          │
│  └─ Output: Elasticsearch query JSON                         │
└─────────────────────────────────────────────────────────────┘
```

---

## 🛠️ Implementation Details

### Phase 1: LLM Tool Schema Extension

**File**: `shopping_bot/llm_service.py`

**Add to `UNIFIED_ES_PARAMS_TOOL`**:

```python
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
                "description": (
                    "Comparison operator:\n"
                    "- gte (>=): Greater than or equal (e.g., protein >= 20g)\n"
                    "- lte (<=): Less than or equal (e.g., sodium <= 300mg)\n"
                    "- gt (>): Strictly greater than\n"
                    "- lt (<): Strictly less than"
                )
            },
            "value": {
                "type": "number",
                "description": "Numeric threshold value in the nutrient's standard unit"
            },
            "priority": {
                "type": "string",
                "enum": ["hard", "soft"],
                "default": "hard",
                "description": (
                    "Filter priority:\n"
                    "- hard: User-specified constraint (ES bool.filter)\n"
                    "- soft: System suggestion (ES function_score boost)"
                )
            }
        },
        "required": ["nutrient_name", "operator", "value"]
    },
    "maxItems": 5,
    "description": (
        "Nutritional macro constraints extracted from user query. "
        "Examples:\n"
        "- 'protein bars with more than 20g protein' → [{nutrient_name: 'protein g', operator: 'gt', value: 20}]\n"
        "- 'low sodium chips under 200mg' → [{nutrient_name: 'sodium mg', operator: 'lt', value: 200}]\n"
        "- 'high protein low sugar (>15g protein, <5g sugar)' → "
        "[{nutrient_name: 'protein g', operator: 'gt', value: 15}, "
        "{nutrient_name: 'total sugar g', operator: 'lt', value: 5}]"
    )
}
```

**Update LLM Prompt** with nutrient extraction examples:

```python
MACRO_EXTRACTION_EXAMPLES = """
<macro_extraction_examples>
<example>
  User: "Show me protein bars with more than 20g protein"
  macro_filters: [
    {"nutrient_name": "protein g", "operator": "gt", "value": 20, "priority": "hard"}
  ]
</example>

<example>
  User: "I want chips with less than 200mg sodium"
  macro_filters: [
    {"nutrient_name": "sodium mg", "operator": "lt", "value": 200, "priority": "hard"}
  ]
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
  User: "Low calorie ice cream (under 150 calories per serving)"
  macro_filters: [
    {"nutrient_name": "energy kcal", "operator": "lt", "value": 150, "priority": "hard"}
  ]
</example>

<example>
  User: "I have high blood pressure, need low sodium options"
  reasoning: Health condition implies sodium restriction
  macro_filters: [
    {"nutrient_name": "sodium mg", "operator": "lte", "value": 140, "priority": "hard"}
  ]
</example>

<example>
  User: "Diabetes-friendly breakfast cereals"
  reasoning: Diabetes implies low sugar requirement
  macro_filters: [
    {"nutrient_name": "added sugar g", "operator": "lte", "value": 5, "priority": "hard"}
  ]
</example>

<example>
  User: "Post-workout snacks for muscle recovery"
  reasoning: Post-workout implies high protein need
  macro_filters: [
    {"nutrient_name": "protein g", "operator": "gte", "value": 15, "priority": "hard"},
    {"nutrient_name": "carbohydrate g", "operator": "gte", "value": 20, "priority": "soft"}
  ]
</example>

<extraction_rules>
1. Extract EXPLICIT macro mentions with numbers
2. Infer constraints from health conditions (diabetes→low sugar, hypertension→low sodium)
3. Infer from use cases (post-workout→high protein, weight loss→low calorie)
4. Use "hard" priority for explicit user constraints
5. Use "soft" priority for inferred optimizations
6. Standardize nutrient names to match nutri_breakdown_updated fields
7. Convert units if needed (user says "milligrams" → use "mg" suffix)
</extraction_rules>
</macro_extraction_examples>
"""
```

---

### Phase 2: Category Intelligence Module

**File**: `shopping_bot/macro_optimizer.py` (NEW)

```python
"""
Macro Optimizer: Category-aware nutritional intelligence
────────────────────────────────────────────────────────
Merges user-specified macro constraints with category-specific defaults
"""

import json
import os
from typing import Dict, List, Any, Optional
from pathlib import Path

class MacroOptimizer:
    """Intelligent macro filtering and ranking based on category profiles"""
    
    def __init__(self):
        self.profiles = self._load_category_profiles()
    
    def _load_category_profiles(self) -> Dict[str, Any]:
        """Load category macro profiles from JSON"""
        try:
            profile_path = Path(__file__).parent / "taxonomies" / "category_macro_profiles.json"
            with open(profile_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"ERROR: Failed to load category_macro_profiles.json: {e}")
            return {"_default": {"optimize": {"maximize": [], "minimize": []}}}
    
    def get_category_profile(self, category_path: str) -> Dict[str, Any]:
        """
        Extract L3 category from full path and return its profile
        
        Args:
            category_path: Full path like "f_and_b/food/light_bites/chips_and_crisps"
        
        Returns:
            Category profile dict with optimize rules
        """
        # Extract L2 and L3 from path
        parts = [p for p in category_path.split('/') if p]
        
        # Path format: f_and_b/food/{L2}/{L3} or f_and_b/beverages/{L2}/{L3}
        if len(parts) >= 4:
            l2 = parts[2]  # e.g., "light_bites"
            l3 = parts[3]  # e.g., "chips_and_crisps"
            
            # Check L2 → L3 nested structure
            if l2 in self.profiles and l3 in self.profiles[l2]:
                return self.profiles[l2][l3]
        
        # Fallback to L2 level if L3 not found
        if len(parts) >= 3:
            l2 = parts[2]
            if l2 in self.profiles:
                # Return first L3 in that L2 as default, or create synthetic
                for l3_key in self.profiles[l2]:
                    if isinstance(self.profiles[l2][l3_key], dict):
                        return self.profiles[l2][l3_key]
        
        # Final fallback to _default
        return self.profiles.get("_default", {
            "optimize": {"maximize": [], "minimize": []},
            "display_priority": [],
            "health_context": "General nutritional optimization"
        })
    
    def merge_constraints(
        self,
        user_constraints: List[Dict[str, Any]],
        category_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Merge user-specified constraints with category defaults
        
        Args:
            user_constraints: List of macro_filters from LLM
            category_path: Product category path (e.g., "f_and_b/food/light_bites/chips_and_crisps")
        
        Returns:
            {
                "hard_filters": [...],  # User-specified + hard category defaults
                "soft_boosts": [...],   # Category-based soft optimizations
                "display_priority": [...] # Nutrients to show in results
            }
        """
        hard_filters = []
        soft_boosts = []
        
        # 1. Add user-specified constraints as hard filters
        user_nutrients = set()
        for constraint in (user_constraints or []):
            if not constraint:
                continue
            
            nutrient = constraint.get("nutrient_name")
            operator = constraint.get("operator")
            value = constraint.get("value")
            priority = constraint.get("priority", "hard")
            
            if not (nutrient and operator and value is not None):
                continue
            
            user_nutrients.add(nutrient)
            
            if priority == "hard":
                hard_filters.append({
                    "nutrient": nutrient,
                    "operator": operator,
                    "value": value,
                    "source": "user"
                })
            else:
                soft_boosts.append({
                    "nutrient": nutrient,
                    "operator": operator,
                    "value": value,
                    "weight": 1.2 if operator in ["gte", "gt"] else 0.85,
                    "source": "user_soft"
                })
        
        # 2. Add category-specific defaults (only if not overridden by user)
        if category_path:
            profile = self.get_category_profile(category_path)
            optimize = profile.get("optimize", {})
            
            # Process maximize targets (soft boosts)
            for item in optimize.get("maximize", []):
                nutrient = item.get("field")
                if nutrient not in user_nutrients:
                    soft_boosts.append({
                        "nutrient": nutrient,
                        "operator": "gte",
                        "value": item.get("ideal_min", 0),
                        "weight": item.get("weight", 1.2),
                        "source": "category_default"
                    })
            
            # Process minimize targets (soft penalties)
            for item in optimize.get("minimize", []):
                nutrient = item.get("field")
                if nutrient not in user_nutrients:
                    # Check if there's an ideal_max (range optimization)
                    if "ideal_max" in item:
                        soft_boosts.append({
                            "nutrient": nutrient,
                            "operator": "lte",
                            "value": item.get("ideal_max"),
                            "weight": item.get("weight", 0.85),
                            "source": "category_default"
                        })
            
            display_priority = profile.get("display_priority", [])
        else:
            display_priority = []
        
        return {
            "hard_filters": hard_filters,
            "soft_boosts": soft_boosts,
            "display_priority": display_priority
        }
    
    def validate_nutrient_name(self, nutrient: str) -> bool:
        """
        Validate that nutrient field exists in nutri_breakdown_updated
        
        Common fields:
        - Macros: protein g, carbohydrate g, total fat g, fiber g, total sugar g, added sugar g
        - Fats: saturated fat g, trans fat g, unsaturated fat g, mufa g, pufa g
        - Minerals: sodium mg, calcium mg, iron mg, potassium mg, zinc mg, magnesium mg
        - Vitamins: vitamin a mcg, vitamin b12 mcg, vitamin c mg, vitamin d mcg, vitamin e mg
        - Special: caffeine mg, cholesterol mg, energy kcal
        """
        valid_nutrients = [
            # Macros
            "protein g", "carbohydrate g", "carbs g", "total fat g", "fat g",
            "fiber g", "total sugar g", "added sugar g", "natural sugar g",
            "energy kcal", "calories kcal",
            
            # Fats
            "saturated fat g", "trans fat g", "unsaturated fat g", "mufa g", "pufa g",
            
            # Minerals (mg)
            "sodium mg", "calcium mg", "iron mg", "potassium mg", "zinc mg",
            "magnesium mg", "phosphorous mg", "chloride mg", "selenium mg",
            "copper mg", "manganese mg",
            
            # Vitamins
            "vitamin a mcg", "vitamin b12 mcg", "vitamin c mg", "vitamin d mcg",
            "vitamin e mg", "vitamin k mcg", "folate mcg", "niacin mg",
            "riboflavin mg", "thiamine mg",
            
            # Special compounds
            "caffeine mg", "cholesterol mg", "taurine mg", "glucuronolactone mg",
            "l-theanine mg", "melatonin mg", "omega 3 dha mg", "omega 3 ala mg",
        ]
        
        return nutrient.lower() in [n.lower() for n in valid_nutrients]
    
    def normalize_nutrient_name(self, user_input: str) -> Optional[str]:
        """
        Normalize user-friendly nutrient names to ES field names
        
        Examples:
            "protein" → "protein g"
            "sodium" → "sodium mg"
            "calories" → "energy kcal"
            "added sugars" → "added sugar g"
        """
        mapping = {
            "protein": "protein g",
            "carbs": "carbohydrate g",
            "carbohydrate": "carbohydrate g",
            "carbohydrates": "carbohydrate g",
            "fat": "total fat g",
            "fiber": "fiber g",
            "fibre": "fiber g",
            "sugar": "total sugar g",
            "sugars": "total sugar g",
            "added sugar": "added sugar g",
            "added sugars": "added sugar g",
            "calories": "energy kcal",
            "energy": "energy kcal",
            
            # Fats
            "saturated fat": "saturated fat g",
            "trans fat": "trans fat g",
            "unsaturated fat": "unsaturated fat g",
            
            # Minerals
            "sodium": "sodium mg",
            "calcium": "calcium mg",
            "iron": "iron mg",
            "potassium": "potassium mg",
            "zinc": "zinc mg",
            "magnesium": "magnesium mg",
            
            # Vitamins
            "vitamin a": "vitamin a mcg",
            "vitamin b12": "vitamin b12 mcg",
            "vitamin c": "vitamin c mg",
            "vitamin d": "vitamin d mcg",
            "vitamin e": "vitamin e mg",
            
            # Special
            "caffeine": "caffeine mg",
            "cholesterol": "cholesterol mg",
        }
        
        user_lower = user_input.lower().strip()
        return mapping.get(user_lower)


# Singleton instance
_optimizer = None

def get_macro_optimizer() -> MacroOptimizer:
    """Get or create macro optimizer singleton"""
    global _optimizer
    if _optimizer is None:
        _optimizer = MacroOptimizer()
    return _optimizer
```

---

### Phase 3: ES Query Builder Integration

**File**: `search_v2/extension/search`

**Modify `_build_enhanced_es_query` function**:

```python
def _build_enhanced_es_query(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build ES query with improved brand handling and percentile-based ranking.
    NOW SUPPORTS: Macro-based filtering and ranking
    """
    p = params or {}
    
    # ... existing code ...
    
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
    
    hard_filters = merged_constraints.get("hard_filters", [])
    soft_boosts = merged_constraints.get("soft_boosts", [])
    
    # Apply hard filters as ES range queries
    for hf in hard_filters:
        nutrient = hf.get("nutrient")
        operator = hf.get("operator")
        value = hf.get("value")
        
        if not (nutrient and operator and value is not None):
            continue
        
        field_path = f"category_data.nutritional.nutri_breakdown_updated.{nutrient}"
        
        # Build range query
        range_query = {"range": {field_path: {}}}
        
        if operator == "gte":
            range_query["range"][field_path]["gte"] = value
        elif operator == "lte":
            range_query["range"][field_path]["lte"] = value
        elif operator == "gt":
            range_query["range"][field_path]["gt"] = value
        elif operator == "lt":
            range_query["range"][field_path]["lt"] = value
        
        filters.append(range_query)
        
        try:
            print(f"DEBUG: MACRO_HARD_FILTER | {nutrient} {operator} {value} (source: {hf.get('source')})")
        except Exception:
            pass
    
    # Store soft_boosts for function_score integration
    # These will be added to scoring_functions alongside existing percentile-based scoring
    macro_scoring_functions = []
    
    for sb in soft_boosts:
        nutrient = sb.get("nutrient")
        operator = sb.get("operator")
        value = sb.get("value")
        weight = sb.get("weight", 1.2)
        
        if not (nutrient and operator and value is not None):
            continue
        
        field_path = f"category_data.nutritional.nutri_breakdown_updated.{nutrient}"
        
        # Build function_score boost
        # If product meets soft constraint, multiply score by weight
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
    
    # ... rest of existing code ...
    
    # When building function_score, merge macro_scoring_functions
    if shoulds or filters:
        # Get category-specific scoring functions (existing)
        scoring_functions = build_function_score_functions(subcategory, include_flean=True)
        
        # ADD macro-based scoring functions
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
    
    # ... rest of function ...
```

---

### Phase 4: Source Field Inclusion

**Ensure nutri_breakdown_updated fields are returned in results**:

```python
# In _build_enhanced_es_query, update _source.includes:
"_source": {
    "includes": [
        "id", "name", "brand", "price", "mrp", "hero_image.*",
        "package_claims.*", "category_group", "category_paths", 
        "description", "use", "flean_score.*",
        "stats.adjusted_score_percentiles.*",
        # ... existing stats fields ...
        
        # NEW: Include nutritional data
        "category_data.nutritional.nutri_breakdown_updated.*",
        "category_data.nutritional.qty",
    ]
}
```

---

## 🧪 Testing Strategy

### Test Cases

#### Test 1: Explicit Hard Filter
```python
User: "Show me protein bars with more than 20g protein"
Expected:
  - LLM extracts: macro_filters=[{"nutrient_name": "protein g", "operator": "gt", "value": 20}]
  - ES query includes: range filter on protein g > 20
  - Results: Only products with protein > 20g
```

#### Test 2: Implicit Category Optimization
```python
User: "Show me chips"
Expected:
  - LLM extracts: category_paths=["f_and_b/food/light_bites/chips_and_crisps"], macro_filters=[]
  - MacroOptimizer adds soft boosts for low sodium, low sat fat
  - ES query includes: function_score boosts favoring healthier chips
  - Results: Chips ranked by health metrics (lower sodium ranked higher)
```

#### Test 3: Multi-Constraint
```python
User: "High protein, low sugar snacks - at least 15g protein and under 5g sugar"
Expected:
  - LLM extracts: macro_filters=[
      {"nutrient_name": "protein g", "operator": "gte", "value": 15},
      {"nutrient_name": "total sugar g", "operator": "lt", "value": 5}
    ]
  - ES query includes: TWO range filters (AND condition)
  - Results: Only products meeting BOTH constraints
```

#### Test 4: Health Condition Inference
```python
User: "I have high blood pressure, need snacks"
Expected:
  - LLM infers: macro_filters=[{"nutrient_name": "sodium mg", "operator": "lte", "value": 140}]
  - ES query includes: range filter on sodium <= 140mg
  - Results: Only low-sodium snacks
```

#### Test 5: Category + User Override
```python
User: "Protein bars with less than 10g sugar"  # User specifies sugar, not protein
Expected:
  - LLM extracts: macro_filters=[{"nutrient_name": "added sugar g", "operator": "lt", "value": 10}]
  - Category default adds soft boost for high protein (since user didn't specify)
  - ES query includes: 
    - Hard filter: sugar < 10g
    - Soft boost: favor high protein variants
  - Results: Low-sugar protein bars, ranked by protein content
```

---

## 📊 Query Examples & Expected Behavior

### Example 1: Basic Macro Query
**User Input**: "protein bars with more than 20g protein"

**LLM Output**:
```json
{
  "anchor_product_noun": "protein bars",
  "category_group": "f_and_b",
  "category_paths": ["f_and_b/food/light_bites/energy_bars"],
  "macro_filters": [
    {
      "nutrient_name": "protein g",
      "operator": "gt",
      "value": 20,
      "priority": "hard"
    }
  ]
}
```

**ES Query (simplified)**:
```json
{
  "query": {
    "bool": {
      "filter": [
        {"term": {"category_group": "f_and_b"}},
        {"range": {"category_data.nutritional.nutri_breakdown_updated.protein g": {"gt": 20}}}
      ]
    }
  }
}
```

---

### Example 2: Implicit Category Optimization
**User Input**: "show me chips"

**LLM Output**:
```json
{
  "anchor_product_noun": "chips",
  "category_group": "f_and_b",
  "category_paths": ["f_and_b/food/light_bites/chips_and_crisps"],
  "macro_filters": []
}
```

**MacroOptimizer Output**:
```json
{
  "hard_filters": [],
  "soft_boosts": [
    {"nutrient": "sodium mg", "operator": "lte", "value": 300, "weight": 0.8},
    {"nutrient": "saturated fat g", "operator": "lte", "value": 3.0, "weight": 0.85},
    {"nutrient": "protein g", "operator": "gte", "value": 5.0, "weight": 1.3}
  ]
}
```

**ES Query (simplified)**:
```json
{
  "query": {
    "function_score": {
      "query": {"bool": {"filter": [...]}},
      "functions": [
        {"filter": {"range": {"category_data.nutritional.nutri_breakdown_updated.sodium mg": {"lte": 300}}}, "weight": 0.8},
        {"filter": {"range": {"category_data.nutritional.nutri_breakdown_updated.saturated fat g": {"lte": 3.0}}}, "weight": 0.85},
        {"filter": {"range": {"category_data.nutritional.nutri_breakdown_updated.protein g": {"gte": 5.0}}}, "weight": 1.3}
      ],
      "score_mode": "multiply"
    }
  }
}
```

---

### Example 3: Health Condition
**User Input**: "I'm diabetic, need breakfast cereals"

**LLM Output** (with inference):
```json
{
  "anchor_product_noun": "breakfast cereals",
  "category_group": "f_and_b",
  "category_paths": ["f_and_b/food/breakfast_essentials/breakfast_cereals"],
  "macro_filters": [
    {
      "nutrient_name": "added sugar g",
      "operator": "lte",
      "value": 5,
      "priority": "hard"
    }
  ]
}
```

---

## 🔄 Integration Flow

```
┌──────────────────────────────────────────────────────────────┐
│ 1. User Query                                                 │
│    "High protein chips with less than 300mg sodium"          │
└──────────────────────────────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│ 2. LLM Extraction (llm_service.py)                           │
│    UNIFIED_ES_PARAMS_TOOL extracts:                          │
│    {                                                          │
│      anchor_product_noun: "chips",                           │
│      category_paths: ["f_and_b/food/light_bites/chips..."], │
│      macro_filters: [                                        │
│        {nutrient: "sodium mg", operator: "lt", value: 300}   │
│      ]                                                        │
│    }                                                          │
└──────────────────────────────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│ 3. Macro Optimizer (macro_optimizer.py)                      │
│    merge_constraints():                                       │
│    - User constraint: sodium < 300mg (HARD)                  │
│    - Category default: protein >= 5g (SOFT boost)            │
│    - Category default: sat fat <= 3g (SOFT penalty)          │
│    Output: {hard_filters: [...], soft_boosts: [...]}         │
└──────────────────────────────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│ 4. ES Query Builder (search_v2)                         │
│    _build_enhanced_es_query():                               │
│    - Add hard filter: range query sodium < 300               │
│    - Add soft boosts: function_score for protein, sat fat    │
│    - Merge with existing scoring (percentiles, flean score)  │
└──────────────────────────────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│ 5. Elasticsearch Execution                                    │
│    Returns: Products with sodium < 300mg,                    │
│             ranked by protein (higher better) and            │
│             sat fat (lower better)                            │
└──────────────────────────────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│ 6. Results Display                                            │
│    Show nutritional info prominently:                        │
│    - Display nutrients from display_priority                 │
│    - Highlight why products match (e.g., "High Protein!")    │
└──────────────────────────────────────────────────────────────┘
```

---

## ⚙️ Configuration & Extensibility

### Adding New Categories

1. Edit `category_macro_profiles.json`
2. Add new L2/L3 entry with optimize rules
3. No code changes needed - profiles loaded dynamically

### Adjusting Thresholds

Modify ideal_min/ideal_max values in JSON:
```json
"chips_and_crisps": {
  "optimize": {
    "minimize": [
      {"field": "sodium mg", "weight": 0.8, "ideal_max": 300}  // Adjust 300 → 250
    ]
  }
}
```

### Custom Nutrient Fields

If new nutrients added to ES mapping:
1. Update `validate_nutrient_name()` in `macro_optimizer.py`
2. Update LLM examples with new nutrient
3. Add to `normalize_nutrient_name()` mapping

---

## 🎯 Success Metrics

1. **Query Coverage**: % of product queries that benefit from macro optimization
2. **User Satisfaction**: CTR on macro-optimized results vs baseline
3. **Health Impact**: % of results showing improved nutritional profiles
4. **Fallback Rate**: % of queries with missing nutritional data (should be <20%)

---

## 🚀 Deployment Checklist

- [ ] Create `category_macro_profiles.json`
- [ ] Implement `macro_optimizer.py`
- [ ] Update `UNIFIED_ES_PARAMS_TOOL` schema
- [ ] Add macro extraction examples to LLM prompts
- [ ] Modify `_build_enhanced_es_query()` for macro filtering
- [ ] Update ES `_source` includes for nutri_breakdown_updated
- [ ] Add unit tests for MacroOptimizer
- [ ] Add integration tests for end-to-end flow
- [ ] Test with real user queries
- [ ] Deploy to staging
- [ ] Monitor ES query performance (latency impact)
- [ ] A/B test: macro-optimized vs baseline ranking
- [ ] Deploy to production

---

## 📝 Notes & Considerations

### Performance
- **Hard filters**: May reduce result count (acceptable)
- **Soft boosts**: Minimal performance impact (function_score is efficient)
- **Missing data**: Products without nutritional data still included (just not boosted)

### Data Quality
- `nutri_breakdown_updated` preferred over raw `nutri_breakdown` (normalized)
- Handle missing values gracefully (don't filter out, just don't boost)
- Consider adding `exists` queries for critical nutrients

### User Experience
- Show WHY products ranked high (e.g., "20g protein per serving!")
- Display nutritional highlights from `display_priority`
- Allow users to adjust thresholds dynamically in UI

---

## 🔮 Future Enhancements

1. **Dynamic Threshold Learning**: Learn optimal thresholds from user behavior
2. **Personalized Profiles**: User-specific macro preferences (e.g., "I'm on keto")
3. **Comparative Ranking**: "This has 30% less sodium than average chips"
4. **Nutrient Ratios**: "High protein-to-calorie ratio"
5. **Meal Planning**: "Breakfast options with <400 calories, >15g protein"

---

**Version**: 1.0  
**Last Updated**: 2025-01-10  
**Author**: AI Assistant  
**Status**: Design Complete, Ready for Implementation

