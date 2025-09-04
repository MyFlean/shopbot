# shopping_bot/scoring_config.py
"""
Category-Based Dynamic Scoring Configuration
────────────────────────────────────────────
Defines which bonuses and penalties apply to each subcategory
for intelligent, context-aware product ranking.
"""

from typing import Dict, List, Any

# Scoring configuration per subcategory
CATEGORY_SCORING_RULES: Dict[str, Dict[str, Any]] = {
    # ─────────────────────────────────
    # LIGHT BITES
    # ─────────────────────────────────
    "chips_and_crisps": {
        "bonuses": [
            {"field": "stats.protein_percentiles.subcategory_percentile", "weight": 1.2, "threshold": 70},
            {"field": "stats.fiber_percentiles.subcategory_percentile", "weight": 1.3, "threshold": 60},
            {"field": "stats.wholefood_percentiles.subcategory_percentile", "weight": 1.5, "threshold": 50},
        ],
        "penalties": [
            {"field": "stats.trans_fat_penalty_percentiles.subcategory_percentile", "weight": 0.7, "threshold": 70},
            {"field": "stats.sodium_penalty_percentiles.subcategory_percentile", "weight": 0.8, "threshold": 80},
            {"field": "stats.oil_penalty_percentiles.subcategory_percentile", "weight": 0.85, "threshold": 75},
        ]
    },
    "popcorn": {
        "bonuses": [
            {"field": "stats.fiber_percentiles.subcategory_percentile", "weight": 1.4, "threshold": 60},
            {"field": "stats.wholefood_percentiles.subcategory_percentile", "weight": 1.5, "threshold": 50},
        ],
        "penalties": [
            {"field": "stats.oil_penalty_percentiles.subcategory_percentile", "weight": 0.8, "threshold": 70},
            {"field": "stats.sodium_penalty_percentiles.subcategory_percentile", "weight": 0.85, "threshold": 75},
        ]
    },
    "nachos": {
        "bonuses": [
            {"field": "stats.protein_percentiles.subcategory_percentile", "weight": 1.3, "threshold": 60},
            {"field": "stats.fiber_percentiles.subcategory_percentile", "weight": 1.2, "threshold": 60},
        ],
        "penalties": [
            {"field": "stats.sodium_penalty_percentiles.subcategory_percentile", "weight": 0.75, "threshold": 80},
            {"field": "stats.saturated_fat_penalty_percentiles.subcategory_percentile", "weight": 0.8, "threshold": 75},
        ]
    },
    "energy_bars": {
        "bonuses": [
            {"field": "stats.protein_percentiles.subcategory_percentile", "weight": 1.8, "threshold": 50},
            {"field": "stats.fiber_percentiles.subcategory_percentile", "weight": 1.4, "threshold": 60},
            {"field": "stats.fortification_percentiles.subcategory_percentile", "weight": 1.3, "threshold": 50},
        ],
        "penalties": [
            {"field": "stats.sugar_penalty_percentiles.subcategory_percentile", "weight": 0.7, "threshold": 75},
            {"field": "stats.empty_food_penalty_percentiles.subcategory_percentile", "weight": 0.75, "threshold": 70},
        ]
    },
    
    # ─────────────────────────────────
    # SWEET TREATS
    # ─────────────────────────────────
    "chocolates": {
        "bonuses": [
            {"field": "stats.simplicity_percentiles.subcategory_percentile", "weight": 1.3, "threshold": 60},
            {"field": "stats.wholefood_percentiles.subcategory_percentile", "weight": 1.2, "threshold": 50},
        ],
        "penalties": [
            {"field": "stats.sugar_penalty_percentiles.subcategory_percentile", "weight": 0.9, "threshold": 90},  # Less strict for chocolates
            {"field": "stats.additives_penalty_percentiles.subcategory_percentile", "weight": 0.8, "threshold": 70},
        ]
    },
    "cookies": {
        "bonuses": [
            {"field": "stats.fiber_percentiles.subcategory_percentile", "weight": 1.3, "threshold": 50},
            {"field": "stats.protein_percentiles.subcategory_percentile", "weight": 1.2, "threshold": 60},
        ],
        "penalties": [
            {"field": "stats.sugar_penalty_percentiles.subcategory_percentile", "weight": 0.85, "threshold": 80},
            {"field": "stats.trans_fat_penalty_percentiles.subcategory_percentile", "weight": 0.7, "threshold": 70},
        ]
    },
    "candies_gums_and_mints": {
        "bonuses": [
            {"field": "stats.simplicity_percentiles.subcategory_percentile", "weight": 1.4, "threshold": 50},
        ],
        "penalties": [
            {"field": "stats.sugar_penalty_percentiles.subcategory_percentile", "weight": 0.95, "threshold": 95},  # Very lenient
            {"field": "stats.sweetener_penalty_percentiles.subcategory_percentile", "weight": 0.8, "threshold": 70},
        ]
    },
    
    # ─────────────────────────────────
    # BEVERAGES
    # ─────────────────────────────────
    "soft_drinks": {
        "bonuses": [
            {"field": "stats.fortification_percentiles.subcategory_percentile", "weight": 1.2, "threshold": 50},
        ],
        "penalties": [
            {"field": "stats.sugar_penalty_percentiles.subcategory_percentile", "weight": 0.6, "threshold": 70},
            {"field": "stats.sweetener_penalty_percentiles.subcategory_percentile", "weight": 0.7, "threshold": 60},
            {"field": "stats.calories_penalty_percentiles.subcategory_percentile", "weight": 0.8, "threshold": 75},
        ]
    },
    "fruit_juices": {
        "bonuses": [
            {"field": "stats.fortification_percentiles.subcategory_percentile", "weight": 1.3, "threshold": 50},
            {"field": "stats.wholefood_percentiles.subcategory_percentile", "weight": 1.4, "threshold": 60},
        ],
        "penalties": [
            {"field": "stats.sugar_penalty_percentiles.subcategory_percentile", "weight": 0.9, "threshold": 85},  # Natural sugars OK
            {"field": "stats.additives_penalty_percentiles.subcategory_percentile", "weight": 0.7, "threshold": 70},
        ]
    },
    "energy_and_non_alcoholic_drinks": {
        "bonuses": [
            {"field": "stats.fortification_percentiles.subcategory_percentile", "weight": 1.5, "threshold": 50},
        ],
        "penalties": [
            {"field": "stats.sugar_penalty_percentiles.subcategory_percentile", "weight": 0.7, "threshold": 75},
            {"field": "stats.calories_penalty_percentiles.subcategory_percentile", "weight": 0.75, "threshold": 80},
        ]
    },
    
    # ─────────────────────────────────
    # DAIRY & BAKERY
    # ─────────────────────────────────
    "bread_and_buns": {
        "bonuses": [
            {"field": "stats.fiber_percentiles.subcategory_percentile", "weight": 1.8, "threshold": 50},
            {"field": "stats.protein_percentiles.subcategory_percentile", "weight": 1.4, "threshold": 60},
            {"field": "stats.wholefood_percentiles.subcategory_percentile", "weight": 1.6, "threshold": 50},
        ],
        "penalties": [
            {"field": "stats.sodium_penalty_percentiles.subcategory_percentile", "weight": 0.75, "threshold": 75},
            {"field": "stats.sugar_penalty_percentiles.subcategory_percentile", "weight": 0.85, "threshold": 80},
        ]
    },
    "yogurt_and_shrikhand": {
        "bonuses": [
            {"field": "stats.protein_percentiles.subcategory_percentile", "weight": 1.6, "threshold": 50},
            {"field": "stats.fortification_percentiles.subcategory_percentile", "weight": 1.2, "threshold": 50},
        ],
        "penalties": [
            {"field": "stats.sugar_penalty_percentiles.subcategory_percentile", "weight": 0.7, "threshold": 75},
            {"field": "stats.saturated_fat_penalty_percentiles.subcategory_percentile", "weight": 0.85, "threshold": 80},
        ]
    },
    "cheese": {
        "bonuses": [
            {"field": "stats.protein_percentiles.subcategory_percentile", "weight": 1.7, "threshold": 50},
            {"field": "stats.simplicity_percentiles.subcategory_percentile", "weight": 1.3, "threshold": 60},
        ],
        "penalties": [
            {"field": "stats.sodium_penalty_percentiles.subcategory_percentile", "weight": 0.9, "threshold": 85},  # Cheese is naturally salty
            {"field": "stats.saturated_fat_penalty_percentiles.subcategory_percentile", "weight": 0.9, "threshold": 85},
        ]
    },
    
    # ─────────────────────────────────
    # BREAKFAST
    # ─────────────────────────────────
    "breakfast_cereals": {
        "bonuses": [
            {"field": "stats.fiber_percentiles.subcategory_percentile", "weight": 1.7, "threshold": 50},
            {"field": "stats.protein_percentiles.subcategory_percentile", "weight": 1.3, "threshold": 60},
            {"field": "stats.fortification_percentiles.subcategory_percentile", "weight": 1.5, "threshold": 50},
        ],
        "penalties": [
            {"field": "stats.sugar_penalty_percentiles.subcategory_percentile", "weight": 0.6, "threshold": 70},
            {"field": "stats.sodium_penalty_percentiles.subcategory_percentile", "weight": 0.85, "threshold": 80},
        ]
    },
    "muesli_and_oats": {
        "bonuses": [
            {"field": "stats.fiber_percentiles.subcategory_percentile", "weight": 1.8, "threshold": 50},
            {"field": "stats.protein_percentiles.subcategory_percentile", "weight": 1.5, "threshold": 50},
            {"field": "stats.wholefood_percentiles.subcategory_percentile", "weight": 1.7, "threshold": 50},
        ],
        "penalties": [
            {"field": "stats.sugar_penalty_percentiles.subcategory_percentile", "weight": 0.7, "threshold": 75},
            {"field": "stats.additives_penalty_percentiles.subcategory_percentile", "weight": 0.75, "threshold": 70},
        ]
    },
    
    # ─────────────────────────────────
    # DEFAULT (fallback for unmapped categories)
    # ─────────────────────────────────
    "_default": {
        "bonuses": [
            {"field": "stats.protein_percentiles.subcategory_percentile", "weight": 1.2, "threshold": 70},
            {"field": "stats.fiber_percentiles.subcategory_percentile", "weight": 1.2, "threshold": 70},
            {"field": "stats.wholefood_percentiles.subcategory_percentile", "weight": 1.3, "threshold": 60},
        ],
        "penalties": [
            {"field": "stats.sugar_penalty_percentiles.subcategory_percentile", "weight": 0.8, "threshold": 80},
            {"field": "stats.sodium_penalty_percentiles.subcategory_percentile", "weight": 0.8, "threshold": 80},
            {"field": "stats.trans_fat_penalty_percentiles.subcategory_percentile", "weight": 0.7, "threshold": 70},
        ]
    }
}

def get_scoring_rules(subcategory: str) -> Dict[str, List[Dict[str, Any]]]:
    """
    Get scoring rules for a specific subcategory.
    Falls back to default rules if subcategory not found.
    
    Args:
        subcategory: The subcategory slug (e.g., "chips_and_crisps")
    
    Returns:
        Dict with 'bonuses' and 'penalties' lists
    """
    # Clean the subcategory string
    subcategory = subcategory.lower().replace(" ", "_").replace("-", "_")
    
    # Return specific rules or default
    return CATEGORY_SCORING_RULES.get(subcategory, CATEGORY_SCORING_RULES["_default"])

def build_function_score_functions(subcategory: str, include_flean: bool = True) -> List[Dict[str, Any]]:
    """
    Build Elasticsearch function_score functions based on subcategory.
    
    Args:
        subcategory: The product subcategory
        include_flean: Whether to include the base flean score (default True)
    
    Returns:
        List of function score configurations for ES query
    """
    functions = []
    
    # Always include flean score as the base quality multiplier (normalized > 1)
    # Using script_score here so that with score_mode=multiply the overall score is
    # multiplied by a factor in ~[1.0, 2.0] based on the percentile.
    if include_flean:
        functions.append({
            "script_score": {
                "script": {
                    "source": (
                        "double p = (doc.containsKey('stats.adjusted_score_percentiles.subcategory_percentile') \n"
                        "            && doc['stats.adjusted_score_percentiles.subcategory_percentile'].size() > 0) \n"
                        "    ? doc['stats.adjusted_score_percentiles.subcategory_percentile'].value : 50; \n"
                        "return 1.0 + (Math.max(0.0, Math.min(100.0, p)) / 100.0);"
                    )
                }
            }
        })
    
    # Get category-specific rules
    rules = get_scoring_rules(subcategory)
    
    # Add bonuses
    for bonus in rules.get("bonuses", []):
        functions.append({
            "filter": {
                "range": {
                    bonus["field"]: {
                        "gte": bonus["threshold"]
                    }
                }
            },
            "weight": bonus["weight"]
        })
    
    # Add penalties
    for penalty in rules.get("penalties", []):
        functions.append({
            "filter": {
                "range": {
                    penalty["field"]: {
                        "gte": penalty["threshold"]
                    }
                }
            },
            "weight": penalty["weight"]
        })
    
    return functions

# Export for use in other modules
__all__ = ["CATEGORY_SCORING_RULES", "get_scoring_rules", "build_function_score_functions"]