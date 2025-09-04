from __future__ import annotations

import json
from typing import Any, Dict, List

import anthropic

from .config import get_config
from .data_fetchers.es_products import get_es_fetcher
from .models import UserContext

Cfg = get_config()


async def process_image_query(ctx: UserContext, image_url: str) -> Dict[str, Any]:
    """Process an image URL to extract product info and fetch top 3 product IDs from ES.

    No confidence threshold; accepts model output as-is.
    """
    try:
        extractor = anthropic.AsyncAnthropic(api_key=Cfg.ANTHROPIC_API_KEY)

        TOOL = {
            "name": "parse_product_from_image",
            "description": "Extract product_name, brand_name, OCR text, and classify category_group (f_and_b or personal_care).",
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
            "You are given a product photo URL.\n"
            "Perform OCR and extract strictly from the image (no guessing):\n"
            "- product_name: prominent printed product name (include clear variant/flavor)\n"
            "- brand_name: printed brand; empty if not visible\n"
            "- ocr_full_text: compact readable text from label\n"
            "Then classify ONLY the high-level category_group:\n"
            "- If food or beverage, set category_group=f_and_b.\n"
            "- If personal care (skin/hair/body), set category_group=personal_care.\n"
            "Prefer explicit label cues over background.\n"
            "Return ONLY a tool call to parse_product_from_image with fields:\n"
            "product_name, brand_name, ocr_full_text, category_group.\n"
        )

        resp = await extractor.messages.create(
            model=Cfg.LLM_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image", "source": {"type": "url", "url": image_url}},
                    ],
                }
            ],
            tools=[TOOL],
            tool_choice={"type": "tool", "name": "parse_product_from_image"},
            temperature=0,
            max_tokens=400,
        )

        # Extract tool use
        product_name = ""
        brand_name = ""
        ocr_text = ""
        for block in (resp.content or []):
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "parse_product_from_image":
                data = getattr(block, "input", {}) or {}
                product_name = str(data.get("product_name", "")).strip()
                brand_name = str(data.get("brand_name", "")).strip()
                ocr_text = str(data.get("ocr_full_text", "")).strip()
                category_group = str(data.get("category_group", "")).strip()
                break

        try:
            print(f"IMAGE_VISION | product='{product_name}' | brand='{brand_name}' | ocr_len={len(ocr_text)}")
        except Exception:
            pass

        # Build ES query from extracted fields and high-level category
        # Tokenize name preserving informative tokens like flavor and brand cues
        name_tokens = [t for t in product_name.replace("|", " ").split() if t]
        should = []
        if product_name:
            should.append({
                "multi_match": {
                    "query": product_name,
                    "type": "phrase",
                    "fields": ["name^5", "description^2", "combined_text"],
                }
            })
        if brand_name:
            should.append({
                "match": {"brand": {"query": brand_name, "boost": 3.0}}
            })
        # Add a few tokens from OCR to help
        for tok in name_tokens[:3]:
            should.append({
                "multi_match": {
                    "query": tok,
                    "type": "best_fields",
                    "fields": ["name^3", "description", "combined_text"],
                }
            })

        # Use existing fetcher.search params API by composing params it expects
        params: Dict[str, Any] = {
            "q": product_name or ocr_text,
            "size": 3,
            "keywords": name_tokens[:4],
        }
        if category_group:
            params["category_group"] = category_group
        if brand_name:
            # Brand normalization via ES brand suggestion to align with canonical values (e.g., 'Dabur Real')
            try:
                canonical = await loop.run_in_executor(None, lambda: fetcher.suggest_brand(brand_name, category_group or None))
            except Exception:
                canonical = None
            effective_brand = (canonical or brand_name).strip()
            params["brands"] = [effective_brand]
            params["enforce_brand"] = True  # Hard brand filter for vision flow
            try:
                print(f"IMAGE_BRAND_CANON | raw='{brand_name}' | canonical='{effective_brand}'")
            except Exception:
                pass

        # If the product name contains color/flavor tokens like 'orange', enforce them as must_keywords
        flavor_tokens = []
        for t in ["orange", "mango", "apple", "guava", "mixed", "pineapple"]:
            if t in (product_name or "").lower():
                flavor_tokens.append(t)
        if flavor_tokens:
            params["must_keywords"] = flavor_tokens

        fetcher = get_es_fetcher()
        import asyncio as _a
        loop = _a.get_running_loop()
        result = await loop.run_in_executor(None, lambda: fetcher.search(params))

        products = (result or {}).get("products", [])
        product_ids: List[str] = [str(p.get("id")).strip() for p in products[:3] if str(p.get("id") or "").strip()]
        try:
            print(f"ES_IMAGE_TOP3 | ids={product_ids}")
        except Exception:
            pass

        return {"product_ids": product_ids}

    except Exception as exc:
        try:
            print(f"IMAGE_FLOW_ERROR | error={exc}")
        except Exception:
            pass
        return {"product_ids": []}


