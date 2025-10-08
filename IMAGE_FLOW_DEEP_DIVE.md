# IMAGE PROCESSING DEEP DIVE: Fast Combined 3 LLM Approach

## Overview

Your codebase implements a sophisticated **fast combined 3 LLM approach** for image processing that transforms product photos into actionable shopping recommendations. This document traces the complete path from image reception to final answer generation.

## 1. IMAGE RECEIPT & NORMALIZATION

**Entry Point**: `shopping_bot/routes/chat.py:425-457`

When an `image_url` parameter is detected in the chat request, the system immediately branches to the vision flow:

```python
# NEW: Image pathway — if image_url is present, run image flow and short-circuit
image_url = str(data.get("image_url") or "").strip()
if image_url:
    # Run image flow to get top 3 product ids
    from ..vision_flow import process_image_query
    log.info(f"IMAGE_FLOW_START | user={user_id} | url_present=true")
    image_result = await process_image_query(ctx, image_url)
    
    # Build minimal envelope content for immediate UX response
    content = {
        "summary_message": "Choose an option:",
        "ux_response": {
            "ux_surface": "MPM",
            "quick_replies": ["Show healthier", "Cheaper", "More like this"],
            "product_ids": image_result.get("product_ids", [])
        },
        "product_intent": "show_me_options",
    }
```

**Key Insight**: The image path **short-circuits** the normal text processing pipeline and returns immediately with product IDs for UX display.

## 2. IMAGE NORMALIZATION & VALIDATION

**Location**: `shopping_bot/vision_flow.py:37-81`

The `_normalize_b64_input()` function handles two input formats:

### Data URL Format (`data:image/jpeg;base64,...`)
```python
def _normalize_b64_input(image_input: str) -> Tuple[str, str]:
    if image_input.startswith("data:"):
        try:
            header, b64_part = image_input.split(",", 1)
            mt = header.split(";")[0].split(":", 1)[1].strip()
            # Normalize and validate
            raw_bytes = base64.b64decode(b64_part, validate=False)
            
            # Size validation (5MB cap)
            if len(raw_bytes) > _IMG_MAX_BYTES:  # 5MB limit
                raise ValueError("image_too_large")
            
            # Media type validation
            ALLOWED_MEDIA = {"image/jpeg", "image/png", "image/gif", "image/webp"}
            mt_eff = mt if mt in ALLOWED_MEDIA else _detect_media_type(raw_bytes)
            
            return mt_eff, base64.b64encode(raw_bytes).decode("ascii")
```

### Raw Base64 Format
```python
# Assume raw base64 string
try:
    raw_bytes = base64.b64decode(image_input, validate=False)
except Exception as e:
    raise RuntimeError(f"invalid_base64: {e}")
```

**Magic Number Detection**: `_detect_media_type()` identifies image types from binary headers:
- JPEG: `\xff\xd8\xff`
- PNG: `\x89PNG\r\n\x1a\x0a`
- GIF: `GIF8`
- WebP: `RIFF` + `WEBP`

## 3. VISION ANALYSIS (LLM 1): Product Extraction & Classification

**Location**: `shopping_bot/vision_flow.py:83-162`

The first LLM call uses **Anthropic's vision model** with structured tool calling:

```python
async def process_image_query(ctx: UserContext, image_url: str) -> Dict[str, Any]:
    media_type, b64_data = _normalize_b64_input(image_url)
    extractor = anthropic.AsyncAnthropic(api_key=Cfg.ANTHROPIC_API_KEY)

    TOOL = {
        "name": "parse_product_from_image",
        "description": "Extract product_name, brand_name, OCR text, and classify category_group",
        "input_schema": {
            "type": "object",
            "properties": {
                "product_name": {"type": "string"},
                "brand_name": {"type": "string"},
                "ocr_full_text": {"type": "string"},
                "category_group": {"type": "string"}
            },
            "required": ["product_name", "ocr_full_text"]
        }
    }

    prompt = (
        "You are given a product photo.\n"
        "Perform OCR and extract strictly from the image (no guessing):\n"
        "- product_name: prominent printed product name (include clear variant/flavor)\n"
        "- brand_name: printed brand; empty if not visible\n"
        "- ocr_full_text: compact readable text from label\n"
        "Then classify ONLY the high-level category_group:\n"
        "- If food or beverage, set category_group=f_and_b.\n"
        "- If personal care (skin/hair/body), set category_group=personal_care.\n"
        "Return ONLY a tool call to parse_product_from_image"
    )

    resp = await extractor.messages.create(
        model=Cfg.LLM_MODEL,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": b64_data,
                    },
                },
            ],
        }],
        tools=[TOOL],
        tool_choice={"type": "tool", "name": "parse_product_from_image"},
        temperature=0,  # Deterministic extraction
        max_tokens=400,
    )

    # Extract structured data from tool response
    product_name = str(data.get("product_name", "")).strip()
    brand_name = str(data.get("brand_name", "")).strip()
    ocr_text = str(data.get("ocr_full_text", "")).strip()
    category_group = str(data.get("category_group", "")).strip()
```

## 4. ELASTICSEARCH PRODUCT SEARCH

**Location**: `shopping_bot/vision_flow.py:163-235`

The extracted vision data powers an Elasticsearch query with sophisticated matching:

```python
# Build ES query from extracted fields and high-level category
name_tokens = [t for t in product_name.replace("|", " ").split() if t]
should = []

# Primary phrase matching on product name
if product_name:
    should.append({
        "multi_match": {
            "query": product_name,
            "type": "phrase",
            "fields": ["name^5", "description^2", "combined_text"],
        }
    })

# Brand matching with canonicalization
if brand_name:
    canonical = await loop.run_in_executor(None, lambda: fetcher.suggest_brand(brand_name, category_group or None))
    effective_brand = (canonical or brand_name).strip()
    should.append({
        "match": {"brand": {"query": effective_brand, "boost": 3.0}}
    })

# OCR text token matching
for tok in name_tokens[:3]:
    should.append({
        "multi_match": {
            "query": tok,
            "type": "best_fields",
            "fields": ["name^3", "description", "combined_text"],
        }
    })

# Flavor detection for food products
flavor_tokens = []
for t in ["orange", "mango", "apple", "guava", "mixed", "pineapple"]:
    if t in (product_name or "").lower():
        flavor_tokens.append(t)

# Execute search with all parameters
params: Dict[str, Any] = {
    "q": product_name or ocr_text,
    "size": 3,  # Top 3 results
    "keywords": name_tokens[:4],
    "is_image_query": True,  # Special handling flag
}
if category_group:
    params["category_group"] = category_group
if brand_name:
    params["brands"] = [effective_brand]
    params["enforce_brand"] = True
if flavor_tokens:
    params["must_keywords"] = flavor_tokens

result = await loop.run_in_executor(None, lambda: fetcher.search(params))
products = (result or {}).get("products", [])
product_ids: List[str] = [str(p.get("id")).strip() for p in products[:3]]

return {"product_ids": product_ids}
```

## 5. IMMEDIATE UX RESPONSE (No LLM 2/3 Yet)

**Location**: `shopping_bot/routes/chat.py:436-457`

The system **immediately returns** with a UX-focused response:

```python
content = {
    "summary_message": "Choose an option:",
    "ux_response": {
        "ux_surface": "MPM",  # Multi-Product Mode
        "quick_replies": ["Show healthier", "Cheaper", "More like this"],
        "product_ids": image_result.get("product_ids", [])
    },
    "product_intent": "show_me_options",
}
envelope = build_envelope(
    wa_id=wa_id,
    session_id=session_id,
    bot_resp_type=ResponseType.IMAGE_IDS,  # Special response type
    content=content,
    # ... other parameters
)
```

## 6. USER SELECTION & FOLLOW-UP PROCESSING

**Location**: `shopping_bot/routes/chat.py:328-422`

When a user selects a product (`selected_product_id`), the system:

1. **Fetches full product details** via Elasticsearch `mget`
2. **Synthesizes a query** for single product analysis
3. **Routes to the full 3 LLM pipeline** for detailed response generation

```python
if selected_product_id:
    # Fetch full product doc
    fetcher = get_es_fetcher()
    docs = await loop.run_in_executor(None, lambda: fetcher.mget_products([selected_product_id]))
    doc = docs[0]
    
    # Create synthetic query for SPM (Single Product Mode)
    synthetic_query = f"is this good? {brand} {name}".strip()
    
    # Inject fetched data for LLM processing
    fetched = {
        "search_products": {
            "products": [doc],
            "meta": {"total_hits": 1, "returned": 1}
        }
    }
    
    # Route to full LLM pipeline
    llm = LLMService()
    answer = await llm.generate_response(
        synthetic_query,
        ctx,
        fetched,
        intent_l3="Product_Discovery",
        product_intent="is_this_good",
    )
```

## 7. THE "FAST COMBINED 3 LLM APPROACH" REVEALED

The system uses **three sequential LLM calls** in the unified response generation:

### LLM 1: Combined Classification & Assessment
**Location**: `shopping_bot/llm_service.py:1594-1752`

When `USE_COMBINED_CLASSIFY_ASSESS=true` (production flag), this replaces separate classification and assessment:

```python
async def classify_and_assess(self, query: str, ctx: Optional[UserContext] = None) -> Dict[str, Any]:
    # Single LLM call that does both:
    # 1. Intent classification (L1/L2/L3)
    # 2. Product-related detection  
    # 3. Requirements assessment (ASK slots)
    # 4. Contextual question generation
    
    resp = await self.anthropic.messages.create(
        model=Cfg.LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        tools=[COMBINED_CLASSIFY_ASSESS_TOOL],
        tool_choice={"type": "tool", "name": "classify_and_assess"},
        temperature=0.2,
        max_tokens=800,
    )
```

### LLM 2: Product Response Generation
**Location**: `shopping_bot/llm_service.py:1894-2148`

This generates the actual product recommendations:

```python
async def _generate_product_response(self, query: str, ctx: UserContext, fetched: Dict[str, Any], intent_l3: str, product_intent: Optional[str] = None) -> Dict[str, Any]:
    # 1. Extract and enrich top products from ES results
    # 2. Select hero product (healthiest/cleanest)
    # 3. Generate unified response with UX
    
    unified_prompt = (
        "You are Flean's WhatsApp copywriter. Write one concise message..."
        # 3-part summary structure with evidence-based recommendations
    )
    
    resp = await self.anthropic.messages.create(
        model=Cfg.LLM_MODEL,
        messages=[{"role": "user", "content": unified_prompt}],
        tools=[FINAL_ANSWER_UNIFIED_TOOL],
        tool_choice={"type": "tool", "name": "generate_final_answer_unified"},
        temperature=0.7,
        max_tokens=900,
    )
```

### LLM 3: UX Response Generation  
**Location**: `shopping_bot/ux_response_generator.py:454-526`

Generates the appropriate UX surface and quick replies:

```python
async def generate_ux_response_for_intent(intent: str, previous_answer: Dict[str, Any], ctx: UserContext, user_query: str) -> Dict[str, Any]:
    # Extract product_ids from previous LLM response
    # Generate UX surface (SPM/MPM) based on intent
    # Create contextual quick replies
    # Build dpl_runtime_text for WhatsApp display
    
    generator = get_ux_response_generator()
    ux_response = await generator.generate_ux_response(...)
```

## 8. FINAL RESPONSE ASSEMBLY

**Location**: `shopping_bot/fe_payload.py:build_envelope()`

The final response envelope combines:
- **summary_message**: 3-part evidence-based recommendation  
- **ux_response**: UX surface + quick replies + product_ids
- **product_ids**: Ordered list with hero first
- **response_type**: `final_answer` or `image_ids`

## PERFORMANCE OPTIMIZATIONS

1. **Image Path Short-Circuit**: Immediate UX response with product IDs, deferring detailed analysis until user selection
2. **Combined Classification**: Single LLM call replaces multiple separate calls
3. **Async Execution**: All LLM calls run asynchronously
4. **Executor Offloading**: ES operations run in thread pools to avoid blocking
5. **Product Enrichment**: Top-K products enriched with full details for better LLM reasoning

## KEY ARCHITECTURAL INSIGHTS

1. **Vision-First UX**: The system prioritizes showing results immediately over detailed analysis
2. **Progressive Enhancement**: Image flow → Product selection → Full LLM pipeline
3. **Evidence-Based Responses**: All recommendations backed by flean scores, percentiles, and nutrition data
4. **Brand Awareness**: Sophisticated brand canonicalization and matching
5. **Context Preservation**: Conversation history and user preferences carried through all stages

This architecture enables **sub-second initial response times** for image queries while maintaining the depth and quality of the full 3 LLM analysis pipeline for engaged users.
