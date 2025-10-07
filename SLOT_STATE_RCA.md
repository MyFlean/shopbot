# Root Cause Analysis: Slot Value State Management

**Date**: 2025-10-07  
**Issue**: Potential stale constraint pollution across product queries

---

## **Problem Statement**

You raised critical questions:
1. **Follow-up scenario**: If user adds new dietary requirement, does it merge or replace old?
2. **New query scenario**: If user doesn't mention dietary this time, do old values pollute results?

---

## **Current Architecture Analysis**

### **1. Slot Storage Flow**

**Initial Capture (Assessment Flow):**
```
User: "chips"
Bot asks: "Any dietary requirements?"
User: "vegan"
→ store_user_answer() called
→ ctx.session["dietary_requirements"] = "vegan"
→ ctx.permanent["user_answers"]["ASK_DIETARY_REQUIREMENTS"] = {value, timestamp, session_id}
```

**Code Location**: `bot_helpers.py:200-221`
```python
def store_user_answer(text: str, assessment: Dict[str, Any], ctx: UserContext) -> None:
    slot = UserSlot(target)
    session_key = SLOT_TO_SESSION_KEY.get(slot, ...)
    ctx.session[session_key] = text  # OVERWRITES
    ctx.permanent.setdefault("user_answers", {})[target] = {...}  # SNAPSHOTS
```

---

### **2. LLM2 Overwrite Issue** ⚠️

**Location**: `llm_service.py:3186-3187`
```python
if params.get("dietary_terms"):
    ctx.session["dietary_requirements"] = params.get("dietary_terms")  # OVERWRITES
```

**Scenario - FOLLOW-UP (User Adds Constraint):**
```
Turn 1: User: "vegan chips"
  → session["dietary_requirements"] = "vegan" (from user)

Turn 2: User: "under 100"
  → LLM2 reads session (has "vegan")
  → LLM2 suggests: dietary_terms = ["VEGAN", "LOW SODIUM"] (health-first)
  → Overwrites: session["dietary_requirements"] = ["VEGAN", "LOW SODIUM"]
  → Result: MERGE ✓ (but accidental, depends on LLM remembering)
```

**Risk**: If LLM2 forgets user's "vegan", it gets lost!

---

### **3. New Query Pollution Issue** ⚠️⚠️

**Location**: `bot_core.py:534-541`
```python
async def _start_new_assessment(self, query: str, ctx: UserContext) -> BotResponse:
    # ...
    # Seed canonical query for this assessment and clear stale planner hints
    ctx.session["canonical_query"] = query
    ctx.session["last_query"] = query
    dbg = ctx.session.setdefault("debug", {})
    dbg["last_search_params"] = {}  # CLEARED ✓
    # BUT dietary_requirements/preferences NOT cleared! ❌
```

**Scenario - NEW QUERY (User Switches Product):**
```
Session 1: User: "vegan chips under 100"
  → session["dietary_requirements"] = ["VEGAN"]
  → session["price_max"] = 100
  → Results: vegan chips ✓

Session 1 (continued): User: "show me pasta"
  → NEW query detected (not follow-up)
  → _start_new_assessment() called
  → debug.last_search_params cleared ✓
  → BUT session["dietary_requirements"] = ["VEGAN"] PERSISTS ❌
  → LLM2 reads session, sees ["VEGAN"]
  → Pasta results filtered for VEGAN (UNINTENDED!)
```

**Current Behavior**: Slots bleed across product boundaries!

---

### **4. Reset Logic Analysis**

**When session IS cleared**: `_reset_session_only()`
```python
def _reset_session_only(self, ctx: UserContext) -> None:
    ctx.session.clear()  # FULL CLEAR
    ctx.fetched_data.clear()
```

**When it's called**:
- `fu.patch.reset_context == True` (explicit LLM signal to reset)
- Never called automatically on product switch

---

## **Root Causes Identified**

### **RC1: LLM2 Overwrites Instead of Merging User Slots**
- **Location**: `llm_service.py:3186-3193`
- **Issue**: Each LLM2 call replaces `dietary_requirements`, `brands`, etc.
- **Risk**: User-explicit values lost if LLM doesn't recall them from history

### **RC2: New Assessment Doesn't Clear Product-Specific Slots**
- **Location**: `bot_core.py:534-541`
- **Issue**: Clears `debug.last_search_params` but not `dietary_requirements`, `preferences`, `brands`
- **Risk**: Constraints from chips search pollute pasta search

### **RC3: No Distinction Between User-Provided vs LLM-Suggested**
- **Issue**: Both stored in same `session["dietary_requirements"]` key
- **Risk**: Can't tell if "LOW SODIUM" was user intent or LLM suggestion

### **RC4: Permanent Storage Snapshot, But No Merge Logic**
- **Location**: `bot_helpers.py:217-221`
- **Issue**: Snapshots each answer to `permanent["user_answers"]`, but not used for merge
- **Risk**: Historical user preferences stored but not leveraged for merging

---

## **Impact Assessment**

### **Follow-Up Scenarios**

| User Flow | Current Behavior | Correct Behavior | Status |
|-----------|-----------------|------------------|--------|
| User: "vegan chips" → "under 100" | LLM2 recalls "vegan" + adds "LOW SODIUM" → ["VEGAN", "LOW SODIUM"] | Merge user + health suggestion | ⚠️ Fragile (depends on LLM memory) |
| User: "chips" → Bot suggests LOW SODIUM → User: "no palm oil" | LLM2 might keep or drop LOW SODIUM | Merge: ["LOW SODIUM", "PALM OIL FREE"] | ❌ Inconsistent |
| User: "vegan chips" → "make it gluten free too" | LLM2 should merge → ["VEGAN", "GLUTEN FREE"] | Merge both user constraints | ⚠️ Fragile |

### **New Query Scenarios**

| User Flow | Current Behavior | Correct Behavior | Status |
|-----------|-----------------|------------------|--------|
| "vegan chips" → "show me pasta" | Pasta results FILTERED for vegan (stale!) | Clear dietary for new product | ❌ CRITICAL BUG |
| "chips under 50" → "want juice" | Juice filtered to ₹50 (stale!) | Clear price for new product | ❌ CRITICAL BUG |
| "Lays chips" → "ketchup" | Ketchup might inherit stale brand | Clear brand for new product | ❌ CRITICAL BUG |

---

## **Proposed Solutions**

### **Option A: Aggressive Clearing (Safe, Simple)**
- **When**: Every new assessment (product switch)
- **What**: Clear `dietary_requirements`, `preferences`, `brands`, `price_min`, `price_max`
- **Keep**: Only `budget` (user-level permanent)
- **Pro**: No stale pollution
- **Con**: User must re-specify dietary for each product (UX friction)

### **Option B: Smart Merge with Provenance Tracking (Complex, Better UX)**
- **When**: Every LLM2 call
- **What**: Separate `dietary_requirements_user` vs `dietary_requirements_suggested`
- **Merge**: Union for same product; clear suggested on product switch
- **Pro**: Preserves user intent, clears suggestions
- **Con**: More complex state management

### **Option C: Assessment-Scoped Slots (Middle Ground)**
- **When**: Store slots under `assessment["answers"]` instead of top-level session
- **Clear**: When assessment completes or new one starts
- **Preserve**: Only `permanent["user_answers"]` for long-term profile
- **Pro**: Automatic scoping, simple
- **Con**: Breaks existing session key access patterns

### **Option D: Hybrid - Smart Clear on Product Switch**
- **When**: `_start_new_assessment()` detects product category change
- **What**: Clear product-specific slots (dietary, brands); keep budget
- **How**: Compare `last_search_params.category_group` vs new query intent
- **Pro**: Balanced - clears when needed, preserves when same category
- **Con**: Requires category detection before assessment

---

## **Recommendation**

**Implement Option A + Partial Option B**

### **Phase 1 (Immediate Fix)**:
1. Clear product-specific slots in `_start_new_assessment()`:
   ```python
   # Clear stale product constraints
   slots_to_clear = ["dietary_requirements", "preferences", "brands", "price_min", "price_max"]
   for slot in slots_to_clear:
       ctx.session.pop(slot, None)
   ```

2. Preserve user-explicit dietary in assessment answers:
   ```python
   # In assessment flow, track which slots were USER-PROVIDED
   assessment["user_provided_slots"] = ["dietary_requirements"]  # if user answered
   ```

3. LLM2 merge logic:
   ```python
   # Merge user-provided + LLM-suggested
   user_dietary = assessment.get("user_provided_dietary", [])
   llm_dietary = params.get("dietary_terms", [])
   merged = list(set(user_dietary + llm_dietary))
   ctx.session["dietary_requirements"] = merged
   ```

### **Phase 2 (Future Enhancement)**:
- Split into `dietary_requirements_user` and `dietary_requirements_ai`
- Union for ES query building
- Clear only AI suggestions on new assessment

---

## **Testing Scenarios**

```python
# Scenario 1: Follow-up with new constraint
"vegan chips" → "also gluten free" 
Expected: ["VEGAN", "GLUTEN FREE"]
Current: Depends on LLM memory ⚠️

# Scenario 2: New product
"vegan chips" → "show me pasta"
Expected: pasta (no vegan filter)
Current: pasta filtered for vegan ❌

# Scenario 3: LLM suggestion preservation
"chips" → [LLM suggests "LOW SODIUM"] → "under 50"
Expected: Keep LOW SODIUM (same product)
Current: Depends on LLM memory ⚠️

# Scenario 4: LLM suggestion clearing
"chips" → [LLM suggests "LOW SODIUM"] → "want juice"
Expected: Juice (no sodium constraint)
Current: Juice filtered for low sodium ❌
```

---

## **Files Requiring Changes**

1. `shopping_bot/bot_core.py:534-541` - Add slot clearing in `_start_new_assessment`
2. `shopping_bot/llm_service.py:3186-3193` - Change overwrite to merge logic
3. `shopping_bot/bot_helpers.py:200-221` - Track user-provided vs inferred in assessment
4. `shopping_bot/enhanced_bot_core.py:222-270` - Mirror changes in enhanced core

---

## **Severity**

**CRITICAL** - This affects core product search quality and can:
- Show irrelevant results (vegan pasta when user didn't ask)
- Hide valid results (filter too aggressively)
- Create confusing UX (constraints appearing from nowhere)

---

## **Next Steps**

1. Implement Phase 1 fixes (clear on new assessment + merge user values)
2. Add unit tests for cross-product pollution
3. Monitor Redis session state in production
4. Consider Phase 2 for v2 (provenance tracking)

