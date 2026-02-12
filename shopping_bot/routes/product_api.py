# shopping_bot/routes/product_api.py
"""
Product APIs for Flutter App

This module provides 3 API endpoints:
1. GET /api/v1/product/{product_id} - PDP (Product Detail Page) - Full product data by ID
2. POST /api/v1/scanner - Scanner API - Image-based product lookup using Claude Vision
3. GET /api/v1/catalogue - Catalogue API - List products by subcategory

All product data is returned as raw Elasticsearch _source without transformation.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional, Tuple

import anthropic
from flask import Blueprint, jsonify, request

from ..config import get_config
from ..data_fetchers.es_products import get_es_fetcher

log = logging.getLogger(__name__)
bp = Blueprint("product_api", __name__)
Cfg = get_config()


# ============================================================================
# Response Helpers
# ============================================================================

def _success_response(data: Any, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build standardized success response."""
    response = {"success": True, "data": data}
    if meta:
        response["meta"] = meta
    return response


def _error_response(code: str, message: str, status: int = 400) -> Tuple[Dict[str, Any], int]:
    """Build standardized error response with status code."""
    return jsonify({
        "success": False,
        "error": {"code": code, "message": message}
    }), status


# ============================================================================
# PDP API - Product Detail Page
# ============================================================================

@bp.route("/api/v1/product/<product_id>", methods=["GET"])
def get_product_detail(product_id: str) -> Tuple[Dict[str, Any], int]:
    """
    Get complete product data by Elasticsearch ID.
    
    Returns the full raw _source from Elasticsearch without any transformation.
    The Flutter app will parse the data as needed.
    
    Path Parameters:
        product_id: Elasticsearch product ID (e.g., '01K1B1BPGN2WAXFB5DNSGXX4W3')
    
    Response (200):
        {
            "success": true,
            "data": {
                "id": "...",
                "name": "...",
                "brand": "...",
                "price": 349.0,
                "mrp": 399.0,
                "hero_image": {...},
                "category_group": "f_and_b",
                "category_paths": [...],
                "package_claims": {...},
                "category_data": {...},
                "flean_score": {...},
                "stats": {...},
                "ingredients": {...},
                ...
            }
        }
    
    Response (404):
        {"success": false, "error": {"code": "PRODUCT_NOT_FOUND", "message": "..."}}
    """
    try:
        if not product_id or not product_id.strip():
            return _error_response("INVALID_ID", "Product ID is required", 400)
        
        pid = product_id.strip()
        fetcher = get_es_fetcher()
        
        product = fetcher.get_product_by_id(pid)
        
        if not product:
            log.warning(f"PDP_NOT_FOUND | id={pid}")
            return _error_response("PRODUCT_NOT_FOUND", f"Product with ID '{pid}' not found", 404)
        
        log.info(f"PDP_SUCCESS | id={pid} | name={product.get('name', '')[:30]}")
        
        return jsonify(_success_response(product)), 200
        
    except Exception as e:
        log.error(f"PDP_ERROR | id={product_id} | error={e}", exc_info=True)
        return _error_response("INTERNAL_ERROR", "Failed to fetch product details", 500)


# ============================================================================
# Scanner API - Image-based Product Lookup
# ============================================================================

# Image processing constants
_IMG_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_ALLOWED_MEDIA = {"image/jpeg", "image/png", "image/gif", "image/webp"}


def _detect_media_type(image_bytes: bytes) -> str:
    """Detect image media type from magic bytes."""
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


def _normalize_base64_image(image_input: str) -> Tuple[str, str]:
    """
    Normalize base64 image input (data URL or raw base64).
    
    Returns:
        Tuple of (media_type, base64_data)
    
    Raises:
        ValueError: If image is invalid, too large, or unsupported format
    """
    # Handle data URL format
    if image_input.startswith("data:"):
        try:
            header, b64_part = image_input.split(",", 1)
            mt = header.split(";")[0].split(":", 1)[1].strip()
            raw_bytes = base64.b64decode(b64_part, validate=False)
            
            if not raw_bytes:
                raise ValueError("Empty image data")
            if len(raw_bytes) > _IMG_MAX_BYTES:
                raise ValueError(f"Image too large (max {_IMG_MAX_BYTES // (1024*1024)}MB)")
            
            mt_eff = mt if mt in _ALLOWED_MEDIA else _detect_media_type(raw_bytes)
            if not mt_eff or mt_eff not in _ALLOWED_MEDIA:
                raise ValueError(f"Unsupported image format. Allowed: {_ALLOWED_MEDIA}")
            
            b64_norm = base64.b64encode(raw_bytes).decode("ascii")
            return mt_eff, b64_norm
            
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Invalid data URL format: {e}")
    
    # Handle raw base64
    try:
        raw_bytes = base64.b64decode(image_input, validate=False)
    except Exception as e:
        raise ValueError(f"Invalid base64 encoding: {e}")
    
    if not raw_bytes:
        raise ValueError("Empty image data")
    if len(raw_bytes) > _IMG_MAX_BYTES:
        raise ValueError(f"Image too large (max {_IMG_MAX_BYTES // (1024*1024)}MB)")
    
    mt_eff = _detect_media_type(raw_bytes) or "image/jpeg"
    if mt_eff not in _ALLOWED_MEDIA:
        raise ValueError(f"Unsupported image format. Allowed: {_ALLOWED_MEDIA}")
    
    b64_norm = base64.b64encode(raw_bytes).decode("ascii")
    return mt_eff, b64_norm


def _extract_product_from_image(media_type: str, b64_data: str) -> Dict[str, Any]:
    """
    Use Anthropic Claude to extract product name and brand from image.
    
    Returns:
        Dict with 'product_name', 'brand_name', 'ocr_text', 'category_group'
    """
    import json as _json
    
    api_key = getattr(Cfg, "ANTHROPIC_API_KEY", "") or ""
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")
    
    client = anthropic.Anthropic(api_key=api_key)
    
    # Use a simple text-based prompt that asks for JSON output
    # This avoids the tools API complexity with sync client
    prompt = (
        "Analyze this product image and extract the following information. "
        "Return your answer as valid JSON with these exact fields:\n\n"
        "{\n"
        '  "product_name": "the main product name printed on packaging (include variant/flavor)",\n'
        '  "brand_name": "the brand name (empty string if not visible)",\n'
        '  "ocr_full_text": "all readable text from the product label",\n'
        '  "category_group": "f_and_b" for food/beverages OR "personal_care" for skin/hair/body\n'
        "}\n\n"
        "Return ONLY the JSON object, no other text."
    )
    
    resp = client.messages.create(
        model=getattr(Cfg, "LLM_MODEL", "claude-sonnet-4-20250514"),
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
        temperature=0,
        max_tokens=500,
    )
    
    # Extract result from response text
    result = {
        "product_name": "",
        "brand_name": "",
        "ocr_text": "",
        "category_group": "f_and_b"
    }
    
    # Get the text content from response
    response_text = ""
    for block in (resp.content or []):
        if hasattr(block, "text"):
            response_text += block.text
    
    # Try to parse JSON from response
    try:
        # Find JSON in response (handle markdown code blocks)
        text = response_text.strip()
        if text.startswith("```"):
            # Extract from code block
            lines = text.split("\n")
            json_lines = []
            in_json = False
            for line in lines:
                if line.startswith("```") and not in_json:
                    in_json = True
                    continue
                elif line.startswith("```") and in_json:
                    break
                elif in_json:
                    json_lines.append(line)
            text = "\n".join(json_lines)
        
        parsed = _json.loads(text)
        result["product_name"] = str(parsed.get("product_name", "")).strip()
        result["brand_name"] = str(parsed.get("brand_name", "")).strip()
        result["ocr_text"] = str(parsed.get("ocr_full_text", "")).strip()
        result["category_group"] = str(parsed.get("category_group", "f_and_b")).strip()
        
    except _json.JSONDecodeError:
        # If JSON parsing fails, try to extract key info from text
        log.warning(f"SCANNER_JSON_PARSE_ERROR | response={response_text[:200]}")
        # Leave result as default empty values
    
    return result


@bp.route("/api/v1/scanner", methods=["POST"])
def scanner_lookup() -> Tuple[Dict[str, Any], int]:
    """
    Scan a product image to identify and fetch product details.
    
    Uses Anthropic Claude Vision to extract product name and brand from image,
    then searches Elasticsearch to find matching products.
    
    Request Body:
        {
            "image": "<base64_encoded_image>" or "data:image/jpeg;base64,..."
        }
    
    Response (200):
        {
            "success": true,
            "data": {
                "extracted": {
                    "product_name": "Yoga Bar Protein",
                    "brand_name": "Yoga Bar",
                    "ocr_text": "...",
                    "category_group": "f_and_b"
                },
                "products": [
                    { ...full ES product data... },
                    ...
                ]
            }
        }
    
    Response (400):
        {"success": false, "error": {"code": "INVALID_IMAGE", "message": "..."}}
    """
    try:
        # Parse request
        body = request.get_json(force=True, silent=True) or {}
        image_data = body.get("image", "")
        
        if not image_data:
            return _error_response("MISSING_IMAGE", "Image data is required in 'image' field", 400)
        
        # Validate and normalize image
        try:
            media_type, b64_data = _normalize_base64_image(image_data)
        except ValueError as e:
            return _error_response("INVALID_IMAGE", str(e), 400)
        
        log.info(f"SCANNER_START | media_type={media_type} | size={len(b64_data)}")
        
        # Extract product info using Claude Vision
        try:
            extracted = _extract_product_from_image(media_type, b64_data)
        except Exception as e:
            log.error(f"SCANNER_VISION_ERROR | error={e}", exc_info=True)
            return _error_response("VISION_ERROR", f"Failed to analyze image: {e}", 500)
        
        product_name = extracted.get("product_name", "")
        brand_name = extracted.get("brand_name", "")
        
        log.info(f"SCANNER_EXTRACTED | product={product_name} | brand={brand_name}")
        
        if not product_name and not brand_name:
            return jsonify(_success_response({
                "extracted": extracted,
                "products": [],
                "message": "Could not identify product from image"
            })), 200
        
        # Search ES for matching products
        fetcher = get_es_fetcher()
        
        # Build search query
        search_query = f"{brand_name} {product_name}".strip() if brand_name else product_name
        
        # Use existing search method with constructed query
        result = fetcher.search({
            "q": search_query,
            "size": 5,
            "category_group": extracted.get("category_group", ""),
        })
        
        # Get raw products from result
        products = result.get("products", [])
        
        log.info(f"SCANNER_COMPLETE | query={search_query} | found={len(products)}")
        
        return jsonify(_success_response({
            "extracted": extracted,
            "products": products
        })), 200
        
    except Exception as e:
        log.error(f"SCANNER_ERROR | error={e}", exc_info=True)
        return _error_response("INTERNAL_ERROR", "Failed to process image", 500)


# ============================================================================
# Catalogue API - List Products by Subcategory
# ============================================================================

@bp.route("/api/v1/catalogue", methods=["GET"])
def get_catalogue() -> Tuple[Dict[str, Any], int]:
    """
    List products by subcategory with pagination.
    
    Returns all products matching the subcategory path as raw ES data.
    
    Query Parameters:
        subcategory (required): Category path (e.g., 'f_and_b/food/munchies_and_snacks')
        page (optional): Page number, 0-indexed (default: 0)
        size (optional): Products per page, max 100 (default: 20)
        sort (optional): Sort by 'flean_score' (default) or 'price'
    
    Response (200):
        {
            "success": true,
            "data": {
                "products": [
                    { ...full ES product data... },
                    ...
                ]
            },
            "meta": {
                "total": 150,
                "page": 0,
                "size": 20,
                "total_pages": 8,
                "has_next": true,
                "has_prev": false
            }
        }
    
    Response (400):
        {"success": false, "error": {"code": "MISSING_SUBCATEGORY", "message": "..."}}
    """
    try:
        # Parse query parameters
        subcategory = request.args.get("subcategory", "").strip()
        
        if not subcategory:
            return _error_response(
                "MISSING_SUBCATEGORY", 
                "Query parameter 'subcategory' is required (e.g., 'f_and_b/food/munchies_and_snacks')", 
                400
            )
        
        # Parse pagination params
        try:
            page = int(request.args.get("page", 0))
            page = max(0, page)  # Ensure non-negative
        except (TypeError, ValueError):
            page = 0
        
        try:
            size = int(request.args.get("size", 20))
            size = max(1, min(size, 100))  # Clamp between 1 and 100
        except (TypeError, ValueError):
            size = 20
        
        sort_by = request.args.get("sort", "flean_score").strip().lower()
        if sort_by not in ("flean_score", "price"):
            sort_by = "flean_score"
        
        log.info(f"CATALOGUE_REQUEST | subcategory={subcategory} | page={page} | size={size} | sort={sort_by}")
        
        # Fetch products
        fetcher = get_es_fetcher()
        result = fetcher.search_by_subcategory(
            subcategory=subcategory,
            page=page,
            size=size,
            sort_by=sort_by
        )
        
        products = result.get("products", [])
        meta = result.get("meta", {})
        
        log.info(f"CATALOGUE_COMPLETE | subcategory={subcategory} | total={meta.get('total', 0)} | returned={len(products)}")
        
        return jsonify(_success_response(
            {"products": products},
            meta=meta
        )), 200
        
    except Exception as e:
        log.error(f"CATALOGUE_ERROR | error={e}", exc_info=True)
        return _error_response("INTERNAL_ERROR", "Failed to fetch catalogue", 500)

