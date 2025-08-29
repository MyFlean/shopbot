# shopping_bot/data_fetchers/es_products.py
from __future__ import annotations

import asyncio
import copy
import os
import re
from typing import Any, Dict, List, Optional

import requests

from ..enums import BackendFunction
from ..llm_service import LLMService
from . import register_fetcher

# Configuration flags
USE_MOCK_DATA = os.getenv("USE_MOCK_DATA", "true").lower() in {"1", "true", "yes", "on"}

# ES Configuration
ELASTIC_BASE = os.getenv(
    "ELASTIC_BASE",
    "https://ecf1f6c12cba494b8dd14b854befb208.asia-south1.gcp.elastic-cloud.com:443",
)
ELASTIC_INDEX = os.getenv("ELASTIC_INDEX", "flean_products_v2")
ELASTIC_API_KEY = os.getenv("ELASTIC_API_KEY", "")
TIMEOUT = int(os.getenv("ELASTIC_TIMEOUT_SECONDS", "10"))

# Mock product data for testing
MOCK_PRODUCTS = [
    {
        "id": "01K184RAFREPPDPHRG0TZ4BVPY",
        "name": "ADONIS Ds Acne Facewash",
        "brand": "ADONIS",
        "price": 299,
        "mrp": 399,
        "category": "personal_care",
        "category_paths": ["personal_care", "face_care", "cleansers"],
        "description": "Advanced acne fighting face wash with salicylic acid",
        "protein_g": None,
        "carbs_g": None,
        "fat_g": None,
        "calories": None,
        "health_claims": ["Acne Fighting", "Deep Cleansing"],
        "dietary_labels": [],
        "image": "https://example.com/adonis-acne-facewash.jpg",
        "ingredients": "Water, Salicylic Acid, Glycerin, Sodium Lauryl Sulfate",
        "score": 8.5,
        "rank": 1
    },
    {
        "id": "01K184RAGX2295G86Y822CNGWK",
        "name": "ADONIS Ds Pure Mild Foaming Cleanser",
        "brand": "ADONIS",
        "price": 349,
        "mrp": 449,
        "category": "personal_care",
        "category_paths": ["personal_care", "face_care", "cleansers"],
        "description": "Gentle foaming cleanser for sensitive skin",
        "protein_g": None,
        "carbs_g": None,
        "fat_g": None,
        "calories": None,
        "health_claims": ["Mild Formula", "Sensitive Skin"],
        "dietary_labels": [],
        "image": "https://example.com/adonis-foaming-cleanser.jpg",
        "ingredients": "Water, Cocamidopropyl Betaine, Glycerin, Aloe Extract",
        "score": 8.2,
        "rank": 2
    },
    {
        "id": "AESTU00000003",
        "name": "Theracne365 Clear Deep Cleansing Foam - (60ml)",
        "brand": "Theracne365",
        "price": 199,
        "mrp": 249,
        "category": "personal_care",
        "category_paths": ["personal_care", "face_care", "cleansers"],
        "description": "Deep cleansing foam for acne-prone skin",
        "protein_g": None,
        "carbs_g": None,
        "fat_g": None,
        "calories": None,
        "health_claims": ["Deep Cleansing", "Acne Control"],
        "dietary_labels": [],
        "image": "https://example.com/theracne-foam-60ml.jpg",
        "ingredients": "Water, Niacinamide, Tea Tree Oil, Zinc PCA",
        "score": 7.9,
        "rank": 3
    },
    {
        "id": "AHAGL00000001",
        "name": "Advanced Face Wash Gel - (50 g)",
        "brand": "AHAGLOW",
        "price": 125,
        "mrp": 150,
        "category": "personal_care",
        "category_paths": ["personal_care", "face_care", "cleansers"],
        "description": "AHA-based exfoliating face wash gel",
        "protein_g": None,
        "carbs_g": None,
        "fat_g": None,
        "calories": None,
        "health_claims": ["Exfoliating", "Brightening"],
        "dietary_labels": [],
        "image": "https://example.com/ahaglow-gel-50g.jpg",
        "ingredients": "Water, Glycolic Acid, Lactic Acid, Hyaluronic Acid",
        "score": 7.6,
        "rank": 4
    },
    {
        "id": "01K184RAH26Y4KBPP5X6E306VK",
        "name": "Aminu AHA Face Wash",
        "brand": "Aminu",
        "price": 450,
        "mrp": 550,
        "category": "personal_care",
        "category_paths": ["personal_care", "face_care", "cleansers"],
        "description": "Premium AHA face wash for smooth skin",
        "protein_g": None,
        "carbs_g": None,
        "fat_g": None,
        "calories": None,
        "health_claims": ["AHA Formula", "Skin Smoothing"],
        "dietary_labels": [],
        "image": "https://example.com/aminu-aha-facewash.jpg",
        "ingredients": "Water, Glycolic Acid, Mandelic Acid, Ceramides",
        "score": 8.8,
        "rank": 5
    },
    {
        "id": "01K184RAH5QH18CQPATNZQ6MBC",
        "name": "Anua 8 Hyaluronic Acid + Squalane Moisturizing Gentle Gel Cleanser",
        "brand": "Anua",
        "price": 899,
        "mrp": 1199,
        "category": "personal_care",
        "category_paths": ["personal_care", "face_care", "cleansers"],
        "description": "Hydrating gel cleanser with 8 types of hyaluronic acid",
        "protein_g": None,
        "carbs_g": None,
        "fat_g": None,
        "calories": None,
        "health_claims": ["8 Hyaluronic Acids", "Deep Hydration"],
        "dietary_labels": [],
        "image": "https://example.com/anua-ha-cleanser.jpg",
        "ingredients": "Water, Hyaluronic Acid Complex, Squalane, Panthenol",
        "score": 9.2,
        "rank": 6
    }
]

# Food & Beverage mock products for variety
MOCK_FOOD_PRODUCTS = [
    {
        "id": "PROTEIN_POWDER_001",
        "name": "MuscleBlaze Whey Protein Isolate",
        "brand": "MuscleBlaze",
        "price": 1899,
        "mrp": 2499,
        "category": "f_and_b",
        "category_paths": ["f_and_b", "health_nutrition", "protein_supplements"],
        "description": "Premium whey protein isolate for muscle building",
        "protein_g": 25.0,
        "carbs_g": 2.0,
        "fat_g": 0.5,
        "calories": 110,
        "health_claims": ["High Protein", "Fast Absorption", "Muscle Building"],
        "dietary_labels": ["LACTOSE_FREE"],
        "image": "https://example.com/muscleblaze-isolate.jpg",
        "ingredients": "Whey Protein Isolate, Natural Flavors, Stevia",
        "score": 9.1,
        "rank": 1
    },
    {
        "id": "PROTEIN_POWDER_002",
        "name": "Optimum Nutrition Gold Standard Whey",
        "brand": "Optimum Nutrition",
        "price": 3299,
        "mrp": 3999,
        "category": "f_and_b",
        "category_paths": ["f_and_b", "health_nutrition", "protein_supplements"],
        "description": "World's best-selling whey protein powder",
        "protein_g": 24.0,
        "carbs_g": 3.0,
        "fat_g": 1.0,
        "calories": 120,
        "health_claims": ["Gold Standard", "Proven Quality", "Muscle Recovery"],
        "dietary_labels": [],
        "image": "https://example.com/optimum-gold-standard.jpg",
        "ingredients": "Whey Protein Isolates, Whey Protein Concentrates, Natural Flavors",
        "score": 9.5,
        "rank": 2
    },
    {
        "id": "SNACKS_001",
        "name": "Lays Classic Salted Chips",
        "brand": "Lays",
        "price": 20,
        "mrp": 20,
        "category": "f_and_b",
        "category_paths": ["f_and_b", "snacks", "chips"],
        "description": "Classic salted potato chips",
        "protein_g": 2.0,
        "carbs_g": 15.0,
        "fat_g": 10.0,
        "calories": 150,
        "health_claims": [],
        "dietary_labels": ["VEGETARIAN"],
        "image": "https://example.com/lays-classic.jpg",
        "ingredients": "Potatoes, Vegetable Oil, Salt",
        "score": 7.2,
        "rank": 3
    }
]

def _get_mock_products_for_query(query: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return skincare products only; count driven by product_intent.

    - is_this_good  → 1 product (SPM upstream)
    - others        → 4 products (MPM upstream)
    """
    # Always use skincare/personal care mock set
    base_products: List[Dict[str, Any]] = MOCK_PRODUCTS[:]

    # Apply optional filters
    filtered_products: List[Dict[str, Any]] = []
    for product in base_products:
        # Price filter
        if params.get("price_max") and product["price"] > params["price_max"]:
            continue
        if params.get("price_min") and product["price"] < params["price_min"]:
            continue
        # Brand filter
        if params.get("brands") and product["brand"] not in params["brands"]:
            continue
        # Category filter (force personal care only if present)
        if params.get("category_group") and product.get("category") and product["category"] != params["category_group"]:
            continue
        filtered_products.append(product)

    # Sort by score (descending)
    filtered_products.sort(key=lambda x: x.get("score", 0), reverse=True)

    # Decide count from product_intent
    product_intent = str(params.get("product_intent") or "show_me_options").strip().lower()
    target_count = 1 if product_intent == "is_this_good" else 4

    pool = filtered_products or base_products
    return pool[:target_count]

def _create_mock_response(query: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Create mock response that matches the real ES response format"""
    products = _get_mock_products_for_query(query, params)
    
    # Update ranks
    for i, product in enumerate(products, 1):
        product["rank"] = i
    
    return {
        "meta": {
            "total_hits": len(products),
            "returned": len(products),
            "took_ms": 15,  # Mock timing
            "query_successful": True,
            "mock_data": True  # Flag to indicate this is mock data
        },
        "products": products
    }

# Enhanced query template with better field coverage
BASE_BODY: Dict[str, Any] = {
    "track_total_hits": True,
    "size": 20,
    "_source": {
        "includes": [
            "id", "name", "brand", "price", "mrp", "category_paths",
            "ingredients.raw_text", "ingredients.raw_text_new",
            "category_data.nutritional.nutri_breakdown.*",
            "category_data.nutritional.raw_text",
            "package_claims.*", "tags_and_sentiments.tags.*", 
            "hero_image.*", "description", "use"
        ]
    },
    "query": {
        "function_score": {
            "query": {
                "bool": {
                    "filter": [],
                    "must": [],
                    "should": [],
                    "minimum_should_match": 0
                }
            },
            "functions": [],
            "score_mode": "sum",
            "boost_mode": "sum"
        }
    },
    "sort": [
        {"_score": "desc"},
        {"category_data.nutritional.nutri_breakdown.protein_g": {"order": "desc"}}
    ],
    "highlight": {
        "pre_tags": ["<em class=\"hl\">"],
        "post_tags": ["</em>"],
        "fields": {
            "name": {},
            "ingredients.raw_text": {},
            "ingredients.raw_text_new": {},
            "category_data.nutritional.raw_text": {}
        }
    },
    "aggs": {
        "by_brand": {"terms": {"field": "brand", "size": 10}},
        "dietary_labels": {"terms": {"field": "package_claims.dietary_labels", "size": 10}},
        "protein_ranges": {
            "range": {
                "field": "category_data.nutritional.nutri_breakdown.protein_g",
                "ranges": [
                    {"to": 5}, 
                    {"from": 5, "to": 10}, 
                    {"from": 10, "to": 20}, 
                    {"from": 20}
                ]
            }
        }
    }
}

# Text cleaning
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")

def _clean_text(s: Optional[str]) -> Optional[str]:
    if not s:
        return s
    s = TAG_RE.sub("", s)
    s = WS_RE.sub(" ", s).strip()
    return s

def _extract_protein(src: Dict[str, Any]) -> Optional[float]:
    try:
        v = (
            src.get("category_data", {})
            .get("nutritional", {}) 
            .get("nutri_breakdown", {})
            .get("protein_g")
        )
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None

def _get_best_image(hero: Dict[str, Any]) -> Optional[str]:
    if not isinstance(hero, dict):
        return None
    # Try standard resolutions first
    for size in ["640", "750", "828", "1080", "256", "384"]:
        if hero.get(size):
            return hero[size]
    # Fallback to any available image
    for v in hero.values():
        if isinstance(v, str) and v.strip():
            return v
    return None

def _extract_highlight(hit: Dict[str, Any]) -> Optional[str]:
    hl = hit.get("highlight", {})
    for field in ["name", "package_claims.dietary_labels", "ingredients.raw_text"]:
        if field in hl and hl[field]:
            return _clean_text(hl[field][0])
    return None

def _extract_product_terms(query_text: str) -> str:
    """Extract meaningful product terms from natural language query"""
    # Words to remove that don't help product matching
    stop_words = {
        'recommend', 'suggest', 'show', 'find', 'get', 'buy', 'purchase', 'want', 'need',
        'me', 'some', 'any', 'good', 'nice', 'best', 'great', 'options', 'products', 'items',
        'under', 'above', 'below', 'within', 'around', 'about', 'less', 'more', 'than',
        'rupees', 'rs', 'inr', 'price', 'cost', 'budget', 'cheap', 'expensive',
        'can', 'you', 'please', 'help', 'looking', 'for'
    }
    
    # Extract words, remove numbers and stop words
    words = re.findall(r'\b[a-zA-Z]+\b', query_text.lower())
    product_words = [w for w in words if w not in stop_words and len(w) > 2]
    
    return ' '.join(product_words)

def _build_must_query(product_terms: str) -> List[Dict[str, Any]]:
    """Build the main search query with enhanced field coverage"""
    if not product_terms.strip():
        return [{"match_all": {}}]
    
    return [{
        "multi_match": {
            "query": product_terms,
            "type": "most_fields",
            "operator": "or",  # Changed from "and" to be more lenient
            "fuzziness": "AUTO",
            "lenient": True,
            "fields": [
                "name^6",                                    # Product name is most important
                "ingredients.raw_text^8",                   # Ingredients are crucial
                "ingredients.raw_text_new^8",
                "category_data.nutritional.raw_text^3",     # Nutritional descriptions
                "description^2",                            # Product descriptions
                "use",                                      # Usage information
                "package_claims.health_claims^2",           # Health claims
                "package_claims.dietary_labels^2",          # Dietary labels
                "tags_and_sentiments.seo_keywords.*^2",     # SEO keywords
                "tags_and_sentiments.tags.*^1.5"           # Product tags
            ]
        }
    }]

def _build_should_boosts(product_terms: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build should clauses for better relevance based on query content"""
    should_clauses = []
    terms_lower = product_terms.lower()
    
    # Boost for exact phrase matches in name
    if len(product_terms.split()) > 1:
        should_clauses.append({
            "match_phrase": {
                "name": {
                    "query": product_terms,
                    "boost": 10
                }
            }
        })
    
    # Category-specific boosts
    if any(term in terms_lower for term in ['bread', 'roti', 'pav', 'bun']):
        should_clauses.extend([
            {"wildcard": {"category_paths": {"value": "*bread*", "boost": 5.0}}},
            {"match": {"name": {"query": "whole wheat", "boost": 3}}},
            {"match": {"package_claims.dietary_labels": {"query": "whole grain", "boost": 2}}}
        ])
    
    if any(term in terms_lower for term in ['chips', 'snacks', 'namkeen']):
        should_clauses.extend([
            {"wildcard": {"category_paths": {"value": "*snacks*", "boost": 5.0}}},
            {"match": {"package_claims.health_claims": {"query": "baked not fried", "boost": 2}}}
        ])
    
    if any(term in terms_lower for term in ['protein', 'fitness', 'workout']):
        should_clauses.extend([
            {"match": {"package_claims.health_claims": {"query": "high protein", "boost": 5}}},
            {"match": {"package_claims.health_claims": {"query": "source of protein", "boost": 3}}},
            {"range": {"category_data.nutritional.nutri_breakdown.protein_g": {"gte": 10, "boost": 3}}}
        ])
    
    # Dietary preference boosts
    dietary_terms = params.get("dietary_terms", [])
    for term in dietary_terms:
        should_clauses.append({
            "match": {
                "package_claims.dietary_labels": {
                    "query": term,
                    "boost": 8
                }
            }
        })
    
    # Brand preference boosts
    brands = params.get("brands", [])
    for brand in brands:
        should_clauses.append({
            "match": {
                "brand": {
                    "query": brand,
                    "boost": 6
                }
            }
        })
    
    return should_clauses

def _build_function_score_functions(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build function score functions for better ranking"""
    functions = []
    
    # Protein content boost
    protein_weight = params.get("protein_weight", 1.5)
    if protein_weight > 0:
        functions.append({
            "field_value_factor": {
                "field": "category_data.nutritional.nutri_breakdown.protein_g",
                "modifier": "ln1p",
                "missing": 0
            },
            "weight": float(protein_weight)
        })
    
    # Health claims boost
    functions.append({
        "filter": {
            "exists": {"field": "package_claims.health_claims"}
        },
        "weight": 1.2
    })
    
    # Popular brands boost
    popular_brands = ["Nestle", "Britannia", "Parle", "ITC", "Amul"]
    functions.append({
        "filter": {
            "terms": {"brand": popular_brands}
        },
        "weight": 1.1
    })
    
    return functions

def _build_enhanced_es_query(params: Dict[str, Any]) -> Dict[str, Any]:
    """Build sophisticated ES query using the enhanced structure"""
    p = params or {}
    body = copy.deepcopy(BASE_BODY)
    
    # Extract meaningful product terms
    original_query = p.get("q", "").strip()
    product_terms = _extract_product_terms(original_query)
    
    print(f"DEBUG: Original query: '{original_query}'")
    print(f"DEBUG: Extracted product terms: '{product_terms}'")
    
    # Result size
    size = p.get("size", 20)
    body["size"] = max(1, min(50, int(size)))
    
    # Build query components
    bool_query = body["query"]["function_score"]["query"]["bool"]
    
    # Filters (hard constraints)
    filters = bool_query["filter"]
    
    # Category filter
    if p.get("category_group"):
        filters.append({"term": {"category_group": p["category_group"]}})
    
    # Price range filter
    if p.get("price_min") is not None or p.get("price_max") is not None:
        price_range = {}
        if p.get("price_min") is not None:
            price_range["gte"] = float(p["price_min"])
        if p.get("price_max") is not None:
            price_range["lte"] = float(p["price_max"])
        filters.append({"range": {"price": price_range}})
    
    # Brand filter
    if p.get("brands"):
        brands = p["brands"] if isinstance(p["brands"], list) else [p["brands"]]
        filters.append({"terms": {"brand": brands}})
    
    # Dietary requirements filter
    if p.get("dietary_terms"):
        dietary = p["dietary_terms"] if isinstance(p["dietary_terms"], list) else [p["dietary_terms"]]
        filters.append({"terms": {"package_claims.dietary_labels": dietary}})
    
    # Main search query
    bool_query["must"] = _build_must_query(product_terms)
    
    # Relevance boosts
    should_clauses = _build_should_boosts(product_terms, p)
    bool_query["should"] = should_clauses
    bool_query["minimum_should_match"] = 1 if should_clauses else 0
    
    # Function scoring
    body["query"]["function_score"]["functions"] = _build_function_score_functions(p)
    
    return body

def _transform_results(raw_response: Dict[str, Any]) -> Dict[str, Any]:
    """Transform ES response with enhanced field coverage"""
    hits = raw_response.get("hits", {}).get("hits", [])
    total = raw_response.get("hits", {}).get("total", {}).get("value", len(hits))
    took = raw_response.get("took", 0)
    
    products = []
    for rank, hit in enumerate(hits, 1):
        src = hit.get("_source", {})
        score = hit.get("_score", 0)
        
        # Extract nutritional info
        nutrition = src.get("category_data", {}).get("nutritional", {}).get("nutri_breakdown", {})
        
        # Extract package claims
        package_claims = src.get("package_claims", {})
        health_claims = package_claims.get("health_claims", [])
        dietary_labels = package_claims.get("dietary_labels", [])
        
        product = {
            "rank": rank,
            "score": round(score, 3) if isinstance(score, (int, float)) else score,
            "id": src.get("id", f"prod_{rank}"),
            "name": _clean_text(src.get("name", "")),
            "brand": src.get("brand", ""),
            "price": src.get("price"),
            "mrp": src.get("mrp"),
            "category": src.get("category_group", ""),
            "category_paths": src.get("category_paths", []),
            "description": _clean_text(src.get("description", "")),
            
            # Nutritional information
            "protein_g": nutrition.get("protein_g"),
            "carbs_g": nutrition.get("carbs_g"),
            "fat_g": nutrition.get("fat_g"),
            "calories": nutrition.get("energy_kcal"),
            
            # Claims and labels
            "health_claims": health_claims if isinstance(health_claims, list) else [],
            "dietary_labels": dietary_labels if isinstance(dietary_labels, list) else [],
            
            # Image
            "image": _get_best_image(src.get("hero_image", {})),
            
            # Ingredients (useful for detailed info)
            "ingredients": _clean_text(src.get("ingredients", {}).get("raw_text", "")),
        }
        
        # Add highlight if available
        highlight = _extract_highlight(hit)
        if highlight:
            product["highlight"] = highlight
            
        products.append(product)
    
    return {
        "meta": {
            "total_hits": total,
            "returned": len(products),
            "took_ms": took,
            "query_successful": True
        },
        "products": products
    }

class ElasticsearchProductsFetcher:
    """Enhanced Elasticsearch fetcher with mock data toggle"""
    
    def __init__(self, base_url: str = None, index: str = None, api_key: str = None):
        self.base_url = base_url or ELASTIC_BASE
        self.index = index or ELASTIC_INDEX
        self.api_key = api_key or ELASTIC_API_KEY
        
        if not USE_MOCK_DATA and not self.api_key:
            raise RuntimeError("ELASTIC_API_KEY environment variable is required when USE_MOCK_DATA=false")
            
        self.endpoint = f"{self.base_url}/{self.index}/_search"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"ApiKey {self.api_key}"
        } if self.api_key else {}
    
    def search(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute search with mock data toggle"""
        
        # Check if we should use mock data
        if USE_MOCK_DATA:
            print(f"DEBUG: Using mock data (USE_MOCK_DATA=true)")
            query = params.get("q", "")
            mock_response = _create_mock_response(query, params)
            
            print(f"DEBUG: Mock query returned {mock_response['meta']['total_hits']} products")
            if mock_response['products']:
                print("DEBUG: Top mock results:")
                for i, product in enumerate(mock_response['products'][:3], 1):
                    print(f"  {i}. {product['name']} - ₹{product['price']} (score: {product['score']})")
            
            return mock_response
        
        # Use real Elasticsearch
        try:
            # Use the enhanced query builder
            query_body = _build_enhanced_es_query(params)
            
            # Debug logging
            print(f"DEBUG: Enhanced ES Query Structure:")
            print(f"  - Original query: {params.get('q', '')}")
            print(f"  - Category: {params.get('category_group', 'all')}")
            print(f"  - Price max: {params.get('price_max', 'no limit')}")
            print(f"  - Dietary terms: {params.get('dietary_terms', 'none')}")
            
            response = requests.post(
                self.endpoint,
                headers=self.headers,
                json=query_body,
                timeout=TIMEOUT
            )
            response.raise_for_status()
            
            raw_data = response.json()
            result = _transform_results(raw_data)
            
            print(f"DEBUG: Enhanced ES query found {result['meta']['total_hits']} products")
            
            # Show top results for debugging
            if result['products']:
                print("DEBUG: Top ES results:")
                for i, product in enumerate(result['products'][:3], 1):
                    print(f"  {i}. {product['name']} - ₹{product['price']} (score: {product['score']})")
            
            return result
            
        except requests.exceptions.Timeout:
            print(f"DEBUG: ES timeout, falling back to mock data")
            return _create_mock_response(params.get("q", ""), params)
        except requests.exceptions.RequestException as e:
            print(f"DEBUG: ES request failed: {e}, falling back to mock data")
            return _create_mock_response(params.get("q", ""), params)
        except Exception as e:
            print(f"DEBUG: Unexpected ES error: {e}, falling back to mock data")
            return _create_mock_response(params.get("q", ""), params)

# Parameter extraction and normalization
def _extract_defaults_from_context(ctx) -> Dict[str, Any]:
    """Extract search parameters from user context"""
    session = ctx.session or {}
    assessment = session.get("assessment", {})
    
    # Get the original query
    query = assessment.get("original_query") or session.get("last_query", "")
    
    # Extract budget
    budget = session.get("budget", {})
    price_min = None
    price_max = None
    
    if isinstance(budget, dict):
        price_min = budget.get("min")
        price_max = budget.get("max")
    elif isinstance(budget, str):
        # Try to parse budget string like "100-200" or "under 100"
        budget_lower = budget.lower()
        if "under" in budget_lower or "below" in budget_lower:
            try:
                price_max = float(re.search(r'\d+', budget).group())
            except:
                pass
        elif "-" in budget:
            try:
                parts = budget.split("-")
                price_min = float(parts[0].strip())
                price_max = float(parts[1].strip())
            except:
                pass
    
    # Determine product_intent from context (default to show_me_options)
    product_intent = str(session.get("product_intent") or "show_me_options")
    # Size hint: 1 for is_this_good; else 4
    size_hint = 1 if product_intent == "is_this_good" else 4

    return {
        "q": query,
        "size": size_hint,
        "category_group": session.get("category_group", "personal_care"),  # Force non-food default
        "brands": session.get("brands"),
        "dietary_terms": session.get("dietary_requirements"),
        "price_min": price_min,
        "price_max": price_max,
        "protein_weight": 1.5,
        "product_intent": product_intent,
    }

def _normalize_params(base_params: Dict[str, Any], llm_params: Dict[str, Any]) -> Dict[str, Any]:
    """Merge and normalize parameters"""
    # Start with base params
    final_params = dict(base_params)
    
    # Overlay LLM-extracted params
    for key, value in (llm_params or {}).items():
        if value is not None:
            final_params[key] = value
    
    # Normalize lists
    for list_field in ["brands", "dietary_terms"]:
        if list_field in final_params and final_params[list_field]:
            value = final_params[list_field]
            if isinstance(value, str):
                # Split string into list
                final_params[list_field] = [v.strip().upper() for v in value.replace(",", " ").split() if v.strip()]
            elif isinstance(value, list):
                # Clean existing list
                final_params[list_field] = [str(v).strip().upper() for v in value if str(v).strip()]
    
    # Ensure category_group is set
    if not final_params.get("category_group"):
        final_params["category_group"] = "f_and_b"
    
    # Clean up None values
    return {k: v for k, v in final_params.items() if v is not None}

async def build_search_params(ctx) -> Dict[str, Any]:
    """Build final search parameters from context + LLM analysis"""
    
    # Get base parameters from context
    base_params = _extract_defaults_from_context(ctx)
    
    # Use LLM to extract/normalize additional parameters (only if not using mock data)
    llm_params = {}
    
    if not USE_MOCK_DATA:
        try:
            llm_service = LLMService()
            llm_params = await llm_service.extract_es_params(ctx)
            print(f"DEBUG: LLM extracted params = {llm_params}")
        except Exception as e:
            print(f"DEBUG: LLM param extraction failed: {e}")
            llm_params = {}
    else:
        print(f"DEBUG: Skipping LLM param extraction (using mock data)")
    
    # Merge and normalize
    final_params = _normalize_params(base_params, llm_params)
    
    print(f"DEBUG: Final search params = {final_params}")
    
    # Store for debugging
    try:
        ctx.session.setdefault("debug", {})["last_search_params"] = final_params
    except:
        pass
    
    return final_params

# Global fetcher instance
_es_fetcher: Optional[ElasticsearchProductsFetcher] = None

def get_es_fetcher() -> ElasticsearchProductsFetcher:
    """Get singleton ES fetcher instance"""
    global _es_fetcher
    if _es_fetcher is None:
        _es_fetcher = ElasticsearchProductsFetcher()
    return _es_fetcher

# Async handlers for different functions
async def search_products_handler(ctx) -> Dict[str, Any]:
    """Main product search handler with mock data support"""
    params = await build_search_params(ctx)
    fetcher = get_es_fetcher()
    
    # Run in thread to avoid blocking (unless using mock data)
    if USE_MOCK_DATA:
        return fetcher.search(params)
    else:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: fetcher.search(params))

async def fetch_user_profile_handler(ctx) -> Dict[str, Any]:
    """User profile - return minimal data for now"""
    return {
        "user_id": ctx.user_id,
        "preferences": ctx.permanent.get("preferences", {}),
        "dietary_restrictions": ctx.permanent.get("dietary_restrictions", []),
        "favorite_brands": ctx.permanent.get("favorite_brands", []),
    }

async def fetch_purchase_history_handler(ctx) -> Dict[str, Any]:
    """Purchase history - return minimal data for now"""
    return {
        "recent_purchases": [],
        "favorite_categories": ["f_and_b"],
        "average_order_value": 0,
        "last_purchase_date": None,
    }

async def fetch_order_status_handler(ctx) -> Dict[str, Any]:
    """Order status - return minimal data for now"""
    return {
        "orders": [],
        "status": "No recent orders found",
    }

# Register all handlers
register_fetcher(BackendFunction.SEARCH_PRODUCTS, search_products_handler)
register_fetcher(BackendFunction.FETCH_USER_PROFILE, fetch_user_profile_handler)  
register_fetcher(BackendFunction.FETCH_PURCHASE_HISTORY, fetch_purchase_history_handler)
register_fetcher(BackendFunction.FETCH_ORDER_STATUS, fetch_order_status_handler)