from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

import anthropic
import base64
import mimetypes
import traceback

from .config import get_config
from .data_fetchers.es_products import get_es_fetcher
from .models import UserContext

Cfg = get_config()


_IMG_MAX_BYTES = 5 * 1024 * 1024  # 5 MB cap aligned with Anthropic limits


def _detect_media_type(image_bytes: bytes) -> str:
    """Detect common image media types from magic numbers."""
    try:
        if len(image_bytes) >= 3 and image_bytes[:3] == b"\xff\xd8\xff":
            return "image/jpeg"
        if len(image_bytes) >= 8 and image_bytes[:8] == b"\x89PNG\r\n\x1a\x0a":
            return "image/png"
        if len(image_bytes) >= 4 and image_bytes[:4] == b"GIF8":
            return "image/gif"
        if len(image_bytes) >= 12 and image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
            return "image/webp"
    except Exception:
        pass
    return ""


def _normalize_b64_input(image_input: str) -> Tuple[str, str]:
    """Accept base64 from FE (data URL or raw). Return (media_type, base64_data)."""
    ALLOWED_MEDIA = {"image/jpeg", "image/png", "image/gif", "image/webp"}

    if image_input.startswith("data:"):
        try:
            header, b64_part = image_input.split(",", 1)
            mt = header.split(";")[0].split(":", 1)[1].strip()
            # Normalize and validate
            raw_bytes = base64.b64decode(b64_part, validate=False)
            if not raw_bytes:
                raise ValueError("empty_image")
            if len(raw_bytes) > _IMG_MAX_BYTES:
                raise ValueError("image_too_large")
            mt_eff = mt if mt in ALLOWED_MEDIA else _detect_media_type(raw_bytes)
            if not mt_eff or mt_eff not in ALLOWED_MEDIA:
                raise ValueError("unsupported_media_type")
            b64_norm = base64.b64encode(raw_bytes).decode("ascii")
            try:
                print(f"IMG_B64_INPUT | type=data_url | mt={mt_eff} | bytes={len(raw_bytes)} | b64_len={len(b64_norm)}")
            except Exception:
                pass
            return mt_eff, b64_norm
        except Exception as e:
            raise RuntimeError(f"invalid_data_url: {e}")

    # Assume raw base64 string
    try:
        raw_bytes = base64.b64decode(image_input, validate=False)
    except Exception as e:
        raise RuntimeError(f"invalid_base64: {e}")
    if not raw_bytes:
        raise RuntimeError("empty_image")
    if len(raw_bytes) > _IMG_MAX_BYTES:
        raise RuntimeError("image_too_large")
    mt_eff = _detect_media_type(raw_bytes) or "image/jpeg"
    if mt_eff not in ALLOWED_MEDIA:
        raise RuntimeError("unsupported_media_type")
    b64_norm = base64.b64encode(raw_bytes).decode("ascii")
    try:
        print(f"IMG_B64_INPUT | type=raw | mt={mt_eff} | bytes={len(raw_bytes)} | b64_len={len(b64_norm)}")
    except Exception:
        pass
    return mt_eff, b64_norm


async def process_image_query(ctx: UserContext, image_url: str) -> Dict[str, Any]:
    """Process an image URL to extract product info and fetch top 3 product IDs from ES.

    No confidence threshold; accepts model output as-is.
    """
    try:
        media_type, b64_data = _normalize_b64_input(image_url)
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
            "You are given a product photo.\n"
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
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64_data,
                            },
                        },
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
        # Prepare fetcher and loop before any sync offloading
        fetcher = get_es_fetcher()
        import asyncio as _a
        loop = _a.get_running_loop()

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
            # Signal to ES builder that this request comes from vision
            "is_image_query": True,
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
            tb = traceback.format_exc()
            print(f"IMAGE_FLOW_TRACEBACK\n{tb}")
        except Exception:
            pass
        return {"product_ids": []}


