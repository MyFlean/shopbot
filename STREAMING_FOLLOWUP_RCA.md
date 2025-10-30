# Streaming Follow-up Query RCA

## Issue Summary

After streaming product recommendations successfully, clicking on a follow-up quick reply (e.g., "Under ₹50") was treated as a **new general query** instead of a **product refinement follow-up**. This caused the system to ask "what type of product are you looking for?" instead of refining the chips search.

## Evidence from Logs

### The Smoking Gun

**Line 869** (after streaming final answer):
```
CORE:REDIS_CONVO_DUMP_SAVE | [{"i": 1, "user": "", "bot": ""}]
```

**Line 866**:
```
REDIS_SAVE_SIZES | p=3 | s=10 | f=0 | conv_turns=1 | last_rec_exists=False
```

**Key observations**:
1. ✅ Conversation turn was recorded (`conv_turns=1`)
2. ❌ User query is **EMPTY** string
3. ❌ Bot response is **EMPTY** string  
4. ❌ `last_rec_exists=False` → No product memory stored

### Follow-up Query Behavior

**Lines 647-862** (when user clicked "Under ₹50"):
```
🌊 STREAM_CLASSIFY_START | query='Under ₹50...'
🧠 CONV_HISTORY | turns=0 | last_3_preview=[]
🧠 LAST_RECOMMENDATION | EMPTY (no products in memory)
🔀 STREAM_CLASSIFY_RESULT | route=general | data_strategy=none
```

LLM1 had:
- **No conversation history** to see we were talking about chips
- **No last_recommendation** to know we just showed chip products
- **No context** → Treated "Under ₹50" as isolated, incomplete query

Result: Classified as `route=general`, asked clarification question ❌

## Root Cause Analysis

### Comparison: Non-Streaming vs Streaming Path

**Non-Streaming Path** (`bot_core.py` lines 960-980):
```python
# After generating final answer with products:
1. self._store_last_recommendation(original_q, ctx, fetched)  # ✅
2. snapshot_and_trim(ctx, base_query=original_q, final_answer=...) # ✅
3. ctx.session.pop("assessment", None)  # ✅ Cleanup
4. self.ctx_mgr.save_context(ctx)  # ✅
```

**Streaming Path** (`chat_stream.py` lines 145-257):
```python
# After getting answer_dict from LLM3:
1. Build envelope ✅
2. Send SSE final_answer.complete ✅
3. Send SSE end ✅
4. return ❌ STOPS HERE - NEVER SAVES CONTEXT!
```

### What's Missing in Streaming Path

The streaming endpoint **never calls**:
1. ❌ `bot_core._store_last_recommendation()` → No product memory
2. ❌ `snapshot_and_trim()` → No conversation history
3. ❌ Assessment cleanup → Stale state persists
4. ❌ `ctx_mgr.save_context()` after final answer → Context not persisted

## Why This Happened

The streaming path was implemented to:
1. Run ES fetch independently (✅ works)
2. Stream LLM3 response independently (✅ works)
3. Build envelope and send (✅ works)

But it **bypassed** the standard `bot_core._complete_assessment()` flow which handles:
- Storing last_recommendation
- Saving conversation history
- Cleaning up assessment state

## Impact

Without these critical saves:
- **Follow-up detection fails** → No conversation context
- **Memory-based answering fails** → No product memory
- **Refinement queries fail** → System can't understand "cheaper", "gluten free", etc.
- **Assessment state pollutes** → Stale "asking" phase may persist

## Solution Thesis

### Step 1: Identify What Needs Saving (CRITICAL)

After streaming final answer completes, we must:
1. **Store last_recommendation** with products from ES results
2. **Save conversation history** with (original_query, summary_message)
3. **Clean up assessment state** (mark phase=done, remove contextual_questions)
4. **Persist context to Redis** with all updates

### Step 2: Where to Add the Logic

**Option A** (Recommended): In `chat_stream.py` after answer_dict is received
- Pro: Mirrors non-streaming flow exactly
- Pro: All data available (original_query, answer_dict, fetched)
- Con: Duplicates some logic from bot_core

**Option B**: Create a post-streaming hook in bot_core
- Pro: Centralizes logic
- Con: Requires refactoring bot_core internals

**Decision**: Go with Option A for minimal risk

### Step 3: Implementation Plan

In `chat_stream.py`, after receiving `answer_dict` from streaming (line ~222):

```python
# After answer_dict arrives from queue
answer_dict = payload

# CRITICAL: Save conversation context (mirrors bot_core._complete_assessment)
try:
    # 1. Store last_recommendation
    bot_core._store_last_recommendation(original_query, ctx, fetched)
    
    # 2. Save conversation history
    from ..bot_helpers import snapshot_and_trim
    final_answer_summary = {
        "response_type": answer_dict.get("response_type", "final_answer"),
        "message_preview": answer_dict.get("summary_message", "")[:300],
        "has_products": True,
        "ux_intent": ctx.session.get("product_intent"),
        "message_full": answer_dict.get("summary_message", ""),
        "data_source": "es_fetch"
    }
    snapshot_and_trim(ctx, base_query=original_query, final_answer=final_answer_summary)
    
    # 3. Cleanup assessment
    ctx.session.pop("assessment", None)
    ctx.session.pop("contextual_questions", None)
    
    # 4. Save to Redis
    ctx_mgr.save_context(ctx)
    
    log.info(f"STREAMING_CONTEXT_SAVED | query='{original_query[:60]}' | products={len(productIds)}")
except Exception as save_exc:
    log.error(f"STREAMING_CONTEXT_SAVE_FAILED | error={save_exc}")
```

### Step 4: Testing Criteria

After fix, verify:
1. ✅ Conversation history saved with actual text (not empty strings)
2. ✅ `last_rec_exists=True` in Redis save logs
3. ✅ Follow-up query "Under ₹50" classified as follow-up/refinement
4. ✅ ES params use conversation context to understand "Under ₹50" → chips price filter

## Expected Log Sequence After Fix

```
# After streaming completes:
🧠 STORE_LAST_REC_START | query='want some chips' | has_search_products=True
🧠 STORE_LAST_REC_PRODUCTS | total_products=20 | will_snapshot=8
✅ STORE_LAST_REC_SUCCESS | products_stored=8
CORE:HIST_WRITE | count=1 | last_user='want some chips'
💾 REDIS_SAVE_SIZES | conv_turns=1 | last_rec_exists=True
CORE:REDIS_CONVO_DUMP_SAVE | [{"i": 1, "user": "want some chips", "bot": "Looking for chips..."}]
STREAMING_CONTEXT_SAVED | query='want some chips' | products=3

# Next query "Under ₹50":
🧠 CONV_HISTORY | turns=1 | last_3_preview=[{user: "want some chips", bot: "Looking for chips..."}]
🧠 LAST_RECOMMENDATION | query='want some chips' | products_count=8
🔀 STREAM_CLASSIFY_RESULT | route=product | data_strategy=es_fetch ✅ CORRECT!
```

## Files to Modify

1. **`shopping_bot/routes/chat_stream.py`** (lines ~215-235)
   - Add conversation saving logic after answer_dict received
   - Import snapshot_and_trim, call _store_last_recommendation
   - Clean up assessment state

2. **`shopping_bot/bot_core.py`** (no changes needed)
   - Already has the logic we'll call

---

**Status**: Root cause identified, solution designed
**Next**: Implement the fix in chat_stream.py

