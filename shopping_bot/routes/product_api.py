# shopping_bot/routes/product_api.py
"""
Product APIs for Flutter App
─────────────────────────────
Endpoints:
  GET  /api/v1/product/<id>               → PDP (pre-parsed sectioned data)
  GET  /api/v1/product/<id>/alternatives  → 5 healthier alternatives (product cards)
  GET  /api/v1/product/<id>/recommended   → 8 recommended products for PDP (product cards)
  POST /api/v1/scanner                    → Top 3 product cards from image scan
  GET  /api/v1/catalogue                  → Products by subcategory (product cards)
  GET  /api/v1/catalogue/mapping          → Category → subcategory → ES path mapping
"""

from __future__ import annotations

import base64
import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

from ..bedrock_client import BedrockClient

from ..config import get_config
from ..data_fetchers.es_products import (
    get_es_fetcher,
    transform_to_pdp,
    transform_to_product_card,
)

log = logging.getLogger(__name__)
bp = Blueprint("product_api", __name__)
Cfg = get_config()

DATA_DIR = Path(__file__).parent.parent / "data" / "home"


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
# PDP API - Product Detail Page (pre-parsed)
# ============================================================================

@bp.route("/api/v1/product/<product_id>", methods=["GET"])
def get_product_detail(product_id: str) -> Tuple[Dict[str, Any], int]:
    """
    PDP API: Returns pre-parsed, sectioned product data ready for Flutter display.

    Sections returned:
      - product_info: id, name, brand, price, mrp, currency, image_url, image_urls, qty, description
      - flean_badge: score (int), score_display ("4/10"), level, level_text
      - score_cards: named object with keys {flean_rank, protein, fiber, sweeteners, oils, watch_outs, calories}
                     each containing {title, value, subtitle, percentile, status}
      - notes: {criteria_note, ranking_note}
      - highlights: [{label, value}] array (only non-empty values included)
      - ingredients: [string] simple array of ingredient strings
      - nutrition: {basis: "per 100 g", items: [{nutrient, value}]}
      - additional_info: [{label, value}] array (only non-empty values included)
      - macro_tags: [{label, nutrient, value, unit}] top 2 macro nutrients

    Path Parameters:
        product_id: Elasticsearch product ID

    Response (200): { success: true, data: { product_info: {...}, flean_badge: {...}, ... } }
    Response (404): { success: false, error: { code, message } }
    """
    try:
        if not product_id or not product_id.strip():
            return _error_response("INVALID_ID", "Product ID is required", 400)

        pid = product_id.strip()
        fetcher = get_es_fetcher()
        raw_src = fetcher.get_product_by_id(pid)

        if not raw_src:
            log.warning(f"PDP_NOT_FOUND | id={pid}")
            return _error_response("PRODUCT_NOT_FOUND", f"Product with ID '{pid}' not found", 404)

        # Transform to sectioned PDP format
        pdp_data = transform_to_pdp(raw_src)

        log.info(f"PDP_SUCCESS | id={pid} | name={raw_src.get('name', '')[:30]}")
        return jsonify(_success_response(pdp_data)), 200

    except Exception as e:
        log.error(f"PDP_ERROR | id={product_id} | error={e}", exc_info=True)
        return _error_response("INTERNAL_ERROR", "Failed to fetch product details", 500)


# ============================================================================
# Alternatives API - Healthier product suggestions
# ============================================================================

@bp.route("/api/v1/product/<product_id>/alternatives", methods=["GET"])
def get_healthier_alternatives(product_id: str) -> Tuple[Dict[str, Any], int]:
    """
    Returns up to 5 healthier alternatives in the same subcategory,
    sorted by Flean percentile descending (healthiest first).

    The source product is also returned as a product card for reference.

    Path Parameters:
        product_id: Elasticsearch product ID of the scanned/selected product

    Response (200):
        {
            "success": true,
            "data": {
                "source_product": { ...product card... },
                "alternatives": [ ...up to 5 product cards... ]
            }
        }
    """
    try:
        if not product_id or not product_id.strip():
            return _error_response("INVALID_ID", "Product ID is required", 400)

        pid = product_id.strip()
        fetcher = get_es_fetcher()
        result = fetcher.search_healthier_alternatives(pid, limit=5)

        if not result.get("source_product"):
            return _error_response("PRODUCT_NOT_FOUND", f"Product '{pid}' not found", 404)

        source_card = transform_to_product_card(result["source_product"])
        alt_cards = [transform_to_product_card(a) for a in result.get("alternatives", []) if a]

        log.info(f"ALTERNATIVES_SUCCESS | id={pid} | found={len(alt_cards)}")

        return jsonify(_success_response({
            "source_product": source_card,
            "alternatives": alt_cards,
        })), 200

    except Exception as e:
        log.error(f"ALTERNATIVES_ERROR | id={product_id} | error={e}", exc_info=True)
        return _error_response("INTERNAL_ERROR", "Failed to fetch alternatives", 500)


# ============================================================================
# Recommended Products API - Similar products for PDP
# ============================================================================

@bp.route("/api/v1/product/<product_id>/recommended", methods=["GET"])
def get_recommended_products(product_id: str) -> Tuple[Dict[str, Any], int]:
    """
    Returns recommended products for the PDP "You May Also Like" section.

    Logic: Same subcategory as the source product, sorted by Flean percentile
    (healthiest first), excluding the source product.

    Path Parameters:
        product_id: Elasticsearch product ID

    Query Parameters:
        limit (optional): Number of products to return, 1-10 (default 8)

    Response (200):
        {
            "success": true,
            "data": {
                "products": [ ...product cards... ],
                "section_title": "You May Also Like",
                "source_product_id": "...",
                "subcategory": "f_and_b/food/light_bites/chips_and_crisps"
            },
            "meta": {
                "total_in_subcategory": 45,
                "returned": 8
            }
        }
    """
    try:
        if not product_id or not product_id.strip():
            return _error_response("INVALID_ID", "Product ID is required", 400)

        pid = product_id.strip()

        # Parse limit from query params
        try:
            limit = max(1, min(int(request.args.get("limit", 8)), 10))
        except (TypeError, ValueError):
            limit = 8

        fetcher = get_es_fetcher()
        result = fetcher.search_recommended_products(pid, limit=limit)

        if not result.get("subcategory"):
            # Product not found or has no category
            if not result.get("products"):
                return _error_response("PRODUCT_NOT_FOUND", f"Product '{pid}' not found or has no category", 404)

        product_cards = [transform_to_product_card(p) for p in result.get("products", []) if p]

        log.info(f"RECOMMENDED_SUCCESS | id={pid} | found={len(product_cards)}")

        return jsonify(_success_response(
            {
                "products": product_cards,
                "section_title": "You May Also Like",
                "source_product_id": result.get("source_product_id", pid),
                "subcategory": result.get("subcategory", ""),
            },
            meta={
                "total_in_subcategory": result.get("total_in_subcategory", 0),
                "returned": len(product_cards),
            }
        )), 200

    except Exception as e:
        log.error(f"RECOMMENDED_ERROR | id={product_id} | error={e}", exc_info=True)
        return _error_response("INTERNAL_ERROR", "Failed to fetch recommended products", 500)


# ============================================================================
# Scanner API - Image-based Product Lookup (returns top 3 cards)
# ============================================================================

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
    Returns: (media_type, base64_data)
    Raises: ValueError on invalid input
    """
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
            return mt_eff, base64.b64encode(raw_bytes).decode("ascii")
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Invalid data URL format: {e}")

    # Raw base64
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
    return mt_eff, base64.b64encode(raw_bytes).decode("ascii")


def _extract_product_from_image(media_type: str, b64_data: str) -> Dict[str, Any]:
    """Use AWS Bedrock Claude to extract product name and brand from image."""
    import json as _json

    bearer_token = getattr(Cfg, "AWS_BEARER_TOKEN_BEDROCK", "") or ""
    if not bearer_token:
        raise RuntimeError("AWS_BEARER_TOKEN_BEDROCK not configured")

    client = BedrockClient(
        bearer_token=bearer_token,
        region=getattr(Cfg, "BEDROCK_REGION", "ap-south-1"),
        model_id=getattr(Cfg, "BEDROCK_MODEL_ID", "us.anthropic.claude-3-5-sonnet-20241022-v2:0")
    )
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

    resp = client.converse(
        model=getattr(Cfg, "LLM_MODEL", "us.anthropic.claude-3-5-sonnet-20241022-v2:0"),
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64_data}},
            ],
        }],
        temperature=0,
        max_tokens=500,
    )

    result: Dict[str, str] = {"product_name": "", "brand_name": "", "ocr_text": "", "category_group": "f_and_b"}

    response_text = ""
    for block in (resp.content or []):
        if hasattr(block, "text"):
            response_text += block.text

    try:
        text = response_text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            json_lines, in_json = [], False
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
        log.warning(f"SCANNER_JSON_PARSE_ERROR | response={response_text[:200]}")

    return result


@bp.route("/api/v1/scanner", methods=["POST"])
def scanner_lookup() -> Tuple[Dict[str, Any], int]:
    """
    Scanner API (step 1.1): Scan product image → return top 3 product cards.

    The developer shows these 3 cards to the user for selection.
    After selection, call GET /api/v1/product/{id} for the full PDP.
    Then call GET /api/v1/product/{id}/alternatives for healthier options.

    Request Body: { "image": "<base64 or data URL>" }
    Response:     { extracted: {...}, products: [ card, card, card ] }
    """
    try:
        body = request.get_json(force=True, silent=True) or {}
        image_data = body.get("image", "")

        if not image_data:
            return _error_response("MISSING_IMAGE", "Image data is required in 'image' field", 400)

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
                "message": "Could not identify product from image",
            })), 200

        # Search ES – fetch top 3 as product cards
        fetcher = get_es_fetcher()
        search_query = f"{brand_name} {product_name}".strip() if brand_name else product_name
        result = fetcher.search({
            "q": search_query,
            "size": 3,
            "category_group": extracted.get("category_group", ""),
        })

        raw_products = result.get("products", [])
        # Transform to standardized product cards
        product_cards = [transform_to_product_card(p) for p in raw_products if p]

        log.info(f"SCANNER_COMPLETE | query={search_query} | found={len(product_cards)}")

        return jsonify(_success_response({
            "extracted": extracted,
            "products": product_cards,
        })), 200

    except Exception as e:
        log.error(f"SCANNER_ERROR | error={e}", exc_info=True)
        return _error_response("INTERNAL_ERROR", "Failed to process image", 500)


# ============================================================================
# Catalogue API - List Products by Subcategory (product cards)
# ============================================================================

@bp.route("/api/v1/catalogue", methods=["GET"])
def get_catalogue() -> Tuple[Dict[str, Any], int]:
    """
    Returns products for a subcategory as standardized product cards.

    Query Parameters:
        subcategory (required): ES path e.g. 'f_and_b/food/light_bites/chips_and_crisps'
        page (optional): 0-indexed page number (default 0)
        size (optional): items per page, 1-100 (default 20)
        sort (optional): 'flean_score' (default) or 'price'
    """
    try:
        subcategory = request.args.get("subcategory", "").strip()
        if not subcategory:
            return _error_response(
                "MISSING_SUBCATEGORY",
                "Query parameter 'subcategory' is required (e.g. 'f_and_b/food/light_bites/chips_and_crisps')",
                400,
            )

        try:
            page = max(0, int(request.args.get("page", 0)))
        except (TypeError, ValueError):
            page = 0
        try:
            size = max(1, min(int(request.args.get("size", 20)), 100))
        except (TypeError, ValueError):
            size = 20
        sort_by = request.args.get("sort", "flean_score").strip().lower()
        if sort_by not in ("flean_score", "price"):
            sort_by = "flean_score"

        log.info(f"CATALOGUE_REQUEST | subcategory={subcategory} | page={page} | size={size} | sort={sort_by}")

        fetcher = get_es_fetcher()
        result = fetcher.search_by_subcategory(subcategory=subcategory, page=page, size=size, sort_by=sort_by)

        raw_products = result.get("products", [])
        product_cards = [transform_to_product_card(p) for p in raw_products if p]
        meta = result.get("meta", {})

        log.info(f"CATALOGUE_COMPLETE | subcategory={subcategory} | total={meta.get('total', 0)} | returned={len(product_cards)}")

        return jsonify(_success_response({"products": product_cards}, meta=meta)), 200

    except Exception as e:
        log.error(f"CATALOGUE_ERROR | error={e}", exc_info=True)
        return _error_response("INTERNAL_ERROR", "Failed to fetch catalogue", 500)


# ============================================================================
# Catalogue Mapping API - Category → Subcategory → ES Path
# ============================================================================

@lru_cache(maxsize=1)
def _load_category_mapping() -> Dict[str, Any]:
    """Load and cache the category mapping JSON."""
    path = DATA_DIR / "category_mapping.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@bp.route("/api/v1/catalogue/mapping", methods=["GET"])
def get_catalogue_mapping() -> Tuple[Dict[str, Any], int]:
    """
    Returns the full category → subcategory → ES path mapping.

    The developer uses this to know which 'subcategory' value to pass
    to GET /api/v1/catalogue?subcategory=<es_path>.
    """
    try:
        mapping = _load_category_mapping()
        return jsonify(_success_response(mapping)), 200
    except FileNotFoundError:
        log.error("CATALOGUE_MAPPING_NOT_FOUND")
        return _error_response("NOT_FOUND", "Category mapping file not found", 404)
    except Exception as e:
        log.error(f"CATALOGUE_MAPPING_ERROR | error={e}", exc_info=True)
        return _error_response("INTERNAL_ERROR", "Failed to load category mapping", 500)


# ============================================================================
# Unified Products API - Search + Catalogue with Pagination and Filters
# ============================================================================

# Sort & Filter Constants (shared with simple_search)
VALID_SORT_OPTIONS = {
    "relevance",      # Default: ES score
    "price_asc",      # Price Low to High
    "price_desc",     # Price High to Low
    "protein_desc",   # Protein High to Low
    "fiber_desc",     # Fibre High to Low
    "fat_asc",        # Fat Low to High
}

VALID_PRICE_RANGES = {"below_99", "100_249", "250_499", "above_500"}
VALID_FLEAN_SCORES = {"10", "9_plus", "8_plus", "7_plus"}
VALID_PREFERENCES = {"no_palm_oil", "no_added_sugar", "no_additives"}
VALID_DIETARY = {"dairy_free", "gluten_free"}


def _validate_filters(filters: Optional[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Validate the filters object from the request."""
    if not filters:
        return None, None
    if not isinstance(filters, dict):
        return None, "filters must be an object"

    validated: Dict[str, Any] = {}

    price_range = filters.get("price_range")
    if price_range:
        if price_range not in VALID_PRICE_RANGES:
            return None, f"Invalid price_range: '{price_range}'. Valid: {sorted(VALID_PRICE_RANGES)}"
        validated["price_range"] = price_range

    flean_score = filters.get("flean_score")
    if flean_score:
        if flean_score not in VALID_FLEAN_SCORES:
            return None, f"Invalid flean_score: '{flean_score}'. Valid: {sorted(VALID_FLEAN_SCORES)}"
        validated["flean_score"] = flean_score

    preferences = filters.get("preferences", [])
    if preferences:
        if not isinstance(preferences, list):
            return None, "preferences must be an array"
        invalid = [p for p in preferences if p not in VALID_PREFERENCES]
        if invalid:
            return None, f"Invalid preferences: {invalid}. Valid: {sorted(VALID_PREFERENCES)}"
        validated["preferences"] = preferences

    dietary = filters.get("dietary", [])
    if dietary:
        if not isinstance(dietary, list):
            return None, "dietary must be an array"
        invalid = [d for d in dietary if d not in VALID_DIETARY]
        if invalid:
            return None, f"Invalid dietary: {invalid}. Valid: {sorted(VALID_DIETARY)}"
        validated["dietary"] = dietary

    return validated if validated else None, None


@bp.route("/api/v1/products", methods=["POST"])
def get_products_unified() -> Tuple[Dict[str, Any], int]:
    """
    Unified Products API: Search and/or browse products with pagination and filters.

    This endpoint combines the capabilities of:
    - POST /search: Text search with filters
    - GET /api/v1/catalogue: Category browsing with pagination

    Request Body:
        {
            "query": "protein bars",              // optional - text search
            "subcategory": "f_and_b/food/...",    // optional - ES category path
            "page": 0,                            // optional - 0-indexed (default 0)
            "size": 20,                           // optional - 1-100 (default 20)
            "sort_by": "relevance",               // optional - see valid options
            "filters": {                          // optional - all filter types
                "price_range": "below_99",
                "flean_score": "9_plus",
                "preferences": ["no_palm_oil"],
                "dietary": ["gluten_free"]
            }
        }

    Behavior:
        - query only: Full-text search with filters
        - subcategory only: Browse category products with filters
        - both: Search within specific category
        - neither: Returns 400 error

    Sort Options:
        - relevance (default)
        - price_asc, price_desc
        - protein_desc, fiber_desc, fat_asc

    Response (200):
        {
            "success": true,
            "data": { "products": [...product cards...] },
            "meta": {
                "total": 245,
                "page": 0,
                "size": 20,
                "total_pages": 13,
                "has_next": true,
                "has_prev": false,
                "query": "protein bars",
                "subcategory": null,
                "sort_by": "relevance",
                "filters_applied": { ... }
            }
        }
    """
    try:
        body = request.get_json(force=True, silent=True) or {}

        # Extract and validate query
        query = body.get("query")
        if query is not None:
            if not isinstance(query, str):
                return _error_response("INVALID_QUERY", "'query' must be a string", 400)
            query = query.strip() if query else None

        # Extract and validate subcategory
        subcategory = body.get("subcategory")
        if subcategory is not None:
            if not isinstance(subcategory, str):
                return _error_response("INVALID_SUBCATEGORY", "'subcategory' must be a string", 400)
            subcategory = subcategory.strip() if subcategory else None

        # Require at least one of query or subcategory
        if not query and not subcategory:
            return _error_response(
                "MISSING_PARAMETER",
                "At least one of 'query' or 'subcategory' must be provided",
                400
            )

        # Parse pagination
        try:
            page = max(0, int(body.get("page", 0)))
        except (TypeError, ValueError):
            page = 0
        try:
            size = max(1, min(int(body.get("size", 20)), 100))
        except (TypeError, ValueError):
            size = 20

        # Validate sort_by
        sort_by = body.get("sort_by", "relevance")
        if sort_by and sort_by not in VALID_SORT_OPTIONS:
            return _error_response(
                "INVALID_SORT",
                f"Invalid 'sort_by': '{sort_by}'. Valid: {sorted(VALID_SORT_OPTIONS)}",
                400
            )

        # Validate filters
        raw_filters = body.get("filters")
        validated_filters, filter_error = _validate_filters(raw_filters)
        if filter_error:
            return _error_response("INVALID_FILTERS", filter_error, 400)

        log.info(
            f"PRODUCTS_UNIFIED_REQUEST | query={query} | subcategory={subcategory} | "
            f"page={page} | size={size} | sort={sort_by} | filters={validated_filters}"
        )

        # Execute unified search
        fetcher = get_es_fetcher()
        result = fetcher.search_products_unified(
            query=query,
            subcategory=subcategory,
            page=page,
            size=size,
            sort_by=sort_by,
            filters=validated_filters
        )

        # Check for errors in result
        meta = result.get("meta", {})
        if meta.get("error"):
            log.error(f"PRODUCTS_UNIFIED_ES_ERROR | error={meta.get('error')}")
            return _error_response("SEARCH_ERROR", f"Search failed: {meta.get('error')}", 500)

        # Transform products to standardized product cards
        raw_products = result.get("products", [])
        product_cards = [transform_to_product_card(p) for p in raw_products if p]

        log.info(
            f"PRODUCTS_UNIFIED_COMPLETE | query={query} | subcategory={subcategory} | "
            f"total={meta.get('total', 0)} | returned={len(product_cards)}"
        )

        return jsonify(_success_response({"products": product_cards}, meta=meta)), 200

    except Exception as e:
        log.error(f"PRODUCTS_UNIFIED_ERROR | error={e}", exc_info=True)
        return _error_response("INTERNAL_ERROR", "Failed to fetch products", 500)
