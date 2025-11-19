# shopping_bot/routes/simple_search.py
"""
Simple Search Endpoint - Direct Elasticsearch Query Search

This endpoint takes a user query and performs a direct Elasticsearch search
without any filters, tags, categories, or subcategories.
Returns enriched product information.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from flask import Blueprint, jsonify, request

from ..data_fetchers.es_products import get_es_fetcher

log = logging.getLogger(__name__)
bp = Blueprint("simple_search", __name__)


def _parse_es_product_to_payload(es_product: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse Elasticsearch product response to the desired payload format.
    
    Maps ES fields to the required structure:
    - id, name, brand, price: Direct mapping
    - currency: Hardcoded to "INR"
    - unit_size: Hardcoded to "1"
    - image_url: From ES 'image' field
    - health_tags: Hardcoded for now (can be enhanced later with dietary_labels/health_claims)
    - flean_score: From ES 'flean_score' field
    - expert_counts: Hardcoded for now
    - in_stock: Hardcoded to True
    """
    # Extract basic fields
    product_id = es_product.get("id", "")
    name = es_product.get("name", "")
    brand = es_product.get("brand", "")
    price = es_product.get("price")
    image_url = es_product.get("image")  # This is the CDN URL
    flean_score = es_product.get("flean_score")
    
    # Build the payload
    payload = {
        "id": product_id,
        "name": name,
        "brand": brand,
        "price": price,
        "currency": "INR",  # Hardcoded
        "unit_size": "1",  # Hardcoded
        "image_url": image_url,
        "health_tags": ["Low sugar", "Better oils"],  # Hardcoded for now
        "flean_score": flean_score,
        "expert_counts": {  # Hardcoded for now
            "Picked by 5 experts": True,
            "trainers": 2,
            "nutritionists": 1,
            "doctors": 0
        },
        "in_stock": True  # Hardcoded
    }
    
    return payload


@bp.post("/search")
def simple_search() -> tuple[Dict[str, Any], int]:
    """
    Simple search endpoint that takes a query and returns enriched product information.
    
    Request body:
        {
            "query": "user search query"
        }
    
    Response:
        {
            "products": [
                {
                    "id": "...",
                    "name": "...",
                    "brand": "...",
                    "price": 299,
                    "currency": "INR",
                    "unit_size": "1",
                    "image_url": "https://...",
                    "health_tags": ["Low sugar", "Better oils"],
                    "flean_score": 8.6,
                    "expert_counts": {
                        "Picked by 5 experts": True,
                        "trainers": 2,
                        "nutritionists": 1,
                        "doctors": 0
                    },
                    "in_stock": true
                },
                ...
            ],
            "total_hits": 100,
            "returned": 20
        }
    """
    try:
        # Parse request
        data = request.get_json(force=True) or {}
        
        # Validate required field
        query = data.get("query")
        if not query or not isinstance(query, str) or not query.strip():
            return jsonify({
                "error": "Missing or invalid 'query' field. Expected a non-empty string."
            }), 400
        
        query = query.strip()
        log.info(f"SIMPLE_SEARCH_REQUEST | query='{query}'")
        
        # Get ES fetcher instance
        fetcher = get_es_fetcher()
        
        # Build simple params - only the query, no filters
        params = {
            "q": query,
            "size": 20  # Default to 20 results
        }
        
        # Perform ES search
        result = fetcher.search(params)
        
        # Extract products and meta from ES response
        es_products = result.get("products", [])
        meta = result.get("meta", {})
        total_hits = meta.get("total_hits", 0)
        
        log.info(
            f"SIMPLE_SEARCH_SUCCESS | query='{query}' | total_hits={total_hits} | returned={len(es_products)}"
        )
        
        # DEBUG: Print complete ES response structure for first product (if available)
        if es_products:
            first_product = es_products[0]
            try:
                # Pretty print the first product's complete structure
                product_json = json.dumps(first_product, indent=2, ensure_ascii=False, default=str)
                log.info("="*80)
                log.info("ES_RESPONSE_FIRST_PRODUCT | Complete structure:")
                log.info(product_json)
                log.info("="*80)
                
                # Also print all top-level keys available in products
                all_keys = set()
                for product in es_products[:3]:  # Check first 3 products
                    all_keys.update(product.keys())
                log.info(f"ES_RESPONSE_AVAILABLE_KEYS | Keys found in products: {sorted(all_keys)}")
            except Exception as e:
                log.warning(f"ES_RESPONSE_PRINT_ERROR | Could not print ES response: {e}")
        
        # Parse ES products to desired payload format
        parsed_products = []
        for es_product in es_products:
            try:
                parsed_product = _parse_es_product_to_payload(es_product)
                parsed_products.append(parsed_product)
            except Exception as e:
                log.warning(f"PRODUCT_PARSE_ERROR | product_id={es_product.get('id', 'unknown')} | error={e}")
                # Skip products that fail to parse
                continue
        
        # Log first parsed product for debugging
        if parsed_products:
            try:
                first_parsed_json = json.dumps(parsed_products[0], indent=2, ensure_ascii=False, default=str)
                log.info("="*80)
                log.info("PARSED_FIRST_PRODUCT | Transformed payload:")
                log.info(first_parsed_json)
                log.info("="*80)
            except Exception as e:
                log.warning(f"PARSED_PRODUCT_PRINT_ERROR | Could not print parsed product: {e}")
        
        # Return parsed products in desired format
        return jsonify({
            "products": parsed_products,
            "total_hits": total_hits,
            "returned": len(parsed_products)
        }), 200
        
    except Exception as exc:
        log.error(f"SIMPLE_SEARCH_ERROR | error={exc}", exc_info=True)
        return jsonify({
            "error": "Internal server error during search",
            "message": str(exc)
        }), 500

