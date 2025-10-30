# Streaming ASK Flow ES Query Fix

## Issue Summary

When the ASK flow completed in the streaming endpoint and proceeded to search for products, the Elasticsearch query was being built with `<UNKNOWN>` as the query field instead of using the original user query combined with the collected slot answers.

## Root Cause

The issue occurred in `/shopping_bot/routes/chat_stream.py` at line 133 (before the fix):

```python
bot_resp = asyncio.run(bot_core.process_query(message, ctx))
```

Where `message` was the last answer from the user (e.g., "Under ₹50"), NOT the original query.

### The Problem Chain:

1. **User asks**: "want some chips"
2. **System asks**: "What type of chips?" → User answers: "Potato chips"
3. **System asks**: "What's your budget?" → User answers: "Under ₹50"
4. **ASK completes**, chat_stream.py calls `bot_core.process_query("Under ₹50", ctx)`
5. **Critical missing step**: Unlike the non-streaming path (`chat.py` lines 298-308), the streaming path did NOT set `ctx.current_user_text` before calling `bot_core.process_query`
6. **ES parameter extraction** tries to get `current_text` from:
   - `ctx.current_user_text` (not set → empty)
   - `ctx.session["current_user_text"]` (not set → empty)
   - `ctx.session["last_user_message"]` (not set → empty)
7. **Result**: `current_text` is empty, LLM can't construct a query, returns `<UNKNOWN>`

## The Fix

In `/shopping_bot/routes/chat_stream.py`, lines 118-146:

```python
else:
    # All questions answered - proceed to ES fetch
    log.info(f"ASK_COMPLETE | all_slots_filled | proceeding_to_search")
    assessment["phase"] = "complete"
    assessment["currently_asking"] = None
    ctx_mgr.save_context(ctx)
    
    # Signal frontend: ASK phase done
    log.info(f"SSE_EMIT | event=ask_complete | session={session_id}")
    yield _sse_event("ask_complete", {"message": "Got it! Searching for products..."})
    log.info(f"SSE_EMIT | event=status | stage=product_search | session={session_id}")
    yield _sse_event("status", {"stage": "product_search"})
    
    # Now run product search with all collected information
    # CRITICAL: Set ctx.current_user_text to the original query (not the last answer)
    # so that ES parameter extraction uses the right query context
    original_query = assessment.get("original_query", message)
    try:
        setattr(ctx, "current_user_text", original_query)
        setattr(ctx, "message_text", original_query)
        ctx.session["current_user_text"] = original_query
        ctx.session["last_user_message"] = original_query
        ctx.session.setdefault("debug", {})["current_user_text"] = original_query
        log.info(f"ASK_COMPLETE_SET_ORIGINAL_QUERY | original_query='{original_query[:80]}'")
    except Exception as ctx_exc:
        log.warning(f"ASK_COMPLETE_SET_QUERY_FAILED | error={ctx_exc}")
    
    log.info(f"RUNNING_PRODUCT_SEARCH | with_collected_slots")
    bot_resp = asyncio.run(bot_core.process_query(original_query, ctx))
```

### What Changed:

1. **Line 134**: Extract `original_query` from the assessment (e.g., "want some chips")
2. **Lines 136-141**: Set all context attributes that ES extraction expects:
   - `ctx.current_user_text`
   - `ctx.message_text`
   - `ctx.session["current_user_text"]`
   - `ctx.session["last_user_message"]`
   - `ctx.session["debug"]["current_user_text"]`
3. **Line 146**: Pass `original_query` to `bot_core.process_query` (not `message`)

## How It Works Now

1. **User asks**: "want some chips" → Stored as `assessment["original_query"]`
2. **System collects slots**: preferences, budget, etc.
3. **ASK completes**: 
   - Sets `ctx.current_user_text = "want some chips"` (original query)
   - Calls `bot_core.process_query("want some chips", ctx)`
4. **ES parameter extraction**:
   - Gets `current_text = "want some chips"` from `ctx.current_user_text`
   - Accesses collected slot answers from `ctx.session["budget"]`, `ctx.session["preferences"]`, etc.
   - LLM constructs proper query: "potato chips" with budget filter ≤50
5. **Result**: ES query is built correctly! ✅

## Testing

To verify the fix:

1. Start a conversation: "I want some chips"
2. Answer ASK questions: "Potato chips", "Under ₹50"
3. Check logs for:
   ```
   ASK_COMPLETE_SET_ORIGINAL_QUERY | original_query='want some chips'
   CONSTRUCT_QUERY_OUTPUT | q='potato chips'  # (or similar, NOT <UNKNOWN>)
   ```

## Related Code Paths

This fix mirrors how the non-streaming path handles this in `/shopping_bot/routes/chat.py` lines 298-308:

```python
# Inject CURRENT user text directly into ctx so downstream ES/LLM always see it
try:
    setattr(ctx, "current_user_text", message)
    setattr(ctx, "message_text", message)
    ctx.session = ctx.session or {}
    ctx.session["current_user_text"] = message
    ctx.session["last_user_message"] = message
    ctx.session.setdefault("debug", {})["current_user_text"] = message
    log.info(f"INGRESS_SET_CURRENT_TEXT | user={user_id} | text='{message[:80]}'")
except Exception as _ing_exc:
    log.warning(f"INGRESS_SET_CURRENT_TEXT_FAILED | user={user_id} | error={_ing_exc}")
```

## Key Learnings

1. **Context propagation is critical**: ES parameter extraction relies on `ctx.current_user_text` to understand what the user wants
2. **ASK answers vs. original query**: When ASK completes, the system needs the ORIGINAL query (e.g., "want chips"), not the last answer (e.g., "Under ₹50")
3. **Streaming vs. non-streaming parity**: Both paths must set the same context attributes for consistent behavior
4. **Fallback chain**: ES extraction has multiple fallbacks, but if ALL are empty, it fails with `<UNKNOWN>`

## Files Modified

- `/Users/priyam_ps/Desktop/shopbot/shopping_bot/routes/chat_stream.py` (lines 118-146)

---

**Date**: 2025-10-30
**Status**: Fixed ✅

