# LLM1 Prompt Improvements - Redundancy Prevention

**Date:** November 4, 2025  
**Objective:** Eliminate redundant and contradictory follow-up questions in LLM1 (classify_and_assess)  
**Implementation:** Prompt engineering improvements following 2025 best practices

---

## Problem Statement

### Issue Identified
LLM1 was generating redundant or contradictory follow-up questions that confused users:

**Example of the problem:**
```
Question 1: "What matters most to you?"
  1. Quality
  2. Brand
  3. Value for Money

Question 2: "What's your budget range?"
  1. Budget Friendly
  2. Smart Value
  3. Value for Money
```

**Why this is problematic:**
- "Value for Money" appears in BOTH questions (direct duplicate)
- "Budget Friendly" and "Smart Value" are semantically similar to "Value for Money"
- "Quality" overlaps with price/budget considerations
- Creates cognitive dissonance - user doesn't know which question to use for price preferences
- Reduces clarity and user experience quality

---

## Root Cause Analysis

### Identified Issues in Original Prompt

1. **No explicit redundancy prevention rules**
   - Prompt didn't warn against semantic overlap between questions
   - No guidance on what makes questions redundant

2. **Lack of concrete option guidelines**
   - Used vague, multi-dimensional terms: "Quality", "Value", "Smart Choice", "Premium"
   - These terms span multiple dimensions (price + quality + brand prestige)

3. **No validation checklist**
   - LLM had no structured way to self-validate question quality
   - Missing pre-flight checks before generating responses

4. **Insufficient examples**
   - No negative examples showing what NOT to do
   - Examples didn't explicitly demonstrate dimension separation

---

## Solution: Enhanced LLM1 Prompt (2025 Best Practices)

### Changes Made

#### 1. Added Comprehensive Redundancy Prevention Rules

**Location:** `llm_service.py`, lines 2030-2076

**Key additions:**

**Rule 1: No semantic overlap between questions**
```
- BUDGET = price ranges only (e.g., "Under ₹50", "₹50-150", "Over ₹150")
- PREFERENCES = product attributes EXCLUDING price (e.g., flavor, brand, texture)
- Each targets a DIFFERENT aspect
```

**Rule 2: No option reuse across questions**
```
- Each option value appears in EXACTLY ONE question
- ❌ BAD: Q1 has "Value for Money" + Q2 has "Value for Money"
- ❌ BAD: Q1 has "Budget Friendly" + Q2 has "Budget-conscious" (semantic duplicate)
```

**Rule 3: Questions target distinct decision dimensions**
```
- Budget → Price tolerance (numeric/concrete)
- Dietary → Health/ingredient requirements (boolean/categorical)
- Use Case → Context of consumption (situational)
- Preferences → Sensory/brand attributes (qualitative)
```

**Rule 4: Mutually exclusive options within questions**
```
- Options should cover different points on a spectrum
- No overlapping concepts within a single question
```

**Rule 5: Avoid value-laden multi-dimensional terms**
```
- ❌ AVOID: Quality, Value for Money, Smart Choice, Best Deal, Premium quality
- ✅ USE: Specific price ranges, specific flavors, specific features, specific quantities
```

#### 2. Enhanced Tool Schema Descriptions

**Location:** `llm_service.py`, lines 453-493

**Improvements:**

**Updated slot_name description:**
```json
"description": "Slot type: BUDGET (ONLY numeric price ranges like 'Under ₹50'), 
                DIETARY (health requirements), 
                PREFERENCES (ONLY non-price attributes like flavor/texture), ..."
```

**Updated options description:**
```json
"description": "EXACTLY 3 discrete, mutually exclusive options (2-5 words each). 
                CRITICAL: Use concrete terms (price ranges, specific flavors, specific features). 
                NEVER use vague multi-dimensional terms like 'Quality', 'Value for Money', 'Smart Choice'. 
                Each option must be UNIQUE across ALL questions in this array - no semantic duplicates allowed."
```

**Updated ask_slots array description:**
```json
"description": "Ordered list of NON-REDUNDANT questions (2-4 based on domain). 
                MANDATORY: Validate NO semantic overlap between questions. 
                Each question must target a DISTINCT dimension (price, flavor, quantity, etc.). 
                NO option should appear in multiple questions."
```

#### 3. Added Self-Validation Checklist

**Location:** `llm_service.py`, lines 2069-2076

Provides a structured checklist for the LLM to validate before generating questions:

```
□ Each question targets a DISTINCT aspect (price, flavor, quantity, concern, etc.)
□ NO option appears in more than one question
□ Budget question uses ONLY price ranges (₹ amounts), never vague terms
□ Preference question uses ONLY product attributes (flavor, texture, brand popularity), never price-related terms
□ All options within a question are mutually exclusive and non-overlapping
□ No semantic duplicates across questions (e.g., "Budget Friendly" and "Value for Money" are duplicates)
```

#### 4. Added Positive and Negative Examples

**Location:** `llm_service.py`, lines 2077-2098

**✅ GOOD Example 1 - Food & Beverage:**
```
Query: "chips for party tonight"
ask_slots:
  1. ASK_USER_BUDGET: "What's your budget per pack?" 
     → ["Under ₹50", "₹50-150", "Over ₹150"]
  2. ASK_QUANTITY: "How many guests?" 
     → ["10-20 people", "20-30 people", "30+ people"]
```

**✅ GOOD Example 2 - Personal Care:**
```
Query: "shampoo for my hair"
ask_slots:
  1. ASK_USER_BUDGET: "What's your budget?" 
     → ["Under ₹99", "₹99-299", "Over ₹299"]
  2. ASK_PC_CONCERN: "Main hair concern?" 
     → ["Dandruff", "Hair fall", "Frizz"]
  3. ASK_PC_COMPATIBILITY: "Your hair type?" 
     → ["Oily scalp", "Dry scalp", "Normal"]
  4. ASK_INGREDIENT_AVOID: "Any ingredients to avoid?" 
     → ["Sulfate-free", "Paraben-free", "No preference"]
```

**❌ BAD Example (NEVER DO THIS):**
```
Query: "chips"
ask_slots:
  1. ASK_USER_PREFERENCES: "What matters most?" 
     → ["Quality", "Brand", "Value for Money"]
  2. ASK_USER_BUDGET: "Budget range?" 
     → ["Budget Friendly", "Smart Value", "Premium"]

Why bad: 
- "Value for Money", "Budget Friendly", and "Smart Value" all relate to price
- "Quality" and "Premium" overlap
- Creates user confusion
```

#### 5. Added Final Pre-Flight Validation

**Location:** `llm_service.py`, lines 2159-2171

A mandatory checklist right before the LLM returns its response:

```
MANDATORY PRE-FLIGHT CHECK (run this mentally before returning your tool call):

If you're generating ask_slots, verify:
1. ✓ Budget question uses ONLY price ranges with ₹ symbol
2. ✓ Preferences question uses ONLY non-price attributes
3. ✓ NO option text appears in more than one question
4. ✓ NO value-laden abstract terms like "Quality", "Value for Money", "Smart Choice"
5. ✓ Each question targets a DIFFERENT aspect of the user's needs
6. ✓ All options within each question are mutually exclusive and clearly distinct

If any check fails, revise your ask_slots before responding.
```

---

## 2025 Prompt Engineering Best Practices Applied

### 1. **Structured Instructions with Clear Hierarchy**
- Used XML-like tags: `<ask_slot_guidance>`, `<final_validation_before_response>`
- Organized content into numbered rules and sub-rules
- Clear visual hierarchy with headers and bullet points

### 2. **Explicit Constraint Definition**
- Defined what to do AND what NOT to do
- Used ❌ and ✅ symbols for visual clarity
- Provided specific banned terms and recommended alternatives

### 3. **Concrete Examples Over Abstract Rules**
- Provided both positive and negative examples
- Showed complete question sets, not just individual questions
- Included explanations of WHY examples are good or bad

### 4. **Self-Validation Mechanisms**
- Added checklist-style validation points
- Positioned validation BEFORE the final instruction
- Made validation mandatory, not optional

### 5. **Semantic Grounding**
- Defined semantic dimensions clearly (price vs. flavor vs. quantity)
- Explained relationships between concepts (Budget Friendly ≈ Value for Money)
- Provided domain-specific guidance (F&B vs. Personal Care)

### 6. **Chain-of-Thought Reinforcement**
- Multiple checkpoints: guidance → examples → validation
- Progressive disclosure: general rules → specific examples → final check
- Repetition of critical rules in different formats

### 7. **Tool Schema Alignment**
- Enhanced tool schema descriptions to match prompt instructions
- Made constraints explicit in both prompt and schema
- Used consistent terminology across prompt and schema

---

## Expected Improvements

### User Experience
1. **Clearer Questions**: No more ambiguous "What matters most?" questions
2. **No Duplicate Options**: Each option appears only once across all questions
3. **Distinct Dimensions**: Budget asks about price; Preferences asks about flavor/texture
4. **Concrete Choices**: Price ranges instead of vague terms like "Value for Money"

### Question Quality
1. **Budget Questions**: Always use specific ₹ ranges
   - Example: "Under ₹50", "₹50-150", "Over ₹150"
   
2. **Preference Questions**: Focus on product attributes only
   - Example: "Spicy", "Mild", "Tangy" (for food)
   - Example: "Popular brands", "Niche brands", "No preference" (for brands)

3. **Personal Care Questions**: Domain-specific and concrete
   - Example: "Oily scalp", "Dry scalp", "Normal" (for hair type)

### Eliminated Anti-Patterns
1. ❌ "Quality" options (too vague and multi-dimensional)
2. ❌ "Value for Money" options (spans price + quality + brand)
3. ❌ "Smart Choice" / "Best Deal" options (ambiguous)
4. ❌ Duplicate options across questions
5. ❌ Semantically overlapping questions

---

## Testing Recommendations

### Test Cases to Validate Fix

#### Test Case 1: Food & Beverage Query
```
Input: "I want chips"

Expected Output (2 questions):
Q1: "What's your budget per pack?"
  → ["Under ₹50", "₹50-150", "Over ₹150"]

Q2: "What flavor do you prefer?"
  → ["Spicy", "Mild", "Tangy"]

Validation:
✓ No option appears twice
✓ Budget uses price ranges only
✓ Preferences uses flavor attributes only
✓ No terms like "Quality", "Value for Money"
```

#### Test Case 2: Personal Care Query
```
Input: "Need shampoo"

Expected Output (4 questions):
Q1: "What's your budget?"
  → ["Under ₹99", "₹99-299", "Over ₹299"]

Q2: "Main hair concern?"
  → ["Dandruff", "Hair fall", "Frizz"]

Q3: "Your hair type?"
  → ["Oily scalp", "Dry scalp", "Normal"]

Q4: "Any ingredients to avoid?"
  → ["Sulfate-free", "Paraben-free", "No preference"]

Validation:
✓ 4 questions for personal_care domain
✓ Each question targets distinct dimension
✓ No semantic overlap
✓ Concrete, specific options
```

#### Test Case 3: Complex Query with Multiple Signals
```
Input: "chips for party tonight"

Expected Output (2 questions):
Q1: "What's your budget per pack?"
  → ["Under ₹50", "₹50-150", "Over ₹150"]

Q2: "How many guests are you expecting?"
  → ["10-20 people", "20-30 people", "30+ people"]

Validation:
✓ Picked QUANTITY instead of PREFERENCES (context-aware)
✓ Budget still concrete price ranges
✓ No redundancy
```

### Monitoring

Monitor LLM1 outputs for:
1. **Option uniqueness**: No option text appears in multiple questions
2. **Concrete terms**: No abstract terms like "Quality", "Value", "Smart"
3. **Dimension separation**: Budget = price only, Preferences = non-price only
4. **Example count**: Exactly 3 options per question

---

## Files Modified

### Primary Change
- **File**: `/Users/priyam_ps/Desktop/shopbot/shopping_bot/llm_service.py`
- **Lines Modified**: 
  - 2014-2106: Enhanced `<ask_slot_guidance>` section with redundancy prevention rules
  - 453-493: Enhanced tool schema descriptions for `ask_slots`
  - 2159-2171: Added `<final_validation_before_response>` section

### Change Type
- **Prompt Engineering Only**: No code logic changes
- **Backward Compatible**: Existing functionality unchanged
- **Zero Breaking Changes**: Only improves question quality

---

## Rollback Plan

If issues arise, revert changes in `llm_service.py`:
```bash
git diff shopping_bot/llm_service.py
git checkout shopping_bot/llm_service.py  # If needed
```

The changes are isolated to prompt strings only, so rollback is safe and simple.

---

## Success Metrics

### Qualitative
- ✅ Zero reports of duplicate options across questions
- ✅ Zero reports of "confusing" or "contradictory" questions
- ✅ User feedback indicates clearer decision-making

### Quantitative (if logging is available)
- Track option overlap rate: Should be 0%
- Track usage of banned terms ("Quality", "Value for Money"): Should be 0%
- Track question dimension diversity: Should be 100% (all questions target different aspects)

---

## Conclusion

This implementation applies **2025 prompt engineering best practices** to eliminate redundant and contradictory follow-up questions in LLM1. The solution:

1. ✅ **Explicitly defines** what redundancy means
2. ✅ **Provides concrete rules** with examples
3. ✅ **Includes validation mechanisms** (checklist + pre-flight)
4. ✅ **Uses visual clarity** (❌/✅ symbols, structured sections)
5. ✅ **Aligns tool schema** with prompt instructions
6. ✅ **Maintains backward compatibility**

The improved prompt should now guide Claude to generate high-quality, non-redundant follow-up questions that enhance user experience.

---

**Implementation Status**: ✅ Complete  
**Testing Status**: ⏳ Pending user validation  
**Production Ready**: ✅ Yes (prompt-only changes, zero code logic changes)

