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
ELASTIC_INDEX = os.getenv("ELASTIC_INDEX", "flean-v3")
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
    def _normalize_rel_path(path_str: str) -> str:
        parts = [x for x in str(path_str).split("/") if x]
        core_parts = parts[:]
        if core_parts and core_parts[0] in ("f_and_b", "personal_care"):
            core_parts = core_parts[1:]
        if core_parts and core_parts[0] == "food":
            core_parts = core_parts[1:]
        return "/".join(core_parts)
    if isinstance(category_path, str) and category_path.strip():
        rel = _normalize_rel_path(category_path.strip())
        if rel:
            normalized_paths.append(rel)
    for cp in category_paths[:3]:
        try:
            s = str(cp).strip()
            if s:
                rel = _normalize_rel_path(s)
                if rel:
                    normalized_paths.append(rel)
        except Exception:
            pass
    if normalized_paths:
        # Deduplicate
        uniq: List[str] = []
        for rel in normalized_paths:
            if rel not in uniq:
                uniq.append(rel)
        group = (category_group or "").strip()
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
        for rel in uniq[:3]:
            if group == "personal_care":
                full = f"personal_care/{rel}"
                if prefer_keyword:
                    should_cat.append({"term": {"category_paths.keyword": full}})
                else:
                    should_cat.extend([
                        {"term": {"category_paths": full}},
                        {"wildcard": {"category_paths": {"value": f"*{full}*"}}},
                    ])
            elif group == "f_and_b":
                full = f"f_and_b/food/{rel}"
                if prefer_keyword:
                    should_cat.append({"term": {"category_paths.keyword": full}})
                else:
                    should_cat.extend([
                        {"term": {"category_paths": full}},
                        {"wildcard": {"category_paths": {"value": f"*{full}*"}}},
                    ])
            else:
                # Unknown group, try both
                fb = f"f_and_b/food/{rel}"
                pc = f"personal_care/{rel}"
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

    # Dietary labels with boost
    dietary_labels = p.get("dietary_labels") or p.get("dietary_terms") or []
    if isinstance(dietary_labels, list) and dietary_labels:
        for label in dietary_labels:
            if label and str(label).strip():
                shoulds.append({
            "match": {
                "package_claims.dietary_labels": {
                            "query": str(label).strip(),
                            "boost": 3.0
                        }
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
    
    # Main query matching
    if isinstance(keywords, list) and keywords:
        for kw in keywords[:5]:
            kw_str = str(kw).strip()
            if kw_str:
                shoulds.append({
                    "multi_match": {
                        "query": kw_str,
                        "type": "best_fields",
                        "fields": dynamic_fields,
                        "fuzziness": "AUTO"
                    }
                })
    elif q_text:
        shoulds.append({
            "multi_match": {
                "query": q_text,
                "type": "best_fields",
                "fields": dynamic_fields,
                "fuzziness": "AUTO"
            }
        })

    # 3b) Hard must keywords (e.g., flavor tokens like 'orange') to avoid mismatched variants
    must_keywords = p.get("must_keywords") or []
    if isinstance(must_keywords, list) and must_keywords:
        for kw in must_keywords[:3]:
            kw_str = str(kw).strip()
            if kw_str:
                musts.append({
                    "multi_match": {
                        "query": kw_str,
                        "type": "phrase",
                        "fields": ["name^6", "description^2", "combined_text"],
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

    # Set minimum_should_match to 1 if we have textual should clauses; else 0
    try:
        has_text_should = any(
            any(k in s for k in ["multi_match", "match", "match_phrase"]) for s in shoulds
        )
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

    return body

def _build_skin_es_query(params: Dict[str, Any]) -> Dict[str, Any]:
    """Build a personal care (skin) ES query matching the working Postman shape."""
    p = params or {}

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
                "id", "name", "brand", "price", "mrp", "description",
                "category_group", "category_paths", "hero_image.*",
                "review_stats.*",
                "skin_compatibility.*",
                "efficacy.*",
                "side_effects.*",
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

    # Category paths from params
    cat_paths = []
    primary_path = p.get("category_path")
    if isinstance(primary_path, str) and primary_path.strip():
        cat_paths.append(primary_path.strip())
    for cp in (p.get("category_paths") or [])[:3]:
        try:
            s = str(cp).strip()
            if s and s not in cat_paths:
                cat_paths.append(s)
        except Exception:
            pass
    if cat_paths:
        # Use terms on keyword array for exact path matching
        filters.append({"terms": {"category_paths": cat_paths}})

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
        filters.append({"terms": {"brand": p["brands"]}})

    # Must: main query
    if q_text:
        musts.append({
            "multi_match": {
                "query": q_text,
                "type": "best_fields",
                "fields": ["name^4", "description^2", "use", "combined_text"],
                "fuzziness": "AUTO",
            }
        })

    # Skin types → strong should
    for st in (p.get("skin_types") or [])[:4]:
        st_s = str(st).strip().lower()
        if not st_s:
            continue
        shoulds.append({
            "bool": {
                "must": [
                    {"term": {"skin_compatibility.skin_type": st_s}},
                    {"range": {"skin_compatibility.sentiment_score": {"gte": 0.6}}},
                    {"range": {"skin_compatibility.confidence_score": {"gte": 0.3}}},
                ],
                "boost": 5.0,
            }
        })

    # Concerns → efficacy signals (merge skin + hair), non-nested friendly separate boosts
    concern_boost = 3.0 if bool(p.get("prioritize_concerns")) else 2.0
    merged_concerns = []
    try:
        for lst in [p.get("skin_concerns") or [], p.get("hair_concerns") or []]:
            for it in lst:
                if it and it not in merged_concerns:
                    merged_concerns.append(it)
    except Exception:
        merged_concerns = (p.get("skin_concerns") or [])
    for cn in merged_concerns[:4]:
        cn_s = str(cn).strip().lower()
        if not cn_s:
            continue
        shoulds.append({
            "term": {"efficacy.aspect_name": {"value": cn_s, "boost": concern_boost}}
        })
        shoulds.append({
            "range": {"efficacy.sentiment_score": {"gte": 0.7, "boost": concern_boost}}
        })

    # Avoid ingredients → side_effects only (drop ingredients.raw_text for now)
    for ing in (p.get("avoid_ingredients") or [])[:6]:
        ing_s = str(ing).strip()
        if not ing_s:
            continue
        must_not.append({
            "bool": {
                "must": [
                    {"match": {"side_effects.effect_name": ing_s}},
                    {"range": {"side_effects.severity_score": {"gte": 0.3}}},
                ]
            }
        })

    # Personal care: treat should as pure boosts
    bq["minimum_should_match"] = 0

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
        if domain == "personal_care":
            from ..llm_service import LLMService  # type: ignore
            llm_service = LLMService()
            skin_params = await llm_service.generate_skin_es_params(ctx)
            if isinstance(skin_params, dict) and skin_params.get("q"):
                final_params: Dict[str, Any] = dict(skin_params)
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
    
    # Zero-result fallback strategy: relax constraints or probe siblings
    try:
        total = int(((results.get('meta') or {}).get('total_hits')) or 0)
    except Exception:
        total = 0
    if total == 0:
        print("DEBUG: ZERO_RESULT | attempting fallback strategies")
        # Strategy A: relax price window slightly if price_max present
        relaxed_ran = False
        try:
            p2 = dict(params)
            if isinstance(p2.get('price_max'), (int, float)):
                p2['price_max'] = float(p2['price_max']) * 1.25
                relaxed_ran = True
                print(f"DEBUG: RELAX_PRICE | new_price_max={p2['price_max']}")
                alt = await loop.run_in_executor(None, lambda: fetcher.search(p2))
                alt_total = int(((alt.get('meta') or {}).get('total_hits')) or 0)
                if alt_total > 0:
                    alt['meta']['fallback_applied'] = 'relax_price_max_25pct'
                    return alt
        except Exception:
            pass

        # Strategy B: category sibling probe within same l2
        try:
            cat_paths = params.get('category_paths') if isinstance(params.get('category_paths'), list) else []
            primary = params.get('category_path') or (cat_paths[0] if cat_paths else '')
            rel = ''
            if isinstance(primary, str) and primary.strip():
                # extract relative path l2/l3
                parts = [x for x in primary.split('/') if x]
                # expect f_and_b/food/l2/l3 or personal_care/l2/l3
                if len(parts) >= 4 and parts[0] == 'f_and_b' and parts[1] == 'food':
                    l2 = parts[2]
                    # For sibling probe: replace l3 with None to broaden
                    sibling_candidates = [f"f_and_b/food/{l2}"]
                elif len(parts) >= 2 and parts[0] == 'personal_care':
                    l2 = parts[1]
                    sibling_candidates = [f"personal_care/{l2}"]
                else:
                    sibling_candidates = []
                if sibling_candidates:
                    p3 = dict(params)
                    p3.pop('category_paths', None)
                    p3['category_path'] = sibling_candidates[0]
                    print(f"DEBUG: SIBLING_PROBE | path={p3['category_path']}")
                    alt2 = await loop.run_in_executor(None, lambda: fetcher.search(p3))
                    alt2_total = int(((alt2.get('meta') or {}).get('total_hits')) or 0)
                    if alt2_total > 0:
                        alt2['meta']['fallback_applied'] = 'sibling_probe_l2'
                        return alt2
        except Exception:
            pass

        # Strategy C: drop dietary terms if present but too restrictive
        try:
            if (params.get('dietary_terms') or params.get('dietary_labels')) and not relaxed_ran:
                p4 = dict(params)
                p4.pop('dietary_terms', None)
                p4.pop('dietary_labels', None)
                print("DEBUG: DROP_DIETARY_FALLBACK")
                alt3 = await loop.run_in_executor(None, lambda: fetcher.search(p4))
                alt3_total = int(((alt3.get('meta') or {}).get('total_hits')) or 0)
                if alt3_total > 0:
                    alt3['meta']['fallback_applied'] = 'drop_dietary'
                    return alt3
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