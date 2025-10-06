# ASK Slot Enrichment Improvements

## Problem Identified
When asking `ASK_QUANTITY`, the system was returning generic placeholder options:
```json
"options": ["Option 1", "Option 2", "No preference"]
```

Instead of meaningful values like:
```json
"options": ["10-20 people", "20-30 people", "30+ people", "Not sure yet"]
```

## Root Cause
The enrichment logic in `classify_and_assess` had an incomplete if/elif chain that only handled 5 of the 8 possible slot types:
- ✅ `ASK_USER_BUDGET`
- ✅ `ASK_DIETARY_REQUIREMENTS`
- ✅ `ASK_PC_CONCERN`
- ✅ `ASK_PC_COMPATIBILITY`
- ✅ `ASK_INGREDIENT_AVOID`
- ❌ `ASK_QUANTITY` → fell through to generic fallback
- ❌ `ASK_USE_CASE` → fell through to generic fallback
- ❌ `ASK_USER_PREFERENCES` → fell through to generic fallback

## Solutions Implemented

### 1. Added Missing Constants (lines 328-341)

```python
# Quantity options (context-aware)
QUANTITY_PARTY = ["10-20 people", "20-30 people", "30+ people", "Not sure yet"]
QUANTITY_PERSONAL = ["Just for me", "2-3 people", "Family (4-6)", "Not sure"]
QUANTITY_BULK = ["1-2 packs", "3-5 packs", "Bulk order (6+)", "Flexible"]

# Use case options (category-specific)
USE_CASE_SNACKS = ["Party/gathering", "Daily snacking", "Kids lunch box", "Travel/on-the-go"]
USE_CASE_BEVERAGES = ["Morning boost", "Post-workout", "Throughout the day", "Special occasions"]
USE_CASE_PERSONAL_CARE = ["Daily use", "Special occasions", "Specific concern", "Trying something new"]

# User preferences (generic fallback)
USER_PREFERENCES_BRAND = ["Popular brands", "Budget-friendly", "Premium/imported", "No preference"]
USER_PREFERENCES_FLAVOR = ["Sweet", "Savory", "Tangy/spicy", "No preference"]
USER_PREFERENCES_TEXTURE = ["Crunchy", "Soft", "Creamy", "No preference"]
```

### 2. Enhanced Enrichment Logic (lines 1406-1473)

**Key improvements:**

#### Context-Aware Quantity Detection
```python
elif slot_name == "ASK_QUANTITY":
    if any(kw in query_lower for kw in ["party", "gathering", "event", "celebration", "guests"]):
        options = QUANTITY_PARTY  # 10-20, 20-30, 30+
    elif any(kw in query_lower for kw in ["bulk", "stock", "many", "wholesale"]):
        options = QUANTITY_BULK  # 1-2 packs, 3-5 packs, bulk
    else:
        options = QUANTITY_PERSONAL  # Just for me, 2-3 people, family
```

#### Category-Specific Use Cases
```python
elif slot_name == "ASK_USE_CASE":
    if domain == "personal_care":
        options = USE_CASE_PERSONAL_CARE
    elif category in ["chips_and_crisps", "cookies_biscuits", "snacks", "namkeen"]:
        options = USE_CASE_SNACKS
    elif category in ["beverages", "drinks", "juice", "energy_drink"]:
        options = USE_CASE_BEVERAGES
```

#### Intelligent Preference Mapping
```python
elif slot_name == "ASK_USER_PREFERENCES":
    if category in ["chips_and_crisps", "cookies_biscuits", "snacks"]:
        options = USER_PREFERENCES_FLAVOR  # Sweet, Savory, Tangy
    elif any(kw in category for kw in ["cream", "yogurt", "sauce", "spread"]):
        options = USER_PREFERENCES_TEXTURE  # Crunchy, Soft, Creamy
    else:
        options = USER_PREFERENCES_BRAND  # Popular, Budget, Premium
```

#### Smart Fallback (No More Generic Options!)
```python
else:
    log.warning(f"Unknown slot type: {slot_name}, using smart fallback")
    if domain == "personal_care":
        options = ["Natural/organic", "Dermatologist tested", "Budget-friendly", "No preference"]
    else:
        options = ["Healthier choice", "Popular brands", "Best value", "No preference"]
```

### 3. Improved LLM Guidance Prompt (lines 1380-1407)

Added comprehensive `<ask_slot_guidance>` section with:

**Smart Question Selection Rules:**
- F&B: Budget → always, Dietary → health-conscious, Quantity → party context, Use Case → snacks/beverages
- Personal Care: Budget → always, Concern → critical, Compatibility → hair/skin type, Ingredient Avoid → sensitive users

**Concrete Examples:**
- "chips for party tonight" → `ASK_USER_BUDGET, ASK_QUANTITY, ASK_DIETARY_REQUIREMENTS, ASK_USER_PREFERENCES`
- "shampoo for my hair" → `ASK_USER_BUDGET, ASK_PC_CONCERN, ASK_PC_COMPATIBILITY, ASK_INGREDIENT_AVOID`

**Message Writing Tips:**
- Reference user's query naturally
- Keep questions 10-15 words max
- Friendly, helpful tone
- Make options feel guided

### 4. Enhanced Tool Schema (line 246)

Added descriptive slot type documentation:
```python
"description": "Slot type: BUDGET (prices), DIETARY (gluten-free/vegan), PREFERENCES (flavor/brand), USE_CASE (daily/party), QUANTITY (servings/people), PC_CONCERN (acne/dandruff), PC_COMPATIBILITY (skin/hair type), INGREDIENT_AVOID (sulfate/paraben-free)"
```

## Testing Scenario

**Original Query:** `"chips for party tonight"`

### Before (Generic Options):
```json
{
  "ASK_QUANTITY": {
    "message": "How many people are you expecting at your party tonight?",
    "options": ["Option 1", "Option 2", "No preference"]  ❌
  }
}
```

### After (Context-Aware Options):
```json
{
  "ASK_QUANTITY": {
    "message": "How many people are you expecting at your party tonight?",
    "options": ["10-20 people", "20-30 people", "30+ people", "Not sure yet"]  ✅
  }
}
```

## Impact

1. **User Experience:** Users now see meaningful, actionable options instead of confusing placeholders
2. **Context Awareness:** System detects party/bulk/personal contexts and adjusts quantity ranges accordingly
3. **Category Intelligence:** Personal care, snacks, and beverages get domain-specific use case options
4. **Preference Matching:** Flavor, texture, or brand preferences based on product category
5. **LLM Guidance:** Prompt engineer-level improvements to guide Claude in selecting the RIGHT questions for each category
6. **Zero Generic Fallbacks:** Even unknown slot types get intelligent, domain-aware fallback options

## Files Modified

- `/Users/priyam_ps/Desktop/shopbot/shopping_bot/llm_service.py`
  - Lines 328-341: New constants
  - Lines 1406-1473: Enhanced enrichment logic
  - Lines 1380-1407: Improved prompt guidance
  - Line 246: Enhanced schema description

## Validation

✅ Linter: No errors
✅ Syntax: All indentation and control flow correct
✅ Coverage: All 8 slot types now handled with meaningful options
✅ Prompt Engineering: Comprehensive guidance with examples and rules

