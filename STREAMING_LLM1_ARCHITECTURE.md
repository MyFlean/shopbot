# LLM1 Streaming Architecture Analysis & Solution Thesis

## Executive Summary

The fast 3-LLM pipeline's first LLM call (`classify_and_assess`) produces **dual-purpose outputs**:
1. **User-facing content** → ASK slot messages, simple_response text (streamed to user)
2. **Internal state** → route, domain, category, product_intent, data_strategy, ask slots structure (saved to Redis/session)

**Current Problem**: LLM1 uses tool-calling but doesn't stream the response. The complete JSON blob arrives at once, making real-time UX feedback impossible.

**Solution**: Leverage Anthropic's **native streaming tool-use support** via `input_json_delta` events. Accumulate tool arguments progressively, extract user-facing strings incrementally, and parse the complete payload at `content_block_stop`.

---

## Part 1: Deep Analysis of LLM1 Output Categories

### 1.1 User-Facing Outputs (MUST STREAM)

These are sent to the frontend and benefit from streaming UX:

#### A. ASK Slot Messages
```python
"ask_slots": [
    {
        "slot_name": "ASK_USER_BUDGET",
        "message": "Great! What's your budget range for chips?",  # ← STREAM THIS
        "options": ["Under ₹50", "₹50–150", "Over ₹150"]
    },
    {
        "slot_name": "ASK_USER_PREFERENCES",
        "message": "What flavor are you in the mood for?",  # ← STREAM THIS
        "options": ["Spicy", "Classic salted", "Tangy"]
    }
]
```

#### B. Simple Response Messages (for non-product queries)
```python
"simple_response": {
    "message": "Hi! I'm Flean, your shopping assistant 😊...",  # ← STREAM THIS
    "response_type": "bot_identity"
}
```

#### C. Reasoning Text (optional, for transparency)
```python
"reasoning": "User wants chips for party, need budget and quantity..."  # ← COULD STREAM
```

### 1.2 Internal State Outputs (NEVER STREAM, MUST BE COMPLETE)

These drive backend logic and **MUST** be complete before processing:

```python
{
    # Routing & Classification
    "route": "product" | "support" | "general",
    "data_strategy": "none" | "es_fetch" | "memory_only",
    "is_follow_up": true | false,
    "follow_up_confidence": "high" | "medium" | "low",
    
    # Product Taxonomy
    "domain": "f_and_b" | "personal_care" | "other",
    "category": "chips_and_crisps",
    "subcategories": ["moisturizer", "serum", "face_cream"],
    "product_intent": "show_me_options" | "is_this_good" | "which_is_better" | "show_me_alternate",
    
    # ASK Slot Structure (NOT the messages)
    "ask_slots": [...],  # Full structure with slot_name, options
    
    # Backend Actions
    "fetch_functions": ["search_products"],
    
    # Simple Response Structure (NOT the message text)
    "simple_response": {
        "response_type": "bot_identity" | "out_of_category" | ...
    }
}
```

### 1.3 Persistence Points (WHERE internal state is saved)

```python
# Location: bot_core.py:422-427
ctx.session.update(
    intent_l1=("A" if is_prod else "E"),
    intent_l2=("A1" if is_prod else "E2"),
    intent_l3=l3,
    is_product_related=is_prod,
    domain=combined.get("domain"),
)

# Location: bot_core.py:502
ctx.session["product_intent"] = p_intent

# Location: bot_core.py:543-546
ctx.session["domain"] = new_domain

# Location: bot_core.py:556-564
ctx.session["contextual_questions"] = {
    "ASK_USER_BUDGET": {
        "message": "...",
        "type": "multi_choice",
        "options": [...]
    }
}

# Location: bot_core.py:569-576
ctx.session["assessment"] = {
    "original_query": query,
    "intent": ...,
    "missing_data": [...],
    "priority_order": [...],
    "fulfilled": [],
    "currently_asking": ask_keys[0]
}

# Location: bot_core.py:578
self.ctx_mgr.save_context(ctx)  # ← ATOMIC REDIS WRITE
```

---

## Part 2: Anthropic's Native Streaming Tool-Use Support

### 2.1 Event Sequence (from Anthropic Docs)

Anthropic **fully supports** streaming with tool calls. Here's the event flow:

```
1. message_start       → Stream opens, empty message shell
2. content_block_start → type="tool_use", tool name announced
3. content_block_delta → type="input_json_delta", partial JSON chunks (REPEAT)
   ├─ Each delta contains: {"partial_json": "..."}
   └─ Accumulate these chunks; DO NOT parse yet
4. content_block_stop  → Tool args complete; NOW parse accumulated JSON
5. message_delta       → stop_reason, token usage
6. message_stop        → Stream ends
```

**Key insight**: Tool arguments stream as **raw JSON strings** via `input_json_delta`. We accumulate the partial JSON, extract user-facing strings incrementally, and parse the complete structure at `content_block_stop`.

### 2.2 Current Non-Streaming Implementation

```python
# llm_service.py:2101-2108 (CURRENT)
resp = await self.anthropic.messages.create(
    model=Cfg.LLM_MODEL,
    messages=[{"role": "user", "content": prompt}],
    tools=[COMBINED_CLASSIFY_ASSESS_TOOL],
    tool_choice={"type": "tool", "name": "classify_and_assess"},
    temperature=0,
    max_tokens=2000,
)
# Returns complete message; no streaming
```

**To enable streaming**:
```python
async with self.anthropic.messages.stream(
    model=Cfg.LLM_MODEL,
    messages=[{"role": "user", "content": prompt}],
    tools=[COMBINED_CLASSIFY_ASSESS_TOOL],
    tool_choice={"type": "tool", "name": "classify_and_assess"},
    temperature=0,
    max_tokens=2000,
) as stream:
    async for event in stream:
        # Process input_json_delta events
        pass
```

### 2.2 State Dependency Chain

```
LLM1 complete 
    ↓
Parse tool_use.input
    ↓
Extract route, domain, category, product_intent, data_strategy
    ↓
Decision tree (bot_core.py:441-605)
    ↓
    ├─ data_strategy="none" → Return simple response immediately
    ├─ data_strategy="memory_only" → Call generate_memory_based_answer
    └─ data_strategy="es_fetch" → Continue to ASK slots or ES fetch
```

**Critical insight**: The entire decision tree depends on having the **complete** tool payload. We cannot make routing decisions on partial data.

### 2.3 Redis Atomicity Requirement

```python
# bot_core.py:578
self.ctx_mgr.save_context(ctx)
```

This must happen **after** all session updates are complete. Partial writes would corrupt state.

---

## Part 3: Solution - Native Streaming with input_json_delta

### 3.1 Core Strategy

**Native Anthropic Streaming**: Use `messages.stream()` with tool_choice, process `input_json_delta` events.

```
┌─────────────────────────────────────────────────────────────┐
│          Anthropic Stream (messages.stream)                  │
└──────┬──────────────────────────────────────────────────────┘
       │
       ├─ message_start
       │      ↓ Initialize accumulator
       │
       ├─ content_block_start (type=tool_use)
       │      ↓ Capture tool_name="classify_and_assess"
       │      ↓ Emit SSE: {"event": "classification_start"}
       │
       ├─ content_block_delta (type=input_json_delta)
       │      ↓ delta.partial_json = '{"reasoning": "User wants chip'
       │      ↓ Accumulate to buffer
       │      ↓ Extract visible strings (regex: "message": "...")
       │      ↓ Emit SSE: {"event": "ask_message_delta", "text": "..."}
       │
       ├─ content_block_delta (type=input_json_delta)  [REPEAT]
       │      ↓ delta.partial_json = 's for party...", "route":'
       │      ↓ Continue accumulation + extraction
       │
       ├─ content_block_stop
       │      ↓ Parse complete accumulated JSON
       │      ↓ Validate tool_use.input structure
       │      ↓ Emit SSE: {"event": "classification_complete"}
       │
       └─ message_stop
              ↓ Save to ctx.session + Redis (atomic)
              ↓ Route to next pipeline stage
```

### 3.2 Implementation Components

#### Component 1: Streaming Tool Accumulator

```python
# NEW: shopping_bot/streaming/tool_stream_accumulator.py

class ToolStreamAccumulator:
    """
    Accumulates tool-use JSON chunks from Anthropic streaming API
    while extracting user-visible strings for progressive display.
    """
    def __init__(self):
        self.tool_name = None
        self.input_buffer = ""  # Raw JSON string buffer
        self.partial_parsed = {}  # Incrementally parsed structure
        self.user_facing_deltas = []  # Extracted strings for streaming
        
    def process_event(self, event):
        """Process a single stream event"""
        event_type = getattr(event, 'type', None) or event.get('type')
        
        if event_type == 'content_block_start':
            # Tool use starts
            content_block = getattr(event, 'content_block', event.get('content_block'))
            if content_block and content_block.type == 'tool_use':
                self.tool_name = content_block.name
                
        elif event_type == 'content_block_delta':
            delta = getattr(event, 'delta', event.get('delta'))
            if delta and delta.type == 'input_json_delta':
                json_chunk = delta.partial_json
                self.input_buffer += json_chunk
                
                # Try to extract user-facing strings incrementally
                self._extract_user_strings(json_chunk)
                
        elif event_type == 'content_block_stop':
            # Complete tool payload received
            self._finalize()
            
    def _extract_user_strings(self, json_chunk):
        """
        Incrementally extract user-visible strings from JSON chunks.
        Uses heuristic parsing to identify message fields.
        """
        try:
            # Pattern 1: ASK slot messages
            # Look for: "message": "What's your budget?"
            import re
            message_pattern = r'"message"\s*:\s*"([^"]+)"'
            matches = re.findall(message_pattern, json_chunk)
            for match in matches:
                if match and match not in [d['text'] for d in self.user_facing_deltas]:
                    self.user_facing_deltas.append({
                        'type': 'ask_message',
                        'text': match
                    })
                    
            # Pattern 2: Simple response message
            # Look for: "simple_response": { "message": "Hi! I'm Flean..."
            simple_pattern = r'"simple_response"[^}]*"message"\s*:\s*"([^"]+)"'
            simple_matches = re.findall(simple_pattern, self.input_buffer)  # Use full buffer for context
            if simple_matches:
                latest = simple_matches[-1]
                if latest not in [d['text'] for d in self.user_facing_deltas]:
                    self.user_facing_deltas.append({
                        'type': 'simple_response',
                        'text': latest
                    })
                    
        except Exception as e:
            log.debug(f"String extraction failed: {e}")
            
    def _finalize(self):
        """Parse complete JSON payload"""
        try:
            self.partial_parsed = json.loads(self.input_buffer)
        except json.JSONDecodeError as e:
            log.error(f"Tool JSON parse failed: {e}")
            self.partial_parsed = {}
            
    def get_complete_payload(self):
        """Return the fully accumulated and parsed tool input"""
        return self.partial_parsed
        
    def get_user_deltas(self):
        """Return list of user-facing string deltas"""
        return self.user_facing_deltas
```

#### Component 2: Enhanced LLM1 with Streaming Support

```python
# MODIFIED: shopping_bot/llm_service.py

async def classify_and_assess_stream(
    self, 
    query: str, 
    ctx: Optional[UserContext] = None,
    emit_callback: Optional[Callable[[Dict], Awaitable[None]]] = None
) -> Dict[str, Any]:
    """
    Streaming version of classify_and_assess.
    
    Args:
        query: User input
        ctx: User context
        emit_callback: Async function to call with SSE deltas
        
    Returns:
        Complete classification payload (identical to non-streaming version)
    """
    # Build prompt (identical to existing)
    prompt = self._build_classify_prompt(query, ctx)
    
    accumulator = ToolStreamAccumulator()
    
    async with self.anthropic.messages.stream(
        model=Cfg.LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        tools=[COMBINED_CLASSIFY_ASSESS_TOOL],
        tool_choice={"type": "tool", "name": "classify_and_assess"},
        temperature=0,
        max_tokens=2000,
    ) as stream:
        async for event in stream:
            # Accumulate tool payload
            accumulator.process_event(event)
            
            # Stream user-facing deltas
            if emit_callback:
                for delta in accumulator.get_user_deltas():
                    if delta not in accumulator._emitted:  # Track to avoid duplicates
                        await emit_callback({
                            'event': 'classification_delta',
                            'data': delta
                        })
                        accumulator._emitted.add(delta['text'])
    
    # Extract complete payload
    data = accumulator.get_complete_payload()
    
    # Existing validation and enrichment logic
    route = data.get("route")
    if route == "product":
        # ... (existing ASK slot validation, lines 2122-2156)
        pass
        
    return data
```

#### Component 3: SSE Route Integration

```python
# MODIFIED: shopping_bot/routes/chat_stream.py

@bp.post("/chat/stream")
def chat_stream() -> Response:
    def generate():
        # ... (existing setup, lines 45-80)
        
        # === STREAMING LLM1 ===
        async def emit_sse(payload):
            """Callback for LLM1 streaming"""
            event_type = payload.get('event', 'delta')
            data = payload.get('data', {})
            yield _sse_event(event_type, data)
        
        # Call streaming classification
        classification = asyncio.run(
            llm_service.classify_and_assess_stream(
                message, 
                ctx,
                emit_callback=emit_sse
            )
        )
        
        # === REST OF PIPELINE (identical to existing) ===
        route = classification.get("route", "general")
        data_strategy = classification.get("data_strategy", "none")
        
        # Save classification to context
        ctx.session["intent_l3"] = classification.get("layer3", "general")
        ctx.session["is_product_related"] = classification.get("is_product_related", False)
        
        # ... (existing routing logic, lines 94-187)
```

---

## Part 4: Challenges & Mitigations

### Challenge 1: Incremental JSON Parsing Fragility

**Problem**: JSON chunks may split mid-string or mid-object.

**Mitigation**:
- Use regex for string extraction (patterns are robust to partial JSON)
- Only parse complete buffer at `content_block_stop`
- Fall back to silent accumulation if extraction fails

### Challenge 2: Duplicate Delta Detection

**Problem**: Regex might re-extract the same string from growing buffer.

**Mitigation**:
```python
self._emitted = set()  # Track emitted text
if delta['text'] not in self._emitted:
    await emit_callback(delta)
    self._emitted.add(delta['text'])
```

