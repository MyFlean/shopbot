# Category Path Inference - RCA & Architecture Design

## Executive Summary
The current F&B category inference relies on LLM examples but lacks structured taxonomy guidance, resulting in inconsistent path generation. This document outlines a systematic upgrade to inject hierarchical taxonomy as authoritative context.

---

## 1. Current State Analysis

### 1.1 Taxonomy Sources
- **F&B Taxonomy**: Exists in `recommendation.py` as flat L2→L3 mapping (~15 L2 categories, 60+ L3s)
- **Personal Care**: Separate JSON loaded in `llm_service._get_personal_care_taxonomy()`
- **User-Provided**: Hierarchical JSON with `f_and_b/{food|beverages}/{l2}/{l3}` structure

### 1.2 Current LLM Context Flow
```
build_search_params() 
  → generate_unified_es_params()
    → Prompt with:
      - conversation_history (10 turns max)
      - user_slots (budget, dietary, preferences, intent)
      - Hard-coded examples: "banana chips" → ["light_bites/chips_and_crisps"]
      - NO structured taxonomy in prompt
```

### 1.3 Tool Schema (UNIFIED_ES_PARAMS_TOOL)
```python
"category_paths": {
    "type": "array",
    "items": {"type": "string"},
    "maxItems": 3,
    "description": "Full paths like 'f_and_b/food/light_bites/chips_and_crisps'"
}
```
**Gap**: Schema allows 3 paths but provides no taxonomy structure to guide ranked selection.

### 1.4 Critical Gaps Identified

| Gap | Impact | Root Cause |
|-----|--------|------------|
| No taxonomy in prompt | LLM invents paths or uses memorized examples | Missing structured context |
| Flat L2→L3 dict in code | Can't represent hierarchical food/beverages split | Old taxonomy format |
| Single example per category | Insufficient coverage for 60+ L3 categories | Limited training data |
| No ranking mechanism | LLM guesses relevance order | No confidence scoring |
| Path format inconsistency | Sometimes "light_bites/chips", sometimes "f_and_b/food/light_bites/chips" | Normalization happens post-LLM |

---

## 2. Requirements

### 2.1 Functional Requirements
1. **FR-1**: LLM MUST receive full hierarchical F&B taxonomy in structured format
2. **FR-2**: Return 2-3 category paths in **relevance-ranked order**
3. **FR-3**: Support both `food` and `beverages` branches under `f_and_b`
4. **FR-4**: Handle ambiguous queries with multiple plausible paths
5. **FR-5**: Maintain backward compatibility with existing path normalization

### 2.2 Non-Functional Requirements
1. **NFR-1**: Token efficiency - taxonomy should be compact JSON
2. **NFR-2**: Zero hallucination - paths must exist in provided taxonomy
3. **NFR-3**: Deterministic ranking when confidence is clear
4. **NFR-4**: Graceful degradation if taxonomy unavailable

---

## 3. Architectural Design

### 3.1 Taxonomy Format Transformation

**User-Provided Format** (hierarchical):
```json
{
  "f_and_b": {
    "food": {
      "light_bites": {
        "chips_and_crisps": {},
        "nachos": {}
      }
    },
    "beverages": {
      "sodas_juices_and_more": {
        "soft_drinks": {}
      }
    }
  }
}
```

**Flattened Prompt Format** (token-efficient):
```json
{
  "food": {
    "light_bites": ["chips_and_crisps", "nachos", "popcorn"],
    "frozen_treats": ["ice_cream_tubs", "kulfi"]
  },
  "beverages": {
    "sodas_juices_and_more": ["soft_drinks", "fruit_juices"],
    "tea_coffee_and_more": ["tea", "coffee"]
  }
}
```

### 3.2 Prompt Architecture (2025 Best Practices)

#### Structure
```xml
<taxonomy_reference type="f_and_b">
{flattened_json}
</taxonomy_reference>

<categorization_rules>
1. MUST select paths from provided taxonomy only
2. Return 2-3 paths in order: MOST_LIKELY → ALTERNATIVE → FALLBACK
3. Use full format: "f_and_b/{food|beverages}/{l2}/{l3}"
4. For ambiguous queries, include multiple L3s under same L2
5. If L3 unclear, use L2-only path: "f_and_b/food/{l2}"
</categorization_rules>

<examples>
<!-- Concrete taxonomy-backed examples -->
</examples>
```

#### Example Set Design
- **Single L3 match**: "banana chips" → ["f_and_b/food/light_bites/chips_and_crisps"]
- **Multiple L3 (ranked)**: "ice cream" → ["f_and_b/food/frozen_treats/ice_cream_tubs", "f_and_b/food/frozen_treats/kulfi"]
- **L2 fallback**: "frozen snacks" → ["f_and_b/food/frozen_foods"]
- **Beverages branch**: "cold coffee" → ["f_and_b/beverages/tea_coffee_and_more/iced_coffee_and_tea"]
- **Cross-L2 ambiguity**: "cookies" → ["f_and_b/food/biscuits_and_crackers/cookies", "f_and_b/food/sweet_treats/cookies"]

### 3.3 Tool Schema Enhancement

**Current**:
```python
"category_paths": {
    "type": "array",
    "items": {"type": "string"},
    "maxItems": 3,
    "description": "Full paths like 'f_and_b/food/light_bites/chips_and_crisps'"
}
```

**Enhanced** (with ranking semantics):
```python
"category_paths": {
    "type": "array",
    "items": {"type": "string"},
    "minItems": 1,
    "maxItems": 3,
    "description": (
        "Ranked category paths from taxonomy (MOST relevant first). "
        "Format: 'f_and_b/{food|beverages}/{l2}/{l3}' or 'f_and_b/{food|beverages}/{l2}'. "
        "MUST exist in provided taxonomy. Return 1-3 paths ordered by relevance."
    )
}
```

### 3.4 Implementation Flow

```
1. Transform user JSON → flattened taxonomy dict
   ↓
2. Inject into prompt as <taxonomy_reference>
   ↓
3. Add taxonomy-specific examples
   ↓
4. LLM generates ranked paths via tool call
   ↓
5. Validate paths exist in taxonomy (optional safety check)
   ↓
6. ES query builder uses paths as-is (already normalized)
```

---

## 4. Code Changes Plan

### 4.1 New Taxonomy Utility
**File**: `shopping_bot/llm_service.py`

```python
def _get_fnb_taxonomy_hierarchical(self) -> Dict[str, Any]:
    """Load user-provided hierarchical taxonomy and flatten for prompt."""
    # Load from config/env
    hierarchical = self._load_fnb_taxonomy_from_env()
    
    # Flatten to {food: {l2: [l3s]}, beverages: {l2: [l3s]}}
    flattened = self._flatten_taxonomy(hierarchical)
    return flattened

def _flatten_taxonomy(self, hier: Dict) -> Dict[str, Dict[str, List[str]]]:
    """Convert nested dict to 2-level structure."""
    result = {}
    for domain in ["food", "beverages"]:
        if domain in hier.get("f_and_b", {}):
            result[domain] = {}
            for l2, l3_dict in hier["f_and_b"][domain].items():
                result[domain][l2] = list(l3_dict.keys()) if isinstance(l3_dict, dict) else []
    return result
```

### 4.2 Prompt Injection Points
**File**: `shopping_bot/llm_service.py` → `generate_unified_es_params()`

```python
# Load taxonomy
fnb_taxonomy = self._get_fnb_taxonomy_hierarchical()

# Inject in prompt (both follow-up and new query branches)
prompt = f"""
<taxonomy_reference type="f_and_b">
{json.dumps(fnb_taxonomy, ensure_ascii=False, indent=2)}
</taxonomy_reference>

<categorization_rules priority="CRITICAL">
1. category_paths MUST come from taxonomy above
2. Return 1-3 paths ranked by relevance (most likely first)
3. Format: "f_and_b/{{food|beverages}}/{{l2}}/{{l3}}" or "f_and_b/{{food|beverages}}/{{l2}}"
4. For ambiguous queries, include multiple plausible L3s
5. Never hallucinate paths not in taxonomy
</categorization_rules>

<examples>
<!-- Add 15-20 taxonomy-backed examples here -->
</examples>
...
"""
```

### 4.3 Example Set (Comprehensive)
```python
TAXONOMY_EXAMPLES = """
<example category="exact_l3_match">
Query: "banana chips"
Reasoning: Specific flavor of chips → L3 match
Output: {
  "category_paths": ["f_and_b/food/light_bites/chips_and_crisps"],
  ...
}
</example>

<example category="ranked_l3_alternatives">
Query: "ice cream"
Reasoning: Generic ice cream → rank tubs first (most common), then alternatives
Output: {
  "category_paths": [
    "f_and_b/food/frozen_treats/ice_cream_tubs",
    "f_and_b/food/frozen_treats/ice_cream_cups",
    "f_and_b/food/frozen_treats/kulfi"
  ],
  ...
}
</example>

<example category="l2_fallback">
Query: "frozen snacks"
Reasoning: Ambiguous L3 → use L2 only
Output: {
  "category_paths": ["f_and_b/food/frozen_foods"],
  ...
}
</example>

<example category="beverages_branch">
Query: "cold coffee"
Reasoning: Beverage, not food
Output: {
  "category_paths": ["f_and_b/beverages/tea_coffee_and_more/iced_coffee_and_tea"],
  ...
}
</example>

<example category="cross_l2_ambiguity">
Query: "healthy breakfast"
Reasoning: Could be multiple L2s → rank by likelihood
Output: {
  "category_paths": [
    "f_and_b/food/breakfast_essentials/muesli_and_oats",
    "f_and_b/food/dairy_and_bakery/yogurt_and_shrikhand"
  ],
  ...
}
</example>
"""
```

### 4.4 Validation Layer (Optional)
```python
def _validate_category_paths(self, paths: List[str], taxonomy: Dict) -> List[str]:
    """Filter out hallucinated paths."""
    valid = []
    for path in paths:
        parts = path.split("/")
        if len(parts) >= 3:
            domain = parts[1]  # food or beverages
            l2 = parts[2]
            l3 = parts[3] if len(parts) > 3 else None
            if domain in taxonomy and l2 in taxonomy[domain]:
                if l3 is None or l3 in taxonomy[domain][l2]:
                    valid.append(path)
    return valid[:3]  # Keep max 3
```

---

## 5. Rollout Plan

### Phase 1: Core Implementation
1. ✅ Add taxonomy flattening utility
2. ✅ Inject taxonomy into both prompt branches (follow-up + new query)
3. ✅ Update categorization rules in prompt
4. ✅ Enhance tool schema description

### Phase 2: Example Enrichment
1. ✅ Add 15-20 taxonomy-backed examples
2. ✅ Cover edge cases (L2-only, beverages, ambiguity)
3. ✅ Test with real user queries

### Phase 3: Validation & Observability
1. ⚠️ Add path validation (optional - can be Phase 2+)
2. ✅ Log ranked paths for analytics
3. ✅ Monitor hallucination rate

### Phase 4: Optimization
1. Token usage analysis
2. Consider caching flattened taxonomy
3. A/B test with/without validation layer

---

## 6. Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Token limit exceeded | Use compact JSON, limit examples to 15-20 |
| LLM ignores taxonomy | Make rules CRITICAL priority, add validation |
| Backward compat break | Keep path normalization in ES builder |
| Taxonomy update lag | Support env-based override (already exists) |
| Hallucination despite rules | Add post-LLM validation filter |

---

## 7. Success Metrics

1. **Path Accuracy**: >95% of returned paths exist in taxonomy
2. **Ranking Quality**: Top path is correct for >90% of unambiguous queries
3. **Coverage**: All L2 categories represented in examples
4. **Token Efficiency**: <500 tokens for taxonomy + rules
5. **Latency**: No significant increase in LLM call time

---

## 8. Next Steps

1. Implement taxonomy flattening utility
2. Update prompt with taxonomy injection
3. Add comprehensive example set
4. Test with production query samples
5. Deploy with observability hooks
6. Iterate based on logs

---

**Author**: AI Senior Architect  
**Date**: 2025-01-08  
**Status**: Design Complete → Ready for Implementation

