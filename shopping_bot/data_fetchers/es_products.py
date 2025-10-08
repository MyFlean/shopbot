# shopping_bot/data_fetchers/es_products.py
"""
Elasticsearch Products Fetcher
──────────────────────────────
Enhanced with:
• Better brand handling using match queries
• Function score for percentile-based ranking
• Result quality checks with fallback strategies
• Minimum score thresholds
"""

from __future__ import annotations

import asyncio
from logging import log
import os
import re
from typing import Any, Dict, List, Optional
import json

import requests

from ..enums import BackendFunction
from . import register_fetcher
from ..scoring_config import build_function_score_functions

# ES Configuration
ELASTIC_BASE = (
    os.getenv("ES_URL")
    or os.getenv("ELASTIC_BASE",
        "https://adb98ad92e064025a9b2893e0589a3b5.asia-south1.gcp.elastic-cloud.com:443"
    )
)
ELASTIC_INDEX = os.getenv("ELASTIC_INDEX", "flean-v4")
ELASTIC_API_KEY = os.getenv("ES_API_KEY") or os.getenv("ELASTIC_API_KEY", "")
TIMEOUT = int(os.getenv("ELASTIC_TIMEOUT_SECONDS", "10"))

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

def _get_current_user_text(ctx) -> str:
    """Best-effort extraction of the CURRENT user utterance.

    Falls back through several likely attributes/locations and finally to any
    previously stored session text. This is critical to ensure each follow-up
    rebuilds ES params using the latest user intent delta.
    """
    # Direct attributes on context
    for attr in [
        "current_user_text",
        "current_text",
        "message_text",
        "user_message",
        "last_user_message",
        "text",
        "message",
    ]:
        try:
            value = getattr(ctx, attr, None)
            if isinstance(value, str) and value.strip():
                try:
                    print(f"DEBUG: CURRENT_TEXT_ATTR | attr={attr} | value='{value.strip()}'")
                except Exception:
                    pass
                return value.strip()
        except Exception:
            pass

    # Common session keys where pipelines may store the last turn text
    try:
        session = getattr(ctx, "session", {}) or {}
        for key in [
            "current_user_text",
            "latest_user_text",
            "last_user_message",
            "last_user_text",
            "last_query",
        ]:
            val = session.get(key)
            if isinstance(val, str) and val.strip():
                try:
                    print(f"DEBUG: CURRENT_TEXT_SESSION | key={key} | value='{val.strip()}'")
                except Exception:
                    pass
                return val.strip()
    except Exception:
        pass

    # Assessment-level fallbacks
    try:
        assessment = (getattr(ctx, "session", {}) or {}).get("assessment", {}) or {}
        val = assessment.get("original_query")
        if isinstance(val, str) and val.strip():
            try:
                print(f"DEBUG: CURRENT_TEXT_ASSESSMENT | value='{val.strip()}'")
            except Exception:
                pass
            return val.strip()
    except Exception:
        pass

    try:
        print("DEBUG: CURRENT_TEXT_FALLBACK_EMPTY")
    except Exception:
        pass
    return ""

def _build_enhanced_es_query(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build ES query with improved brand handling and percentile-based ranking.
    Uses function_score for quality-based ranking.
    """
    p = params or {}

    # Determine size based on product intent
    desired_size = 1 if str(p.get("product_intent", "")).strip().lower() == "is_this_good" else 10

    # Base document structure with enhanced source fields
    body: Dict[str, Any] = {
        "size": int(p.get("size", desired_size)) if isinstance(p.get("size"), int) else desired_size,
        "track_total_hits": True,
        "_source": {
            "includes": [
                "id", "name", "brand", "price", "mrp", "hero_image.*",
                "package_claims.*", "category_group", "category_paths", 
                "description", "use", "flean_score.*",
                "stats.adjusted_score_percentiles.*",
                "stats.wholefood_percentiles.*",
                "stats.protein_percentiles.*",
                "stats.fiber_percentiles.*",
                "stats.fortification_percentiles.*",
                "stats.simplicity_percentiles.*",
                "stats.sugar_penalty_percentiles.*",
                "stats.sodium_penalty_percentiles.*",
                "stats.trans_fat_penalty_percentiles.*",
                "stats.saturated_fat_penalty_percentiles.*",
                "stats.oil_penalty_percentiles.*",
                "stats.sweetener_penalty_percentiles.*",
                "stats.calories_penalty_percentiles.*",
                "stats.empty_food_penalty_percentiles.*",
            ]
        },
        "query": {"bool": {"filter": [], "should": [], "minimum_should_match": 0}},
        "sort": [{"_score": "desc"}],
        "min_score": 0.5,  # Add minimum score threshold
    }

    bq = body["query"]["bool"]
    filters: List[Dict[str, Any]] = bq["filter"]
    shoulds: List[Dict[str, Any]] = bq["should"]
    musts: List[Dict[str, Any]] = bq.setdefault("must", [])

    # 1) Hard filters
    # Category group filter
    category_group = p.get("category_group")
    if isinstance(category_group, str) and category_group.strip():
        filters.append({"term": {"category_group": category_group.strip()}})

    # Category path filter with improved handling (single and multi-paths)
    category_path = p.get("category_path") or p.get("cat_path")
    category_paths = p.get("category_paths") if isinstance(p.get("category_paths"), list) else []
    normalized_paths: List[str] = []
    
    def _normalize_path(path_str: str) -> str:
        """Normalize path: if already full format, use as-is; else extract relative."""
        parts = [x for x in str(path_str).split("/") if x]
        # If already full format (f_and_b/food/... or f_and_b/beverages/...), return as-is
        if len(parts) >= 3 and parts[0] == "f_and_b" and parts[1] in ("food", "beverages"):
            return path_str.strip()
        # If personal_care format
        if len(parts) >= 2 and parts[0] == "personal_care":
            return path_str.strip()
        # Legacy format: strip f_and_b and food to get relative
        core_parts = parts[:]
        if core_parts and core_parts[0] in ("f_and_b", "personal_care"):
            core_parts = core_parts[1:]
        if core_parts and core_parts[0] == "food":
            core_parts = core_parts[1:]
        return "/".join(core_parts)
    
    if isinstance(category_path, str) and category_path.strip():
        normalized = _normalize_path(category_path.strip())
        if normalized:
            normalized_paths.append(normalized)
    for cp in category_paths[:3]:
        try:
            s = str(cp).strip()
            if s:
                normalized = _normalize_path(s)
                if normalized:
                    normalized_paths.append(normalized)
        except Exception:
            pass
    
    if normalized_paths:
        # Deduplicate
        uniq: List[str] = []
        for path in normalized_paths:
            if path not in uniq:
                uniq.append(path)
        
        should_cat: List[Dict[str, Any]] = []
        prefer_keyword = bool(p.get("_has_category_paths_keyword"))
        if prefer_keyword:
            try:
                print("DEBUG: CAT_PATH_FILTER | using category_paths.keyword exact terms")
            except Exception:
                pass
        else:
            try:
                print("DEBUG: CAT_PATH_FILTER | using wildcard on category_paths (no .keyword)")
            except Exception:
                pass
        
        for path in uniq[:3]:
            # Check if already full format
            if path.startswith("f_and_b/") or path.startswith("personal_care/"):
                full = path
            else:
                # Legacy relative path - need to construct full
                group = (category_group or "").strip()
                if group == "personal_care":
                    full = f"personal_care/{path}"
                elif group == "f_and_b":
                    full = f"f_and_b/food/{path}"
                else:
                    # Unknown group, try both
                    fb = f"f_and_b/food/{path}"
                    pc = f"personal_care/{path}"
                    if prefer_keyword:
                        should_cat.extend([
                            {"term": {"category_paths.keyword": fb}},
                            {"term": {"category_paths.keyword": pc}},
                        ])
                    else:
                        should_cat.extend([
                            {"wildcard": {"category_paths": {"value": f"*{fb}*"}}},
                            {"wildcard": {"category_paths": {"value": f"*{pc}*"}}},
                        ])
                    continue
            
            # Add filter for full path
            if prefer_keyword:
                should_cat.append({"term": {"category_paths.keyword": full}})
            else:
                should_cat.extend([
                    {"term": {"category_paths": full}},
                    {"wildcard": {"category_paths": {"value": f"*{full}*"}}},
                ])
        
        if should_cat:
            filters.append({
                "bool": {"should": should_cat, "minimum_should_match": 1}
            })

    # Price range filter
    price_min = p.get("price_min")
    price_max = p.get("price_max")
    if price_min is not None or price_max is not None:
        pr: Dict[str, Any] = {}
        if isinstance(price_min, (int, float)):
            pr["gte"] = float(price_min)
        if isinstance(price_max, (int, float)):
            pr["lte"] = float(price_max)
        if pr:
            filters.append({"range": {"price": pr}})

    # Minimum flean percentile (quality threshold)
    min_flean = p.get("min_flean_percentile")
    if isinstance(min_flean, (int, float)):
        try:
            filters.append({
                "range": {
                    "stats.adjusted_score_percentiles.subcategory_percentile": {"gte": float(min_flean)}
                }
            })
        except Exception:
            pass

    # 2) Domain-specific filters (skin/personal care)
    # Apply skin-specific params when category_group indicates personal care
    try:
        if str(p.get("category_group") or "").strip() == "personal_care":
            # Minimum reviews threshold
            min_reviews = p.get("min_review_count")
            if isinstance(min_reviews, int) and min_reviews > 0:
                filters.append({"range": {"review_stats.total_reviews": {"gte": min_reviews}}})

            # Brand filter
            if isinstance(p.get("brands"), list) and p.get("brands"):
                filters.append({"terms": {"brand": p["brands"]}})

            # Avoid ingredients → must_not side effects and ingredients.raw_text
            avoid_list = p.get("avoid_ingredients") or []
            if isinstance(avoid_list, list) and avoid_list:
                for ing in avoid_list[:6]:
                    ing_s = str(ing).strip()
                    if not ing_s:
                        continue
                    bq.setdefault("must_not", []).append({
                        "bool": {
                            "must": [
                                {"match": {"side_effects.effect_name": ing_s}},
                                {"range": {"side_effects.severity_score": {"gte": 0.3}}}
                            ]
                        }
                    })
                    bq.setdefault("must_not", []).append({"match": {"ingredients.raw_text": ing_s}})

            # Skin type compatibility scoring
            skin_types = p.get("skin_types") or []
            if isinstance(skin_types, list) and skin_types:
                for st in skin_types[:4]:
                    st_s = str(st).strip().lower()
                    if not st_s:
                        continue
                    shoulds.append({
                        "bool": {
                            "must": [
                                {"term": {"skin_compatibility.skin_type": st_s}},
                                {"range": {"skin_compatibility.sentiment_score": {"gte": 0.6}}},
                                {"range": {"skin_compatibility.confidence_score": {"gte": 0.3}}}
                            ],
                            "boost": 5.0
                        }
                    })

            # Efficacy scoring for concerns
            concerns = p.get("skin_concerns") or []
            if isinstance(concerns, list) and concerns:
                boost = 3.0 if bool(p.get("prioritize_concerns")) else 2.0
                for c in concerns[:4]:
                    c_s = str(c).strip().lower()
                    if not c_s:
                        continue
                    shoulds.append({
                        "bool": {
                            "must": [
                                {"match": {"efficacy.aspect_name": c_s}},
                                {"range": {"efficacy.sentiment_score": {"gte": 0.7}}},
                                {"range": {"efficacy.mention_count": {"gte": 5}}}
                            ],
                            "boost": boost
                        }
                    })
    except Exception:
        pass

    # 3) Soft re-ranking signals with boosts
    
    # NOTE: Brand-based boosting is intentionally disabled.
    # Direct brand filtering/boosting proved brittle due to inconsistent brand tokenization
    # (e.g., variants, localization, punctuation). It can eliminate good matches or overfit
    # to noisy brand mentions in text. If we need brand handling later, prefer exact keyword
    # fields (e.g., brand.keyword) and a carefully normalized brand map.

    # Dietary labels with boost (fuzzy enabled for variant tolerance)
    dietary_labels = p.get("dietary_labels") or p.get("dietary_terms") or []
    if isinstance(dietary_labels, list) and dietary_labels:
        for label in dietary_labels:
            if label and str(label).strip():
                shoulds.append({
                    "multi_match": {
                        "query": str(label).strip(),
                        "fields": ["package_claims.dietary_labels^3.0"],
                        "type": "best_fields",
                        "fuzziness": "AUTO"
                    }
                })

    # Health claims with boost
    health_claims = p.get("health_claims") or []
    if isinstance(health_claims, list) and health_claims:
        for claim in health_claims:
            if claim and str(claim).strip():
                shoulds.append({
            "match": {
                        "package_claims.health_claims": {
                            "query": str(claim).strip(),
                            "boost": 2.0
                        }
                    }
                })

    # 3) Keyword/multi_match component
    q_text = str(p.get("q", "")).strip()
    keywords = p.get("keywords") or []
    field_boosts = p.get("field_boosts") or []
    dynamic_fields = ["name^4", "description^2", "use", "combined_text"]
    if isinstance(field_boosts, list) and field_boosts:
        try:
            for fb in field_boosts:
                if isinstance(fb, str) and fb.strip():
                    dynamic_fields.append(fb.strip())
        except Exception:
            pass
    
    # Main query matching (anchor q as MUST; keywords as optional SHOULD)
    if q_text:
        musts.append({
            "multi_match": {
                "query": q_text,
                "type": "best_fields",
                "fields": dynamic_fields,
                "fuzziness": "AUTO"
            }
        })
    if isinstance(keywords, list) and keywords:
        dedup_keywords: List[str] = []
        q_low = q_text.lower()
        for kw in keywords[:2]:  # cap to 2
            kw_str = str(kw).strip()
            if kw_str and (kw_str.lower() not in q_low) and (kw_str.lower() not in dedup_keywords):
                dedup_keywords.append(kw_str.lower())
        for kw in dedup_keywords:
            shoulds.append({
                "multi_match": {
                    "query": kw,
                    "type": "best_fields",
                    "fields": dynamic_fields,
                    "fuzziness": "AUTO"
                }
            })

    # 3b) Hard must keywords with fuzzy tolerance (e.g., flavor tokens like 'orange', 'peri peri')
    must_keywords = p.get("must_keywords") or []
    if isinstance(must_keywords, list) and must_keywords:
        for kw in must_keywords[:3]:
            kw_str = str(kw).strip()
            if kw_str:
                musts.append({
                    "multi_match": {
                        "query": kw_str,
                        "type": "best_fields",
                        "fields": ["name^6", "description^2", "combined_text"],
                        "fuzziness": "AUTO"
                    }
                })

    # 3c) Phrase boosts provided by LLM normaliser
    phrase_boosts = p.get("phrase_boosts") or []
    if isinstance(phrase_boosts, list) and phrase_boosts:
        for pb in phrase_boosts[:6]:
            try:
                if isinstance(pb, dict):
                    field = str(pb.get("field") or pb.get("title") or "name").strip() or "name"
                    phrase = str(pb.get("phrase") or pb.get("title") or "").strip()
                    boost = float(pb.get("boost", 1.5))
                    if phrase:
                        shoulds.append({
                            "match_phrase": {field: {"query": phrase, "boost": boost}}
                        })
            except Exception:
                pass

    # 4) Apply dynamic function_score based on subcategory
    # Derive subcategory from params/category_path
    subcategory = None
    try:
        if isinstance(p.get("fb_subcategory"), str) and p["fb_subcategory"].strip():
            subcategory = p["fb_subcategory"].strip()
        elif isinstance(category_path, str) and category_path.strip():
            # Use the last segment as subcategory when present
            path_parts = [seg for seg in category_path.split("/") if seg]
            if path_parts:
                # Expect f_and_b/food/l2/l3; pick l3 else l2
                subcategory = path_parts[-1]
                if subcategory in ("food", "f_and_b", "personal_care") and len(path_parts) >= 2:
                    subcategory = path_parts[-2]
        if not subcategory:
            subcategory = "_default"
    except Exception:
        subcategory = "_default"

    scoring_functions: List[Dict[str, Any]] = []
    if shoulds or filters:
        # Optional: strict brand filter for trusted sources (e.g., vision flow)
        try:
            if bool(p.get("enforce_brand")) and isinstance(p.get("brands"), list) and p.get("brands"):
                brand_value = str(p.get("brands")[0] or "").strip()
                brand_clean = brand_value.strip("'\" ")
                if brand_clean:
                    # Build robust brand conditions: exact, case variants, wildcard, and phrase match in analyzed fields
                    def _variants(s: str) -> List[str]:
                        base = s.strip()
                        title = " ".join([w.capitalize() for w in base.split()])
                        lower = base.lower()
                        upper = base.upper()
                        no_punct = base.replace("'", "").replace("`", "")
                        uniq = []
                        for v in [base, title, lower, upper, no_punct]:
                            if v and v not in uniq:
                                uniq.append(v)
                        return uniq
                    brand_variants = _variants(brand_clean)
                    should_brand: List[Dict[str, Any]] = []
                    for v in brand_variants:
                        should_brand.append({"term": {"brand": v}})
                        should_brand.append({"wildcard": {"brand": {"value": f"{v}*"}}})
                        should_brand.append({"wildcard": {"brand": {"value": f"*{v}*"}}})
                        # Also allow phrase in analyzed fields
                        should_brand.append({"match_phrase": {"name": v}})
                        should_brand.append({"match_phrase": {"combined_text": v}})
                    filters.append({
                        "bool": {"should": should_brand, "minimum_should_match": 1}
                    })
                    print(f"DEBUG: Enforcing brand filter | brand='{brand_clean}' | variants={brand_variants}")
        except Exception:
            pass
        # Get category-specific scoring functions
        scoring_functions = build_function_score_functions(subcategory, include_flean=True)
        body["query"] = {
            "function_score": {
                "query": {"bool": bq},
                "functions": scoring_functions,
                "score_mode": "multiply",
                "boost_mode": "multiply"
            }
        }

    # Set minimum_should_match: 0 when q is MUST; else 1 if textual SHOULD present
    try:
        has_text_should = any(
            any(k in s for k in ["multi_match", "match", "match_phrase"]) for s in shoulds
        )
        if q_text:
            bq["minimum_should_match"] = 0
        else:
            bq["minimum_should_match"] = 1 if has_text_should else 0
    except Exception:
        bq["minimum_should_match"] = 0

    # Debug logs
    print(f"DEBUG: ES filters={len(filters)} filters")
    print(f"DEBUG: ES should={len(shoulds)} clauses")
    print(f"DEBUG: Using dynamic scoring for subcategory='{subcategory}'")
    print(f"DEBUG: Applied {len(scoring_functions)} scoring functions")
    
    # Add highlight section if there is a query text component
    try:
        if q_text or keywords:
            body["highlight"] = {
                "fields": {
                    "name": {"number_of_fragments": 0},
                    "package_claims.dietary_labels": {"number_of_fragments": 0},
                    "ingredients.raw_text": {"fragment_size": 120, "number_of_fragments": 1}
                }
            }
    except Exception:
        pass

    # Availability filter (zepto)
    try:
        if bool(p.get("availability_zepto_in_stock")):
            filters.append({"term": {"availability.zepto.in_stock": True}})
    except Exception:
        pass

    # Excluded ingredients
    try:
        excl = p.get("excluded_ingredients") or []
        if isinstance(excl, list) and excl:
            for ing in excl[:6]:
                sval = str(ing).strip()
                if not sval:
                    continue
                musts.append({
                    "bool": {
                        "must_not": [
                            {"match": {"ingredients.raw_text": sval}},
                            {"match": {"ingredients.structured.ingredients.ingredients.name": sval}},
                        ]
                    }
                })
    except Exception:
        pass

    # Health positioning tags (soft boosts)
    try:
        hpt = p.get("health_positioning_tags") or []
        if isinstance(hpt, list) and hpt:
            for tag in hpt[:4]:
                tv = str(tag).strip()
                if not tv:
                    continue
                shoulds.append({"term": {"package_claims.health_claims": {"value": tv, "boost": 1.3}}})
    except Exception:
        pass

    # Marketing tags (soft boosts)
    try:
        mkt = p.get("marketing_tags") or []
        if isinstance(mkt, list) and mkt:
            for tag in mkt[:2]:
                tv = str(tag).strip()
                if not tv:
                    continue
                shoulds.append({"term": {"package_claims.marketing_keywords": {"value": tv, "boost": 1.15}}})
    except Exception:
        pass

    return body

def _parse_product_type(anchor: str) -> Dict[str, Any]:
    """Parse anchor_product_noun to extract product category and type for strict matching.
    
    Returns dict with:
        - category_terms: list of category identifiers (hair, face, skin, body, etc)
        - type_terms: list of product type identifiers (oil, wash, serum, cream, etc)
        - exclude_terms: list of terms to exclude in must_not
    """
    if not anchor:
        return {"category_terms": [], "type_terms": [], "exclude_terms": []}
    
    anchor_lower = anchor.lower().strip()
    
    # Product category detection (hair/face/skin/body/lips/eyes/nails)
    category_map = {
        "hair": ["hair", "scalp"],
        "face": ["face", "facial"],
        "skin": ["skin"],
        "body": ["body"],
        "lips": ["lip", "lips"],
        "eyes": ["eye", "eyes"],
        "nails": ["nail", "nails"],
    }
    
    # Product type detection (oil/wash/serum/cream/lotion/etc)
    type_map = {
        "oil": ["oil"],
        "wash": ["wash", "cleanser", "cleansing"],
        "serum": ["serum"],
        "cream": ["cream"],
        "moisturizer": ["moisturizer", "moisturiser"],
        "lotion": ["lotion"],
        "gel": ["gel"],
        "foam": ["foam"],
        "scrub": ["scrub", "exfoliant"],
        "mask": ["mask"],
        "toner": ["toner"],
        "sunscreen": ["sunscreen", "spf"],
        "shampoo": ["shampoo"],
        "conditioner": ["conditioner"],
        "soap": ["soap"],
        "powder": ["powder"],
        "balm": ["balm"],
        "mist": ["mist", "spray"],
        "treatment": ["treatment"],
    }
    
    detected_category = []
    detected_type = []
    
    # Detect categories
    for cat, keywords in category_map.items():
        if any(kw in anchor_lower for kw in keywords):
            detected_category.append(cat)
    
    # Detect types
    for ptype, keywords in type_map.items():
        if any(kw in anchor_lower for kw in keywords):
            detected_type.append(ptype)
    
    # Build exclusion terms (exclude other major categories)
    exclude_terms = []
    
    # If this is hair product, exclude face/skin/body products
    if "hair" in detected_category:
        exclude_terms.extend(["face wash", "facial", "makeup", "foundation", "lipstick"])
        # Also exclude cleansing oils (they're for face, not hair)
        if "oil" in detected_type:
            exclude_terms.extend(["cleansing", "makeup removal", "makeup remover"])
    
    # If this is face product, exclude hair/body products
    elif "face" in detected_category:
        exclude_terms.extend(["hair", "shampoo", "conditioner", "scalp"])
    
    # If this is body product, exclude hair/face products
    elif "body" in detected_category:
        exclude_terms.extend(["hair", "face", "facial"])
    
    # Special cases: if "oil" is mentioned without category, be more careful
    if "oil" in detected_type and not detected_category:
        # Generic "oil" query - exclude cleansing/makeup oils unless explicitly mentioned
        if "cleansing" not in anchor_lower and "makeup" not in anchor_lower:
            exclude_terms.extend(["cleansing", "makeup removal"])
    
    return {
        "category_terms": detected_category,
        "type_terms": detected_type,
        "exclude_terms": exclude_terms,
    }


def _build_skin_es_query(params: Dict[str, Any]) -> Dict[str, Any]:
    """Build a personal care (skin) ES query matching the working Postman shape."""
    p = params or {}
    # Global flags
    is_image_query: bool = bool(p.get("is_image_query"))

    size = int(p.get("size", 10) or 10)
    price_min = p.get("price_min")
    price_max = p.get("price_max")
    min_reviews = p.get("min_review_count")
    q_text = str(p.get("q") or "").strip()

    body: Dict[str, Any] = {
        "size": size,
        "track_total_hits": True,
        "_source": {
            "includes": [
                "id", "name", "brand", "price", "mrp",
                "category_group", "category_paths", "hero_image.1080",
                "review_stats.avg_rating", "review_stats.total_reviews",
                "skin_compatibility.skin_type", "skin_compatibility.sentiment_score", "skin_compatibility.confidence_score",
                "efficacy.aspect_name", "efficacy.sentiment_score", "efficacy.mention_count",
                "side_effects.effect_name", "side_effects.severity_score", "side_effects.sentiment_score",
                "package_claims.health_claims", "package_claims.dietary_labels",
            ]
        },
        "query": {
            "function_score": {
                "query": {"bool": {"filter": [], "must": [], "should": [], "must_not": [], "minimum_should_match": 0}},
                "functions": [
                    {
                        "field_value_factor": {
                            "field": "review_stats.avg_rating",
                            "factor": 1.2,
                            "modifier": "sqrt",
                            "missing": 3.0,
                        }
                    },
                    {
                        "field_value_factor": {
                            "field": "review_stats.total_reviews",
                            "factor": 1.0,
                            "modifier": "log1p",
                            "missing": 1,
                        }
                    },
                ],
                "score_mode": "multiply",
                "boost_mode": "multiply",
            }
        },
        "sort": [{"_score": "desc"}],
        "min_score": 0.5,
    }

    bq = body["query"]["function_score"]["query"]["bool"]
    filters: List[Dict[str, Any]] = bq["filter"]
    shoulds: List[Dict[str, Any]] = bq["should"]
    musts: List[Dict[str, Any]] = bq["must"]
    must_not: List[Dict[str, Any]] = bq["must_not"]

    # Filters: category group
    filters.append({"term": {"category_group": "personal_care"}})

    # NOTE: Personal care: ignore category_path(s) entirely per product decision
    # We intentionally do NOT add any category_paths filter for personal_care
    try:
        if p.get("category_path") or p.get("category_paths"):
            print("DEBUG: SKIN_CATEGORY_PATH_IGNORED | personal care has no enforced hierarchy")
    except Exception:
        pass
    
    # NEW: Strict product type matching using anchor_product_noun
    # Parse anchor to extract category (hair/face/body) and type (oil/wash/serum)
    anchor_noun = str(p.get("anchor_product_noun") or "").strip()
    parsed = _parse_product_type(anchor_noun) if anchor_noun else {}
    
    if anchor_noun and parsed:
        category_terms = parsed.get("category_terms", [])
        type_terms = parsed.get("type_terms", [])
        exclude_terms = parsed.get("exclude_terms", [])
        
        try:
            print(f"DEBUG: PRODUCT_TYPE_PARSE | anchor='{anchor_noun}' | category={category_terms} | type={type_terms} | exclude={exclude_terms}")
        except Exception:
            pass
        
        # Build MUST clause: product must match category (hair/face/body/etc)
        if category_terms:
            category_should = []
            for cat_term in category_terms[:2]:  # Max 2 categories
                # Search across multiple fields for category match
                category_should.extend([
                    {"match_phrase": {"name": {"query": cat_term, "boost": 3.0}}},
                    {"match_phrase": {"use": {"query": cat_term, "boost": 2.0}}},
                    {"match": {"description": {"query": cat_term, "boost": 1.0}}},
                ])
            
            if category_should:
                musts.append({
                    "bool": {
                        "should": category_should,
                        "minimum_should_match": 1
                    }
                })
        
        # Build MUST clause: product must match type (oil/wash/serum/etc)
        if type_terms:
            type_should = []
            for type_term in type_terms[:2]:  # Max 2 types
                # Search across multiple fields for type match
                type_should.extend([
                    {"match_phrase": {"name": {"query": type_term, "boost": 3.0}}},
                    {"match": {"use": {"query": type_term, "boost": 2.0}}},
                    {"match": {"description": {"query": type_term, "boost": 1.0}}},
                ])
                
                # Also check package claims if available
                if type_term in ["oil", "serum", "cream", "lotion", "gel"]:
                    type_should.append({
                        "match": {"package_claims.marketing_keywords": {"query": type_term}}
                    })
            
            if type_should:
                musts.append({
                    "bool": {
                        "should": type_should,
                        "minimum_should_match": 1
                    }
                })
        
        # Build MUST_NOT clauses: exclude wrong product categories
        if exclude_terms:
            for exc_term in exclude_terms[:5]:  # Max 5 exclusions
                # Exclude from name and use fields
                must_not.extend([
                    {"match_phrase": {"name": exc_term}},
                    {"match_phrase": {"use": exc_term}},
                ])

    # Price filter
    if price_min is not None or price_max is not None:
        pr: Dict[str, Any] = {}
        if isinstance(price_min, (int, float)):
            pr["gte"] = float(price_min)
        if isinstance(price_max, (int, float)):
            pr["lte"] = float(price_max)
        if pr:
            filters.append({"range": {"price": pr}})

    # Min reviews (apply only if provided)
    if isinstance(min_reviews, int) and min_reviews > 0:
        filters.append({"range": {"review_stats.total_reviews": {"gte": min_reviews}}})

    # Brands
    if isinstance(p.get("brands"), list) and p.get("brands"):
        # For image queries or when explicit enforcement is requested, apply robust brand gating
        enforce_brand = bool(p.get("enforce_brand")) or is_image_query
        if enforce_brand:
            try:
                brand_value = str((p.get("brands") or [""])[0] or "").strip()
                brand_clean = brand_value.strip("'\" ")
                if brand_clean:
                    def _variants(base: str) -> List[str]:
                        s = base.strip()
                        # Generate simple normalization variants
                        title = " ".join([w.capitalize() for w in s.split()])
                        lower = s.lower()
                        upper = s.upper()
                        no_amp = s.replace("&", "and")
                        no_punct = s.replace("'", "").replace("`", "")
                        uniq: List[str] = []
                        for v in [s, title, lower, upper, no_amp, no_punct]:
                            if v and v not in uniq:
                                uniq.append(v)
                        return uniq

                    brand_variants = _variants(brand_clean)
                    should_brand: List[Dict[str, Any]] = []
                    for v in brand_variants:
                        # Exact term on brand (works if brand is keyword or non-analyzed)
                        should_brand.append({"term": {"brand": v}})
                        # Try keyword subfield when present (safe no-op if unmapped)
                        should_brand.append({"term": {"brand.keyword": v}})
                        # Wildcards to handle minor punctuation/case/tokenization differences
                        should_brand.append({"wildcard": {"brand": {"value": f"{v}*"}}})
                        should_brand.append({"wildcard": {"brand": {"value": f"*{v}*"}}})
                        # Phrase match in analyzed text fields (name, combined_text)
                        should_brand.append({"match_phrase": {"name": v}})
                        should_brand.append({"match_phrase": {"combined_text": v}})

                    filters.append({
                        "bool": {"should": should_brand, "minimum_should_match": 1}
                    })
                    print(f"DEBUG: Enforcing brand filter (skin) | brand='{brand_clean}' | variants={brand_variants}")
                else:
                    # Fallback to simple terms when brand is empty after cleaning
                    filters.append({"terms": {"brand": p["brands"]}})
            except Exception:
                # On any construction error, fallback to simple terms filter
                filters.append({"terms": {"brand": p["brands"]}})
        else:
            # Non-image generic case: keep lightweight terms filter
            filters.append({"terms": {"brand": p["brands"]}})

    # Must/Should: main query
    if q_text:
        # When we have strict product type matching (category + type musts), reduce fuzziness
        # to avoid over-matching. Otherwise keep fuzziness for generic queries.
        has_strict_matching = anchor_noun and (parsed.get("category_terms") or parsed.get("type_terms"))
        
        text_clause = {
            "multi_match": {
                "query": q_text,
                "type": "best_fields",
                "fields": ["name^6"],
                "fuzziness": "0" if has_strict_matching else "AUTO",  # No fuzziness when strict matching
            }
        }
        # For image-origin queries, treat text as a soft signal to avoid over-filtering
        if is_image_query:
            shoulds.append(text_clause)
        else:
            musts.append(text_clause)

    # Skin/Hair suitability → nested strong should on skin_compatibility
    # Map hair_types to scalp equivalents where possible (oily/dry/normal)
    compat_types: List[str] = []
    try:
        for st in (p.get("skin_types") or [])[:4]:
            s = str(st).strip().lower()
            if s and s not in compat_types:
                compat_types.append(s)
        hair_map = {"oily": "oily", "dry": "dry", "normal": "normal"}
        for ht in (p.get("hair_types") or [])[:4]:
            m = hair_map.get(str(ht).strip().lower())
            if m and m not in compat_types:
                compat_types.append(m)
    except Exception:
        pass
    for st in compat_types[:4]:
        st_s = str(st).strip().lower()
        if not st_s:
            continue
        shoulds.append({
            "nested": {
                "path": "skin_compatibility",
                "query": {
                    "bool": {
                        "must": [
                            {
                                "multi_match": {
                                    "query": st_s,
                                    "fields": ["skin_compatibility.skin_type^3.0"],
                                    "fuzziness": "AUTO",
                                    "type": "best_fields"
                                }
                            },
                            {"range": {"skin_compatibility.sentiment_score": {"gte": 0.6}}},
                            {"range": {"skin_compatibility.confidence_score": {"gte": 0.3}}},
                        ]
                    }
                },
                "score_mode": "max",
                "boost": 5.0,
            }
        })

    # Efficacy (positive) → from efficacy_terms if provided; else derive from concerns (ALWAYS include, even if empty)
    concern_boost = 2.0 if not bool(p.get("prioritize_concerns")) else 3.0
    efficacy_terms: List[str] = []
    try:
        for it in (p.get("efficacy_terms") or [])[:6]:
            if it and it not in efficacy_terms:
                efficacy_terms.append(str(it).strip())
    except Exception:
        pass
    if not efficacy_terms:
        merged_concerns = []
        try:
            for lst in [p.get("skin_concerns") or [], p.get("hair_concerns") or []]:
                for it in lst:
                    if it and it not in merged_concerns:
                        merged_concerns.append(str(it).strip())
        except Exception:
            merged_concerns = (p.get("skin_concerns") or [])
        efficacy_terms = merged_concerns[:6]
    # Always add an efficacy nested block; if no terms, use empty-safe variant (no-op boost)
    shoulds.append({
        "nested": {
            "path": "efficacy",
            "query": {
                "bool": {
                    "must": (
                        [
                            {
                                "multi_match": {
                                    "query": " ".join(efficacy_terms),
                                    "fields": ["efficacy.aspect_name^3.0"],
                                    "fuzziness": "AUTO",
                                    "type": "best_fields"
                                }
                            },
                            {"range": {"efficacy.sentiment_score": {"gte": 0.7}}}
                        ]
                        if efficacy_terms else [{"match_all": {}}]
                    )
                }
            },
            "inner_hits": {
                "name": "efficacy_hits",
                "size": 3,
                "_source": ["efficacy.aspect_name", "efficacy.sentiment_score", "efficacy.mention_count"]
            },
            "score_mode": "max",
            "boost": concern_boost,
        }
    })

    # Avoid negatives: side_effects nested + cons_list keywords
    avoid_terms: List[str] = []
    try:
        for it in (p.get("avoid_terms") or [])[:6]:
            if it and it not in avoid_terms:
                avoid_terms.append(str(it).strip())
    except Exception:
        pass

    # Side-effects exclusions (ALWAYS include block, even if empty will no-op)
    for ing in (p.get("avoid_ingredients") or [])[:6]:
        ing_s = str(ing).strip()
        if not ing_s:
            continue
        must_not.append({
            "nested": {
                "path": "side_effects",
                "query": {
                    "bool": {
                        "must": [
                            {"match": {"side_effects.effect_name": ing_s}},
                            {"range": {"side_effects.severity_score": {"gte": 0.3}}},
                        ]
                    }
                }
            }
        })
    # Also use avoid_terms for side_effects
    for term in avoid_terms:
        t = str(term).strip()
        if not t:
            continue
        must_not.append({
            "nested": {
                "path": "side_effects",
                "query": {
                    "bool": {
                        "must": [
                            {"match": {"side_effects.effect_name": t}},
                            {"range": {"side_effects.severity_score": {"gte": 0.3}}},
                        ]
                    }
                }
            }
        })
    # And cons_list keyword exclusions
    # Cons list exclusions (ALWAYS present; no-op when empty)
    must_not.append({"terms": {"cons_list": (avoid_terms or [])}})

    # Personal care: treat should as pure boosts (ensure the four sections exist)
    bq["minimum_should_match"] = 0
    try:
        print(
            f"DEBUG: PC_SECTIONS | efficacy_terms={efficacy_terms} | avoid_terms={avoid_terms} | skin_types={p.get('skin_types')} | hair_types={p.get('hair_types')}"
        )
    except Exception:
        pass

    # Highlight
    body["highlight"] = {
        "fields": {
            "name": {"number_of_fragments": 0},
            "ingredients.raw_text": {"fragment_size": 120, "number_of_fragments": 1},
        }
    }

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
        
        # Extract percentile scores
        stats = src.get("stats", {})
        flean_percentile = None
        if stats.get("adjusted_score_percentiles"):
            flean_percentile = stats["adjusted_score_percentiles"].get("subcategory_percentile")
        # Prepare bonus/penalty percentile bundle for LLM persuasion
        bonus_percentiles = {
            "protein": (stats.get("protein_percentiles", {}) or {}).get("subcategory_percentile"),
            "fiber": (stats.get("fiber_percentiles", {}) or {}).get("subcategory_percentile"),
            "wholefood": (stats.get("wholefood_percentiles", {}) or {}).get("subcategory_percentile"),
            "fortification": (stats.get("fortification_percentiles", {}) or {}).get("subcategory_percentile"),
            "simplicity": (stats.get("simplicity_percentiles", {}) or {}).get("subcategory_percentile"),
        }
        penalty_percentiles = {
            "sugar": (stats.get("sugar_penalty_percentiles", {}) or {}).get("subcategory_percentile"),
            "sodium": (stats.get("sodium_penalty_percentiles", {}) or {}).get("subcategory_percentile"),
            "trans_fat": (stats.get("trans_fat_penalty_percentiles", {}) or {}).get("subcategory_percentile"),
            "saturated_fat": (stats.get("saturated_fat_penalty_percentiles", {}) or {}).get("subcategory_percentile"),
            "oil": (stats.get("oil_penalty_percentiles", {}) or {}).get("subcategory_percentile"),
            "sweetener": (stats.get("sweetener_penalty_percentiles", {}) or {}).get("subcategory_percentile"),
            "calories": (stats.get("calories_penalty_percentiles", {}) or {}).get("subcategory_percentile"),
            "empty_food": (stats.get("empty_food_penalty_percentiles", {}) or {}).get("subcategory_percentile"),
        }
        
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
            
            # Quality scores
            "flean_percentile": flean_percentile,
            "flean_score": src.get("flean_score", {}).get("adjusted_score"),
            "bonus_percentiles": {k: v for k, v in bonus_percentiles.items() if v is not None},
            "penalty_percentiles": {k: v for k, v in penalty_percentiles.items() if v is not None},
            
            # Image
            "image": _get_best_image(src.get("hero_image", {})),
            
            # Ingredients
            "ingredients": _clean_text(src.get("ingredients", {}).get("raw_text", "")),
            # Reviews (surface to LLM for stars)
            "avg_rating": (src.get("review_stats", {}) or {}).get("avg_rating"),
            "total_reviews": (src.get("review_stats", {}) or {}).get("total_reviews"),
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
    """Elasticsearch fetcher with enhanced query capabilities."""
    
    def __init__(self, base_url: str = None, index: str = None, api_key: str = None):
        self.base_url = base_url or ELASTIC_BASE
        self.index = index or ELASTIC_INDEX
        self.api_key = api_key or ELASTIC_API_KEY
        self._has_category_paths_keyword: Optional[bool] = None
        
        if not self.api_key:
            raise RuntimeError("ELASTIC_API_KEY (or ES_API_KEY) is required for Elasticsearch access")
            
        self.endpoint = f"{self.base_url}/{self.index}/_search"
        self.mget_endpoint = f"{self.base_url}/{self.index}/_mget"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"ApiKey {self.api_key}"
        } if self.api_key else {}
    
    def _ensure_mapping_hints(self) -> None:
        """Lazy-load index mapping to detect availability of 'category_paths.keyword'."""
        if self._has_category_paths_keyword is not None:
            return
        try:
            mapping_endpoint = f"{self.base_url}/{self.index}/_mapping"
            resp = requests.get(mapping_endpoint, headers=self.headers, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json() or {}
            # Traverse to detect 'category_paths.keyword'
            has_kw = False
            try:
                # mappings can be keyed by index name
                for _idx, payload in (data or {}).items():
                    mappings = (payload or {}).get("mappings", {}) or {}
                    props = mappings.get("properties", {}) or {}
                    cat = props.get("category_paths", {}) or {}
                    fields = cat.get("fields", {}) or {}
                    if isinstance(fields.get("keyword"), dict):
                        has_kw = True
                        break
            except Exception:
                has_kw = False
            self._has_category_paths_keyword = has_kw
            try:
                print(f"DEBUG: MAPPING_HINT | category_paths.keyword={'yes' if has_kw else 'no'}")
            except Exception:
                pass
        except Exception as exc:
            try:
                print(f"DEBUG: MAPPING_HINT_ERROR | {exc}")
            except Exception:
                pass
            self._has_category_paths_keyword = False
    
    def search(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute search against Elasticsearch with fallback strategies."""
        try:
            # Ensure mapping hints for category_paths.keyword usage
            self._ensure_mapping_hints()
            p = dict(params or {})
            p["_has_category_paths_keyword"] = bool(self._has_category_paths_keyword)
            # Route by domain: personal_care → skin builder; else generic
            if str(p.get("category_group") or "").strip() == "personal_care":
                query_body = _build_skin_es_query(p)
            else:
                query_body = _build_enhanced_es_query(p)
            
            # Debug logging
            print(f"DEBUG: Enhanced ES Query Structure:")
            print(f"  - Query: {params.get('q', '')}")
            print(f"  - Category: {params.get('category_group', 'all')}")
            print(f"  - Brands: {params.get('brands', [])}")
            print(f"  - Price range: {params.get('price_min', 'no min')}-{params.get('price_max', 'no max')}")
            print(f"  - Dietary: {params.get('dietary_labels', [])}")
            try:
                print(f"DEBUG: ES endpoint: {self.endpoint}")
                # Pretty-print the actual JSON body for Postman reproduction
                body_str = json.dumps(query_body, ensure_ascii=False, indent=2)
                # Truncate extremely long outputs to keep logs readable
                if len(body_str) > 40000:
                    print(body_str[:40000] + "\n... (truncated) ...")
                else:
                    print(body_str)
            except Exception:
                pass
            
            response = requests.post(
                self.endpoint,
                headers=self.headers,
                json=query_body,
                timeout=TIMEOUT
            )
            response.raise_for_status()
            
            raw_data = response.json()
            result = _transform_results(raw_data)
            
            print(f"DEBUG: ES query found {result['meta']['total_hits']} products")
            
            # No brand-specific fallback; brand handling is disabled (see note above)
            
            # Suppressed: verbose top results logging
            
            return result
            
        except requests.exceptions.Timeout:
            print(f"DEBUG: ES timeout")
            return {"meta": {"total_hits": 0, "returned": 0, "took_ms": 0, "query_successful": False, "error": "timeout"}, "products": []}
        except requests.exceptions.RequestException as e:
            print(f"DEBUG: ES request failed: {e}")
            return {"meta": {"total_hits": 0, "returned": 0, "took_ms": 0, "query_successful": False, "error": str(e)}, "products": []}
        except Exception as e:
            print(f"DEBUG: Unexpected ES error: {e}")
            return {"meta": {"total_hits": 0, "returned": 0, "took_ms": 0, "query_successful": False, "error": str(e)}, "products": []}

    def mget_products(self, ids: List[str]) -> List[Dict[str, Any]]:
        """Fetch full product documents via _mget for the given IDs.

        Returns a list of _source dicts (with selected fields prioritized) in the same order as requested IDs
        when possible.
        """
        if not ids:
            return []
        try:
            print(f"DEBUG: ES mget request | endpoint={self.mget_endpoint} | id_count={len(ids)} | sample_ids={ids[:3]}")
            body = {
                "ids": [str(x).strip() for x in ids if str(x).strip()]
            }
            response = requests.post(
                self.mget_endpoint,
                headers=self.headers,
                json=body,
                timeout=TIMEOUT
            )
            try:
                print(f"DEBUG: ES mget response | status={response.status_code}")
            except Exception:
                pass
            response.raise_for_status()
            data = response.json() or {}
            docs = data.get("docs", []) or []
            print(f"DEBUG: ES mget parsed | docs_count={len(docs)}")
            out: List[Dict[str, Any]] = []
            for d in docs:
                src = d.get("_source", {}) or {}
                if src:
                    out.append(src)
            print(f"DEBUG: ES mget out | sources_count={len(out)}")
            # If _mget by _id returned nothing, fallback to a terms search on field 'id'
            if not out:
                print("DEBUG: ES mget fallback → terms search on field 'id'")
                return self.search_by_ids(ids)
            return out
        except requests.exceptions.Timeout:
            print("DEBUG: ES mget timeout")
            return []
        except Exception as exc:
            print(f"DEBUG: ES mget failed: {exc}")
            return []

    def suggest_brand(self, brand_hint: str, category_group: Optional[str] = None) -> Optional[str]:
        """Suggest a canonical brand value from ES given a noisy hint.

        Uses a terms aggregation over `brand` filtered by wildcard matches of the hint.
        Returns the top bucket key (most frequent brand) or None.
        """
        try:
            hint = (brand_hint or "").strip().strip("'\" ")
            if not hint:
                return None
            should_terms: List[Dict[str, Any]] = [
                {"term": {"brand": hint}},
                {"wildcard": {"brand": {"value": f"{hint}*"}}},
                {"wildcard": {"brand": {"value": f"*{hint}*"}}},
            ]
            filters: List[Dict[str, Any]] = []
            if category_group and isinstance(category_group, str) and category_group.strip():
                filters.append({"term": {"category_group": category_group.strip()}})
            body: Dict[str, Any] = {
                "size": 0,
                "query": {
                    "bool": {
                        "filter": filters,
                        "should": should_terms,
                        "minimum_should_match": 1,
                    }
                },
                "aggs": {
                    "brand_suggest": {
                        "terms": {"field": "brand", "size": 5}
                    }
                }
            }
            response = requests.post(
                self.endpoint,
                headers=self.headers,
                json=body,
                timeout=TIMEOUT
            )
            response.raise_for_status()
            data = response.json() or {}
            buckets = (((data.get("aggregations", {}) or {}).get("brand_suggest", {}) or {}).get("buckets", []) or [])
            if buckets:
                suggestion = str(buckets[0].get("key", "")).strip()
                print(f"DEBUG: Brand suggest | hint='{hint}' → '{suggestion}'")
                return suggestion or None
        except Exception as exc:
            print(f"DEBUG: Brand suggest failed: {exc}")
        return None

    def search_by_ids(self, ids: List[str]) -> List[Dict[str, Any]]:
        """Fetch documents by matching the 'id' field using a terms query.

        Returns list of _source dicts ordered to match the requested ids.
        """
        if not ids:
            return []
        try:
            ordered_ids = [str(x).strip() for x in ids if str(x).strip()]
            body = {
                "size": len(ordered_ids),
                "_source": {
                    "includes": [
                        "id", "name", "brand", "price", "mrp", "description", "use",
                        "hero_image.*", "package_claims.*", "category_group", "category_paths",
                        "category_data.*", "ingredients.*", "tags_and_sentiments.*",
                        "flean_score.*", "stats.*"
                    ]
                },
                "query": {
                    "terms": {"id": ordered_ids}
                }
            }
            search_endpoint = f"{self.base_url}/{self.index}/_search"
            print(f"DEBUG: ES ids-search request | endpoint={search_endpoint} | id_count={len(ordered_ids)}")
            response = requests.post(
                search_endpoint,
                headers=self.headers,
                json=body,
                timeout=TIMEOUT
            )
            print(f"DEBUG: ES ids-search response | status={response.status_code}")
            response.raise_for_status()
            data = response.json() or {}
            hits = (data.get("hits", {}) or {}).get("hits", []) or []
            print(f"DEBUG: ES ids-search parsed | hits_count={len(hits)}")
            id_to_src: Dict[str, Dict[str, Any]] = {}
            for h in hits:
                src = h.get("_source", {}) or {}
                pid = str(src.get("id", "")).strip()
                if pid:
                    id_to_src[pid] = src
            # Reorder to match requested ids
            out: List[Dict[str, Any]] = [id_to_src.get(i) for i in ordered_ids if id_to_src.get(i)]
            print(f"DEBUG: ES ids-search out | sources_count={len(out)}")
            return out
        except requests.exceptions.Timeout:
            print("DEBUG: ES ids-search timeout")
            return []
        except Exception as exc:
            print(f"DEBUG: ES ids-search failed: {exc}")
            return []

# Parameter extraction and normalization
def _extract_defaults_from_context(ctx) -> Dict[str, Any]:
    """Extract search parameters from user context

    Critical: Always use CURRENT user text for follow-ups so we rebuild ES params
    from the latest delta rather than reusing stale query text from a prior turn.
    """
    session = ctx.session or {}
    assessment = session.get("assessment", {})
    
    # Canonical base query comes from assessment or prior meaningful query
    base_query = (assessment or {}).get("original_query") or session.get("last_query", "")
    query = base_query or ""
    
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
        else:
            # Map gen-z style labels to INR ranges
            label = budget_lower.strip()
            if label in {"budget-friendly", "budget friendly", "budget", "cheap", "affordable"}:
                price_min = None
                price_max = 100.0
            elif label in {"smart value", "value", "mid", "mid-range", "mid range"}:
                price_min = 100.0
                price_max = 200.0
            elif label in {"premium", "expensive", "high-end", "high end"}:
                price_min = 200.0
                price_max = None
    
    # Determine product_intent from context
    product_intent = str(session.get("product_intent") or "show_me_options")
    # Size hint: 1 for is_this_good; else 10
    size_hint = 1 if product_intent == "is_this_good" else 10

    # Persist latest base query back into session for visibility/debug
    try:
        session["last_query"] = query
        ctx.session = session
    except Exception:
        pass

    # Seed params (no cross-assessment carry-over; treat prior slots as hints only via LLM)
    params = {
        "q": query,
        "size": size_hint,
        # Do NOT default category_group; let taxonomy/planner infer it
        "category_group": session.get("category_group") or None,
        # Do NOT carry prior brands/dietary directly; planner/normaliser will decide using current turn
        "brands": None,
        "dietary_terms": None,
        "price_min": price_min,
        "price_max": price_max,
        "protein_weight": 1.5,
        "product_intent": product_intent,
    }

    # Lift quality if preferences indicate health focus
    try:
        pref = str(session.get("preferences", "") or "").lower()
        if any(token in pref for token in ["healthy", "healthier", "cleaner", "low oil", "low sugar", "low sodium", "baked"]):
            prev = float(params.get("min_flean_percentile", 30) or 30)
            params["min_flean_percentile"] = max(prev, 50)
    except Exception:
        pass

    return params

def _normalize_params(base_params: Dict[str, Any], llm_params: Dict[str, Any]) -> Dict[str, Any]:
    """Merge and normalize parameters"""
    # Start with base params
    final_params = dict(base_params)
    
    # Overlay LLM-extracted params (allow LLM to override 'q' when provided)
    for key, value in (llm_params or {}).items():
        if value is not None:
            final_params[key] = value
    
    # Normalize lists
    for list_field in ["brands", "dietary_terms", "dietary_labels"]:
        if list_field in final_params and final_params[list_field]:
            value = final_params[list_field]
            if isinstance(value, str):
                # Split string into list
                if list_field in ["dietary_terms", "dietary_labels"]:
                    final_params[list_field] = [v.strip().upper() for v in value.replace(",", " ").split() if v.strip()]
                else:
                    final_params[list_field] = [v.strip() for v in value.replace(",", " ").split() if v.strip()]
            elif isinstance(value, list):
                # Clean existing list
                if list_field in ["dietary_terms", "dietary_labels"]:
                    final_params[list_field] = [str(v).strip().upper() for v in value if str(v).strip()]
                else:
                    final_params[list_field] = [str(v).strip() for v in value if str(v).strip()]
    
    # Only ensure category_group for F&B when text or taxonomy signals it; else leave None to let ES planner decide
    if not final_params.get("category_group"):
        try:
            q_low = str(final_params.get("q") or "").lower()
            if any(tok in q_low for tok in ["chips", "snack", "ketchup", "juice", "milk", "biscuit", "cookie", "chocolate", "bread"]):
                final_params["category_group"] = "f_and_b"
        except Exception:
            pass
    
    # Clean up None values
    return {k: v for k, v in final_params.items() if v is not None}

async def build_search_params(ctx) -> Dict[str, Any]:
    """Build final search parameters - unified LLM source of truth with minimal fallback"""
    
    # Branch for personal care/skin domain to use skin-specific planner
    try:
        domain = str((ctx.session or {}).get("domain") or "").strip()
        
        # ✅ SAFETY CHECK: Validate domain matches current query intent
        if domain == "personal_care":
            current_text = _get_current_user_text(ctx)
            
            # Heuristic: check if current query has food signals
            food_signals = [
                "chips", "snack", "ketchup", "juice", "milk", "biscuit", 
                "cookie", "chocolate", "bread", "preservative", "organic",
                "sugar", "salt", "sodium", "ingredient", "flavor", "sauce",
                "pickle", "jam", "butter", "cheese", "pasta", "noodle"
            ]
            
            query_lower = current_text.lower()
            is_likely_food = any(signal in query_lower for signal in food_signals)
            
            # If query seems food-related, reset stale personal_care domain
            if is_likely_food:
                
                log.info(
                    f"DOMAIN_MISMATCH_DETECTED | user={ctx.user_id} | "
                    f"stored_domain={domain} | query='{current_text}' | "
                    f"action=resetting_domain"
                )
                # Clear stale domain
                ctx.session.pop("domain", None)
                ctx.session.pop("domain_subcategory", None)
                domain = ""  # Force fallback to unified flow
            else:
                # Domain validation passed, proceed with personal care flow
                from ..llm_service import LLMService
                llm_service = LLMService()
                # Build unified params for personal care
                try:
                    unified_pc = await llm_service.generate_unified_es_params(ctx)
                except Exception:
                    unified_pc = {}
                final_params: Dict[str, Any] = dict(unified_pc or {})
                # Force personal care group and ignore any category paths
                final_params["category_group"] = "personal_care"
                final_params.pop("category_path", None)
                final_params.pop("category_paths", None)
                # Ensure product_intent present
                try:
                    final_params.setdefault("product_intent", str(ctx.session.get("product_intent") or "show_me_options"))
                except Exception:
                    final_params.setdefault("product_intent", "show_me_options")
                # Clamp size to [1,50]
                try:
                    s = int(final_params.get("size", 10) or 10)
                    final_params["size"] = max(1, min(50, s))
                except Exception:
                    final_params["size"] = 10
                try:
                    merged = []
                    for lst in [final_params.get('skin_concerns') or [], final_params.get('hair_concerns') or []]:
                        for it in lst:
                            if it and it not in merged:
                                merged.append(it)
                    print(f"DEBUG: USING_SKIN_PARAMS | q='{final_params.get('q')}' | types={final_params.get('product_types')} | skin_concerns={final_params.get('skin_concerns')} | hair_concerns={final_params.get('hair_concerns')} | merged_concerns={merged}")
                    ctx.session.setdefault("debug", {})["last_skin_search_params"] = final_params
                except Exception:
                    pass
                return final_params
    except Exception as exc:
        try:
            print(f"DEBUG: SKIN_BRANCH_FAILED | {exc}")
        except Exception:
            pass

    # 1) Try unified ES params directly (authoritative)
    try:
        from ..llm_service import LLMService  # type: ignore
        llm_service = LLMService()
        unified = await llm_service.generate_unified_es_params(ctx)
        if isinstance(unified, dict) and unified.get("q"):
            final_params: Dict[str, Any] = dict(unified)
            # Ensure product_intent present
            try:
                final_params.setdefault("product_intent", str(ctx.session.get("product_intent") or "show_me_options"))
            except Exception:
                final_params.setdefault("product_intent", "show_me_options")
            # Default protein weight for scoring
            final_params.setdefault("protein_weight", 1.5)
            # Clamp size to [1,50]
            try:
                s = int(final_params.get("size", 20) or 20)
                final_params["size"] = max(1, min(50, s))
            except Exception:
                final_params["size"] = 20
            # Persist for debugging
            try:
                print(f"DEBUG: USING_UNIFIED_PARAMS_DIRECT | q='{final_params.get('q')}' | dietary={final_params.get('dietary_terms')}")
                ctx.session.setdefault("debug", {})["last_search_params"] = final_params
            except Exception:
                pass
            return final_params
    except Exception as exc:
        try:
            print(f"DEBUG: UNIFIED_CALL_FAILED | {exc}")
        except Exception:
            pass
    
    # 2) Minimal safe fallback (no legacy merges/overwrites)
    try:
        current_text = _get_current_user_text(ctx)
        base = (_extract_defaults_from_context(ctx) or {})
        anchor_q = str(base.get("q") or "").strip()
        q = (current_text or anchor_q or "").strip()
        if not q:
            q = "snacks"  # ultimate minimal default
        fallback: Dict[str, Any] = {
            "q": q,
            "size": 20,
            "category_group": "f_and_b",
            "product_intent": str(ctx.session.get("product_intent") or "show_me_options"),
            "protein_weight": 1.5,
        }
        print(f"DEBUG: UNIFIED_MINIMAL_FALLBACK | q='{fallback['q']}'")
        ctx.session.setdefault("debug", {})["last_search_params"] = fallback
        return fallback
    except Exception:
        # Extreme fallback
        return {"q": "snacks", "size": 20, "category_group": "f_and_b", "product_intent": "show_me_options", "protein_weight": 1.5}

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
    """Main product search handler with quality checks"""
    # Ensure follow-ups always use the latest user text by refreshing session state
    try:
        latest_text = _get_current_user_text(ctx)
        if isinstance(latest_text, str) and latest_text.strip():
            # Do not overwrite last_query with slot-only/ephemeral text
            pass
    except Exception:
        pass

    params = await build_search_params(ctx)
    fetcher = get_es_fetcher()
    
    # Run in thread to avoid blocking
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(None, lambda: fetcher.search(params))
    
    # Additional quality check: if we got results but they're all low quality
    if results.get('products'):
        numeric_fleans = [
            p.get('flean_percentile')
            for p in results['products']
            if isinstance(p.get('flean_percentile'), (int, float))
        ]
        if numeric_fleans:
            avg_flean = sum(numeric_fleans) / len(numeric_fleans)
            if avg_flean < 30 and params.get('brands'):
                # Products are low quality, maybe try without brand constraint
                print(f"DEBUG: Average flean percentile {avg_flean}% is low, considering fallback...")
                results['meta']['quality_warning'] = f'average_flean_percentile_{avg_flean:.1f}'
    
    # Zero-result fallback strategy (tree, ordered):
    # F&B: 1) price_any → 2) drop_hard_soft_keep_category → 3) sibling_l2_full →
    #      4) sibling_l2_price_any → 5) sibling_l2_drop_hard_soft → 6A) drop L4→L3 → 6B) drop category filters
    # Personal care: 1) price_any → 2) relax_reviews → 3) drop_hard_soft → 4) expand_size
    try:
        total = int(((results.get('meta') or {}).get('total_hits')) or 0)
    except Exception:
        total = 0
    if total == 0:
        group = str(params.get('category_group') or '').strip()
        # Personal care fallback branch (no category hierarchy)
        if group == 'personal_care':
            print("DEBUG: ZERO_RESULT | applying PC fallback sequence")

            def _drop_price_pc(d: Dict[str, Any]) -> Dict[str, Any]:
                x = dict(d)
                x.pop('price_min', None)
                x.pop('price_max', None)
                return x

            def _relax_reviews_pc(d: Dict[str, Any]) -> Dict[str, Any]:
                x = dict(d)
                try:
                    if isinstance(x.get('min_review_count'), int) and x['min_review_count'] > 0:
                        x['min_review_count'] = 0
                    else:
                        x.pop('min_review_count', None)
                except Exception:
                    x.pop('min_review_count', None)
                return x

            def _drop_hard_soft_pc(d: Dict[str, Any]) -> Dict[str, Any]:
                x = dict(d)
                for k in [
                    'brands', 'enforce_brand', 'dietary_terms', 'dietary_labels', 'must_keywords',
                    'avoid_terms', 'avoid_ingredients', 'efficacy_terms', 'skin_types', 'hair_types',
                    'min_flean_percentile', 'min_review_count'
                ]:
                    x.pop(k, None)
                return x

            # PC Step 1: Drop price
            try:
                p_pc1 = _drop_price_pc(params)
                if p_pc1 is not params:
                    print("DEBUG: PC_FALLBACK[1] PRICE_ANY")
                    alt_pc1 = await loop.run_in_executor(None, lambda: fetcher.search(p_pc1))
                    alt_pc1_total = int(((alt_pc1.get('meta') or {}).get('total_hits')) or 0)
                    if alt_pc1_total > 0:
                        alt_pc1['meta']['fallback_applied'] = 'pc_price_any'
                        return alt_pc1
            except Exception:
                pass

            # PC Step 2: Relax reviews
            try:
                p_pc2 = _relax_reviews_pc(params)
                print("DEBUG: PC_FALLBACK[2] RELAX_REVIEWS")
                alt_pc2 = await loop.run_in_executor(None, lambda: fetcher.search(p_pc2))
                alt_pc2_total = int(((alt_pc2.get('meta') or {}).get('total_hits')) or 0)
                if alt_pc2_total > 0:
                    alt_pc2['meta']['fallback_applied'] = 'pc_relax_reviews'
                    return alt_pc2
            except Exception:
                pass

            # PC Step 3: Drop hard/soft constraints
            try:
                p_pc3 = _drop_hard_soft_pc(params)
                print("DEBUG: PC_FALLBACK[3] DROP_HARD_SOFT")
                alt_pc3 = await loop.run_in_executor(None, lambda: fetcher.search(p_pc3))
                alt_pc3_total = int(((alt_pc3.get('meta') or {}).get('total_hits')) or 0)
                if alt_pc3_total > 0:
                    alt_pc3['meta']['fallback_applied'] = 'pc_drop_hard_soft'
                    return alt_pc3
            except Exception:
                pass

            # PC Step 4: Expand size to 30
            try:
                p_pc4 = dict(params)
                p_pc4['size'] = max(20, int(p_pc4.get('size', 20) or 20), 30)
                print("DEBUG: PC_FALLBACK[4] EXPAND_SIZE_30")
                alt_pc4 = await loop.run_in_executor(None, lambda: fetcher.search(p_pc4))
                alt_pc4_total = int(((alt_pc4.get('meta') or {}).get('total_hits')) or 0)
                if alt_pc4_total > 0:
                    alt_pc4['meta']['fallback_applied'] = 'pc_expand_size_30'
                    return alt_pc4
            except Exception:
                pass

            # If all PC fallbacks fail, return original results
            return results

        # F&B fallback branch
        print("DEBUG: ZERO_RESULT | applying 6-step fallback tree")

        def _drop_price(d: Dict[str, Any]) -> Dict[str, Any]:
            x = dict(d)
            x.pop('price_min', None)
            x.pop('price_max', None)
            return x

        def _drop_hard_soft(d: Dict[str, Any]) -> Dict[str, Any]:
            x = dict(d)
            # Common hard/soft constraints
            for k in [
                'brands', 'enforce_brand', 'min_flean_percentile', 'min_review_count', 'must_keywords',
                'dietary_terms', 'dietary_labels', 'avoid_terms', 'avoid_ingredients',
                'efficacy_terms', 'skin_types', 'hair_types', 'skin_concerns', 'prioritize_concerns',
            ]:
                x.pop(k, None)
            return x

        def _sibling_l2_path(d: Dict[str, Any]) -> Optional[str]:
            cat_paths = d.get('category_paths') if isinstance(d.get('category_paths'), list) else []
            primary = d.get('category_path') or (cat_paths[0] if cat_paths else '')
            if isinstance(primary, str) and primary.strip():
                parts = [p for p in primary.split('/') if p]
                if len(parts) >= 4 and parts[0] == 'f_and_b' and parts[1] == 'food':
                    return f"f_and_b/food/{parts[2]}"
                if len(parts) >= 2 and parts[0] == 'personal_care':
                    return f"personal_care/{parts[1]}"
            return None

        # Step 1: Drop price only
        try:
            p1 = _drop_price(params)
            if p1 is not params:
                print("DEBUG: FALLBACK[1] PRICE_ANY")
                alt1 = await loop.run_in_executor(None, lambda: fetcher.search(p1))
                alt1_total = int(((alt1.get('meta') or {}).get('total_hits')) or 0)
                if alt1_total > 0:
                    alt1['meta']['fallback_applied'] = 'price_any'
                    return alt1
        except Exception:
            pass

        # Step 2: Drop hard and soft filters, keep category
        try:
            p2 = _drop_hard_soft(params)
            print("DEBUG: FALLBACK[2] DROP_HARD_SOFT_KEEP_CATEGORY")
            alt2 = await loop.run_in_executor(None, lambda: fetcher.search(p2))
            alt2_total = int(((alt2.get('meta') or {}).get('total_hits')) or 0)
            if alt2_total > 0:
                alt2['meta']['fallback_applied'] = 'drop_hard_soft_keep_category'
                return alt2
        except Exception:
            pass

        # Prepare sibling L2 path (if available)
        sibling_l2 = _sibling_l2_path(params)

        # Step 3: Sibling probe with full filters
        if sibling_l2:
            try:
                p3 = dict(params)
                p3.pop('category_paths', None)
                p3['category_path'] = sibling_l2
                print(f"DEBUG: FALLBACK[3] SIBLING_L2_FULL | path={p3['category_path']}")
                alt3 = await loop.run_in_executor(None, lambda: fetcher.search(p3))
                alt3_total = int(((alt3.get('meta') or {}).get('total_hits')) or 0)
                if alt3_total > 0:
                    alt3['meta']['fallback_applied'] = 'sibling_l2_full'
                    return alt3
            except Exception:
                pass

        # Step 4: Sibling probe with dropped price
        if sibling_l2:
            try:
                p4 = _drop_price(params)
                p4.pop('category_paths', None)
                p4['category_path'] = sibling_l2
                print(f"DEBUG: FALLBACK[4] SIBLING_L2_PRICE_ANY | path={p4['category_path']}")
                alt4 = await loop.run_in_executor(None, lambda: fetcher.search(p4))
                alt4_total = int(((alt4.get('meta') or {}).get('total_hits')) or 0)
                if alt4_total > 0:
                    alt4['meta']['fallback_applied'] = 'sibling_l2_price_any'
                    return alt4
            except Exception:
                pass

        # Step 5: Sibling probe with dropped hard/soft
        if sibling_l2:
            try:
                p5 = _drop_hard_soft(params)
                p5.pop('category_paths', None)
                p5['category_path'] = sibling_l2
                print(f"DEBUG: FALLBACK[5] SIBLING_L2_DROP_HARD_SOFT | path={p5['category_path']}")
                alt5 = await loop.run_in_executor(None, lambda: fetcher.search(p5))
                alt5_total = int(((alt5.get('meta') or {}).get('total_hits')) or 0)
                if alt5_total > 0:
                    alt5['meta']['fallback_applied'] = 'sibling_l2_drop_hard_soft'
                    return alt5
            except Exception:
                pass

        # Step 6a: Narrow category from L4 → L3 (drop leaf only) and retry
        try:
            p6a = dict(params)
            cat_paths = p6a.get('category_paths') if isinstance(p6a.get('category_paths'), list) else []
            primary = p6a.get('category_path') or (cat_paths[0] if cat_paths else '')
            truncated = None
            if isinstance(primary, str) and primary.strip():
                parts = [p for p in primary.split('/') if p]
                # Expect: f_and_b/food/L3/L4 or personal_care/L2/L3
                if len(parts) >= 4 and parts[0] == 'f_and_b' and parts[1] == 'food':
                    truncated = f"f_and_b/food/{parts[2]}"  # keep L3, drop L4
                elif len(parts) >= 3 and parts[0] == 'personal_care':
                    truncated = f"personal_care/{parts[1]}"  # keep L2, drop leaf
            if truncated:
                p6a.pop('category_paths', None)
                p6a['category_path'] = truncated
                print(f"DEBUG: FALLBACK[6A] DROP_CATEGORY_L4_TO_L3 | path={p6a['category_path']}")
                alt6a = await loop.run_in_executor(None, lambda: fetcher.search(p6a))
                alt6a_total = int(((alt6a.get('meta') or {}).get('total_hits')) or 0)
                if alt6a_total > 0:
                    alt6a['meta']['fallback_applied'] = 'drop_category_l4_to_l3'
                    return alt6a
        except Exception:
            pass

        # Step 6b: Drop category paths entirely (remove L3), keep category_group
        try:
            p6b = dict(params)
            dropped = False
            if p6b.pop('category_paths', None) is not None:
                dropped = True
            if p6b.pop('category_path', None) is not None:
                dropped = True
            if dropped:
                print("DEBUG: FALLBACK[6B] DROP_CATEGORY_L3 (remove category_path(s))")
                alt6b = await loop.run_in_executor(None, lambda: fetcher.search(p6b))
                alt6b_total = int(((alt6b.get('meta') or {}).get('total_hits')) or 0)
                if alt6b_total > 0:
                    alt6b['meta']['fallback_applied'] = 'drop_category_l3'
                    return alt6b
        except Exception:
            pass

    return results

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