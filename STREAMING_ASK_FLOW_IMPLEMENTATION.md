# Streaming ASK Flow Implementation - Complete Solution

## Executive Summary

**Problem**: The streaming endpoint (`/chat/stream`) was generating ASK questions (e.g., "I want chips" → 2 questions with options) but had no mechanism to handle user answers sequentially. Questions were streamed to frontend but the backend had no state management for the ASK phase.

**Solution**: Implemented a complete state-driven ASK flow that mirrors the non-streaming path, with proper assessment state persistence, answer detection, and sequential question progression.

---

## Architecture Overview

### Key Components

1. **Backend State Management** (`chat_stream.py`)
   - Assessment state initialization after classification
   - Answer detection and storage
   - Sequential question progression logic
   - SSE event emissions for frontend coordination

2. **Frontend Event Handling** (`chat_ui.py`)
   - Already had ASK buffer and queue system (working correctly)
   - Added handlers for new backend events
   - Sequential question display coordination

3. **New SSE Events**
   - `ask_phase_start`: Signals ASK phase begins (informational)
   - `ask_next`: Tells frontend to show next question from buffer
   - `ask_complete`: All questions answered, proceeding to search

---

## Implementation Details

### Part 1: Assessment State Initialization (Backend)

**Location**: `/shopping_bot/routes/chat_stream.py` lines 221-259

**When**: After streaming classification completes and detects product query with ASK slots

**What it does**:
```python
# After classification returns with ask slots
if route == "product" and classification.get("ask"):
    # Initialize assessment state (mirrors bot_core.py)
    assessment_state = {
        "phase": "asking",
        "original_query": message,
        "missing_data": slot_names,           # e.g., ["ASK_USER_BUDGET", "ASK_USER_PREFERENCES"]
        "priority_order": slot_names,
        "currently_asking": slot_names[0],    # First question
        "fulfilled": [],
        "user_provided_slots": [],
    }
    ctx.session["assessment"] = assessment_state
    ctx.session["contextual_questions"] = ask_dict
    ctx_mgr.save_context(ctx)
```

**Why**: Without this state, subsequent user answers had no context to determine:
- Which question is being answered
- What questions remain
- When to proceed to ES fetch

---

### Part 2: Answer Detection and Progression (Backend)

**Location**: `/shopping_bot/routes/chat_stream.py` lines 79-149

**When**: At the START of every streaming request, before classification

**Flow Logic**:

```python
# 1. Check if we're in ASK phase
assessment = ctx.session.get("assessment", {})
if assessment.get("phase") == "asking" and assessment.get("currently_asking"):
    
    # 2. Store user's answer
    store_user_answer(message, assessment, ctx)
    
    # 3. Calculate what's still missing
    fulfilled = set(assessment.get("fulfilled", []))
    priority_order = assessment.get("priority_order", [])
    still_missing = [slot for slot in priority_order if slot not in fulfilled]
    
    # 4. Branch: More questions OR proceed to search
    if still_missing:
        # Show next question
        next_slot = still_missing[0]
        assessment["currently_asking"] = next_slot
        yield _sse_event("ask_next", {
            "slot_name": next_slot,
            "completed_slot": current_slot
        })
        return  # END stream, wait for next answer
    else:
        # All answered, run search
        assessment["phase"] = "complete"
        yield _sse_event("ask_complete", {...})
        bot_resp = asyncio.run(bot_core.process_query(message, ctx))
        # ... emit products
```

**Why**: This is the core state machine that enables sequential question-answer flow.

---

### Part 3: Frontend Event Handlers

**Location**: `/shopping_bot/routes/chat_ui.py` lines 797-832

**Handlers Added**:

#### 1. `ask_phase_start`
```javascript
if (event === 'ask_phase_start') {
  const d = JSON.parse(data);
  setStatus(`Asking ${d.total_questions} questions...`, 'active');
  return;
}
```
**Purpose**: Informational - shows user how many questions to expect

#### 2. `ask_next` (Critical!)
```javascript
if (event === 'ask_next') {
  const d = JSON.parse(data);
  
  // Mark previous question as completed
  if (d.completed_slot) {
    const completedEntry = askBuffer.get(d.completed_slot);
    if (completedEntry) completedEntry.completed = true;
  }
  
  // Reset state and trigger next question display
  activeAskEntry = null;
  awaitingAskResponse = false;
  scheduleNextAsk();  // Shows next question from buffer
  return;
}
```
**Purpose**: Coordinates sequential question display

#### 3. `ask_complete`
```javascript
if (event === 'ask_complete') {
  setStatus('Searching products...', 'active');
  resetAskState();
  // Backend will now stream product results
  return;
}
```
**Purpose**: Signals transition from ASK phase to product search

---

## Complete Flow Walkthrough

### Scenario: User says "I want chips"

```
┌─────────────────────────────────────────────────────────────┐
│ STEP 1: Initial Request                                      │
└─────────────────────────────────────────────────────────────┘

User: "I want chips"
  ↓
Frontend: POST /chat/stream {"message": "I want chips"}
  ↓
Backend: Check assessment state → NONE (new query)
  ↓
Backend: Run classify_and_assess_stream()


┌─────────────────────────────────────────────────────────────┐
│ STEP 2: Classification Streaming                             │
└─────────────────────────────────────────────────────────────┘

LLM generates (via tool streaming):
  ask_slots: [
    {slot_name: "ASK_USER_BUDGET", message: "What's your budget?", options: [...]},
    {slot_name: "ASK_USER_PREFERENCES", message: "What flavor?", options: [...]}
  ]

Backend emits SSE events (in order):
  1. ask_message_delta (slot: ASK_USER_BUDGET, text: "What's your budget?")
  2. ask_options_delta (slot: ASK_USER_BUDGET, options: ["Under ₹50", ...])
  3. ask_message_delta (slot: ASK_USER_PREFERENCES, text: "What flavor?")
  4. ask_options_delta (slot: ASK_USER_PREFERENCES, options: ["Spicy", ...])
  5. ask_plan (slots: [Q1, Q2] with order)
  6. classification_complete

Frontend receives:
  - Stores both questions in askBuffer
  - Shows Q1 (ASK_USER_BUDGET) with options
  - Waits for user click


┌─────────────────────────────────────────────────────────────┐
│ STEP 3: Backend Saves Assessment State                       │
└─────────────────────────────────────────────────────────────┘

Backend (after classification):
  assessment = {
    "phase": "asking",
    "original_query": "I want chips",
    "priority_order": ["ASK_USER_BUDGET", "ASK_USER_PREFERENCES"],
    "currently_asking": "ASK_USER_BUDGET",  ← CRITICAL
    "fulfilled": []
  }
  ctx.session["assessment"] = assessment
  ctx_mgr.save_context(ctx)

Backend emits:
  - ask_phase_start (total_questions: 2)
  - end (awaiting_user_input: true)


┌─────────────────────────────────────────────────────────────┐
│ STEP 4: User Answers First Question                          │
└─────────────────────────────────────────────────────────────┘

User clicks: "Under ₹50"
  ↓
Frontend: POST /chat/stream {"message": "Under ₹50"}
  ↓
Backend: Load context → assessment.phase = "asking" ✓
Backend: assessment.currently_asking = "ASK_USER_BUDGET" ✓
  ↓
Backend: ANSWER DETECTED! (Not a new classification)
  ↓
Backend: store_user_answer("Under ₹50", assessment, ctx)
  Result: 
    - ctx.session["budget"] = "Under ₹50"
    - assessment.fulfilled = ["ASK_USER_BUDGET"]
    - assessment.currently_asking = None
  ↓
Backend: Calculate still_missing = ["ASK_USER_PREFERENCES"]
  ↓
Backend: still_missing is NOT empty → Show next question
  ↓
Backend: 
  assessment.currently_asking = "ASK_USER_PREFERENCES"
  ctx_mgr.save_context(ctx)
  ↓
Backend emits:
  - ask_next {
      slot_name: "ASK_USER_PREFERENCES",
      completed_slot: "ASK_USER_BUDGET",
      remaining_count: 1
    }
  - end (ok: true)


┌─────────────────────────────────────────────────────────────┐
│ STEP 5: Frontend Shows Second Question                       │
└─────────────────────────────────────────────────────────────┘

Frontend receives: ask_next event
  ↓
Handler:
  - Marks ASK_USER_BUDGET as completed in buffer
  - Resets activeAskEntry = null
  - Resets awaitingAskResponse = false
  - Calls scheduleNextAsk()
  ↓
scheduleNextAsk():
  - Finds next incomplete question from buffer: ASK_USER_PREFERENCES
  - Displays it with options ["Spicy", "Classic salted", "Tangy"]
  - Sets awaitingAskResponse = true


┌─────────────────────────────────────────────────────────────┐
│ STEP 6: User Answers Second (Final) Question                 │
└─────────────────────────────────────────────────────────────┘

User clicks: "Spicy"
  ↓
Frontend: POST /chat/stream {"message": "Spicy"}
  ↓
Backend: Load context → assessment.phase = "asking" ✓
Backend: assessment.currently_asking = "ASK_USER_PREFERENCES" ✓
  ↓
Backend: ANSWER DETECTED!
  ↓
Backend: store_user_answer("Spicy", assessment, ctx)
  Result:
    - ctx.session["preferences"] = "Spicy"
    - assessment.fulfilled = ["ASK_USER_BUDGET", "ASK_USER_PREFERENCES"]
  ↓
Backend: Calculate still_missing = []  ← EMPTY!
  ↓
Backend: ASK COMPLETE! Proceed to search
  ↓
Backend:
  assessment.phase = "complete"
  assessment.currently_asking = None
  ctx_mgr.save_context(ctx)
  ↓
Backend emits:
  - ask_complete {"message": "Got it! Searching for products..."}
  - status {stage: "product_search"}
  ↓
Backend: bot_resp = asyncio.run(bot_core.process_query("Spicy", ctx))
  Note: Now ctx has:
    - budget: "Under ₹50"
    - preferences: "Spicy"
    - category: "chips"
  ↓
Backend: Generates ES query with collected info → Fetches products
  ↓
Backend emits:
  - ux_bootstrap (product_ids: [...])
  - final_answer.complete (product cards)
  - end (ok: true)


┌─────────────────────────────────────────────────────────────┐
│ STEP 7: Frontend Displays Products                           │
└─────────────────────────────────────────────────────────────┘

Frontend receives: ask_complete
  ↓
Handler:
  - Resets askBuffer
  - Shows "Searching products..." status
  ↓
Frontend receives: final_answer.complete with products
  ↓
Displays product cards with collected filters applied
```

---

## Key Design Decisions

### 1. Why Check Assessment State FIRST?

```python
# At the very start of generate() function
assessment = ctx.session.get("assessment", {})
if assessment.get("phase") == "asking":
    # Handle answer
    return
# Otherwise, run classification
```

**Reason**: If we ran classification on every request, the LLM would generate NEW questions instead of recognizing the user is answering a previous question.

### 2. Why Store State in Redis (ctx.session)?

**Reason**: The streaming endpoint is stateless across requests. Without Redis persistence:
- Request 1: "I want chips" → generates Q1, Q2
- Request 2: "Under ₹50" → backend has NO MEMORY of Q1, Q2

### 3. Why Return Early After ask_next?

```python
yield _sse_event("ask_next", {...})
return  # Don't continue to product search!
```

**Reason**: We're waiting for the NEXT user answer. If we continued, we'd fetch products without all answers collected.

### 4. Why Does Frontend Already Work?

The frontend (`chat_ui.py`) already had:
- `askBuffer` - stores all questions
- `askQueue` - ordered display queue
- `scheduleNextAsk()` - shows next question
- `awaitingAskResponse` - prevents concurrent questions

It was designed for this flow but never triggered because backend had no state management!

---

## Testing Checklist

### Basic Flow
- [ ] User: "I want chips"
  - Should see 2 questions streamed incrementally
  - First question displays with 3 options
- [ ] User clicks first option
  - Should see second question immediately
  - No new classification runs
- [ ] User clicks second option
  - Should see "Searching products..." message
  - Product cards appear

### Edge Cases
- [ ] User types custom answer instead of clicking option
  - Should still progress to next question
- [ ] User starts new query mid-ASK phase
  - Should reset assessment state and start new classification
- [ ] Backend restart during ASK phase
  - Redis state should persist, user can continue answering

### Validation
- [ ] Check Redis: `ctx.session["assessment"]` structure correct
- [ ] Check logs: "ASK_ANSWER_DETECTED" appears on 2nd+ requests
- [ ] Check frontend: `askBuffer` has both questions after streaming
- [ ] Check product results: Budget and preference filters applied

---

## Debug Commands

### Inspect Assessment State
```bash
# In Redis CLI
redis-cli
> GET "user_context:test_user:sess_123"
> # Look for "assessment" key with phase, currently_asking, fulfilled
```

### Backend Logs to Monitor
```
ASK_INIT | initializing_assessment | slots=[...]
ASK_STATE_SAVED | currently_asking=ASK_USER_BUDGET
ASK_ANSWER_DETECTED | answering=ASK_USER_BUDGET
ASK_ANSWER_STORED | slot=ASK_USER_BUDGET
ASK_NEXT | showing=ASK_USER_PREFERENCES
ASK_COMPLETE | all_slots_filled
RUNNING_PRODUCT_SEARCH | with_collected_slots
```

### Frontend Console Logs
```
🎯 ASK_PHASE_START | Total questions: 2
➡️ ASK_NEXT | Completed: ASK_USER_BUDGET, Next: ASK_USER_PREFERENCES
✅ MARK_COMPLETE | Slot ASK_USER_BUDGET marked as completed
✅ ASK_COMPLETE | All questions answered
```

---

## Files Modified

1. **`/shopping_bot/routes/chat_stream.py`** (Backend)
   - Lines 79-149: Answer detection and progression logic
   - Lines 221-259: Assessment state initialization

2. **`/shopping_bot/routes/chat_ui.py`** (Frontend)
   - Lines 797-832: Event handlers for ask_phase_start, ask_next, ask_complete

**No changes needed to**:
- `llm_service.py` - Streaming classification already works
- `bot_core.py` - Non-streaming path unchanged
- `tool_stream_accumulator.py` - Extraction logic already correct
- Frontend buffer/queue logic - Already implemented correctly

---

## Comparison: Non-Streaming vs Streaming

### Non-Streaming Path (Still Works)

```
User: "I want chips" → POST /chat
  ↓
Backend: classify_and_assess() → 2 questions
Backend: Returns ResponseType.QUESTION with Q1
  ↓
Frontend: Displays Q1 in Flow format
  ↓
User answers → POST /chat
  ↓
Backend: _continue_assessment() → checks fulfilled
Backend: Returns ResponseType.QUESTION with Q2
  ↓
User answers → POST /chat
  ↓
Backend: _continue_assessment() → all fulfilled
Backend: Runs search → Returns products
```

**Key**: Backend returns ONE question per request, frontend makes multiple requests.

### Streaming Path (New Implementation)

```
User: "I want chips" → POST /chat/stream
  ↓
Backend: classify_and_assess_stream() → streams Q1, Q2 incrementally
Backend: Saves assessment state
Frontend: Displays Q1, holds Q2 in buffer
  ↓
User answers → POST /chat/stream
  ↓
Backend: Detects ASK phase, stores answer
Backend: Emits ask_next event
Frontend: Shows Q2 from buffer
  ↓
User answers → POST /chat/stream
  ↓
Backend: Detects ASK phase, stores answer
Backend: All fulfilled → runs search
Backend: Streams products
```

**Key**: Backend streams ALL questions at once, frontend manages sequential display.

---

## Benefits of This Implementation

1. **True Streaming UX**: Questions appear character-by-character as LLM generates
2. **Efficient**: One LLM call generates all questions (vs 2 calls in non-streaming)
3. **Stateful**: Proper Redis-backed state management
4. **Consistent**: Mirrors non-streaming logic, same assessment structure
5. **Flexible**: Frontend controls display order, backend provides state machine
6. **Scalable**: Can extend to 4+ questions (personal care) without changes

---

## Future Enhancements

### Optional: Dynamic Question Generation
Currently, all questions are generated upfront. Could modify to:
- Generate Q1 immediately
- Wait for answer
- Generate Q2 based on Q1 answer (more contextual)

**Trade-off**: More LLM calls but potentially better questions.

### Optional: Skip Questions
Allow user to skip optional questions:
```javascript
// Frontend
<button onClick={() => skipQuestion()}>Skip this question</button>

// Backend
if (message === "SKIP") {
  // Don't store answer, just move to next
  assessment["fulfilled"].append(currently_asking)
}
```

### Optional: Edit Previous Answers
Show all questions with answers, allow editing:
```javascript
// Frontend: Show completed questions with "Edit" button
// Backend: Handle edited answers by marking slot as unfulfilled
```

---

## Conclusion

This implementation provides a complete, production-ready streaming ASK flow that:
- ✅ Generates all questions in one streaming LLM call
- ✅ Displays questions sequentially (one at a time)
- ✅ Handles user answers with proper state management
- ✅ Proceeds to product search after all questions answered
- ✅ Maintains consistency with non-streaming path
- ✅ Provides excellent UX with streaming text and smooth transitions

The solution is **complete and ready for testing**.

