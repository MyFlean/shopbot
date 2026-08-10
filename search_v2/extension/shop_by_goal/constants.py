"""Constants for shop-by-goal mapping and defaults."""

from __future__ import annotations

import os
from typing import Dict

DEFAULT_GOAL_CONFIG_URL = "https://api.flean.ai/ui/app-config/goal"
GOAL_CONFIG_URL_ENV = "GOAL_CONFIG_URL"
DEFAULT_DIET_CONFIG_URL = "https://api.flean.ai/ui/app-config/diet"
DIET_CONFIG_URL_ENV = "DIET_CONFIG_URL"
GOAL_CONFIG_TIMEOUT_SEC = float(os.getenv("GOAL_CONFIG_TIMEOUT_SEC", "3.0"))

DERIVED_FIELD_SCRIPTS: Dict[str, str] = {
    "derived.protein_cal_pct": (
        "double energy = (doc.containsKey('category_data.nutritional.nutri_breakdown.energy kcal') "
        "&& !doc['category_data.nutritional.nutri_breakdown.energy kcal'].empty) "
        "? doc['category_data.nutritional.nutri_breakdown.energy kcal'].value : 0.0; "
        "double protein = (doc.containsKey('category_data.nutritional.nutri_breakdown.protein g') "
        "&& !doc['category_data.nutritional.nutri_breakdown.protein g'].empty) "
        "? doc['category_data.nutritional.nutri_breakdown.protein g'].value : 0.0; "
        "double metric = energy <= 0.0 ? 0.0 : ((protein * 4.0) / energy) * 100.0; "
    ),
    "derived.fat_cal_pct": (
        "double energy = (doc.containsKey('category_data.nutritional.nutri_breakdown.energy kcal') "
        "&& !doc['category_data.nutritional.nutri_breakdown.energy kcal'].empty) "
        "? doc['category_data.nutritional.nutri_breakdown.energy kcal'].value : 0.0; "
        "double fat = (doc.containsKey('category_data.nutritional.nutri_breakdown.total fat g') "
        "&& !doc['category_data.nutritional.nutri_breakdown.total fat g'].empty) "
        "? doc['category_data.nutritional.nutri_breakdown.total fat g'].value : 0.0; "
        "double metric = energy <= 0.0 ? 0.0 : ((fat * 9.0) / energy) * 100.0; "
    ),
    "derived.net_carbs": (
        "double carbs = (doc.containsKey('category_data.nutritional.nutri_breakdown.carbohydrate g') "
        "&& !doc['category_data.nutritional.nutri_breakdown.carbohydrate g'].empty) "
        "? doc['category_data.nutritional.nutri_breakdown.carbohydrate g'].value : 0.0; "
        "double fiber = (doc.containsKey('category_data.nutritional.nutri_breakdown.fiber g') "
        "&& !doc['category_data.nutritional.nutri_breakdown.fiber g'].empty) "
        "? doc['category_data.nutritional.nutri_breakdown.fiber g'].value : 0.0; "
        "double metric = carbs - fiber; "
    ),
    "derived.micronutrient_count": (
        "int count = 0; "
        "if (doc.containsKey('category_data.nutritional.nutri_breakdown.vitamin a mcg') "
        "&& !doc['category_data.nutritional.nutri_breakdown.vitamin a mcg'].empty) { count += 1; } "
        "if (doc.containsKey('category_data.nutritional.nutri_breakdown.vitamin c') "
        "&& !doc['category_data.nutritional.nutri_breakdown.vitamin c'].empty) { count += 1; } "
        "if (doc.containsKey('category_data.nutritional.nutri_breakdown.zinc') "
        "&& !doc['category_data.nutritional.nutri_breakdown.zinc'].empty) { count += 1; } "
        "if (doc.containsKey('category_data.nutritional.nutri_breakdown.iron mg') "
        "&& !doc['category_data.nutritional.nutri_breakdown.iron mg'].empty) { count += 1; } "
        "if (doc.containsKey('category_data.nutritional.nutri_breakdown.folate dfe mcg') "
        "&& !doc['category_data.nutritional.nutri_breakdown.folate dfe mcg'].empty) { count += 1; } "
        "if (doc.containsKey('category_data.nutritional.nutri_breakdown.vitamin b6 mg') "
        "&& !doc['category_data.nutritional.nutri_breakdown.vitamin b6 mg'].empty) { count += 1; } "
        "double metric = count; "
    ),
    "derived.additives_count": (
        "double metric = (doc.containsKey('ingredients.additives') "
        "&& !doc['ingredients.additives'].empty) ? doc['ingredients.additives'].size() : 0.0; "
    ),
}

DERIVED_OP_MAP: Dict[str, str] = {
    "eq": "metric == params.value",
    "neq": "metric != params.value",
    "gt": "metric > params.value",
    "gte": "metric >= params.value",
    "lt": "metric < params.value",
    "lte": "metric <= params.value",
}

SORT_FIELD_TO_UNIFIED_SORT: Dict[tuple[str, str], str] = {
    ("stats.protein_percentiles.category_percentile", "desc"): "protein_desc",
    ("stats.protein_percentiles.subcategory_percentile", "desc"): "protein_desc",
    ("nutri.protein_g", "desc"): "protein_desc",
    ("stats.fiber_percentiles.category_percentile", "desc"): "fiber_desc",
    ("nutri.fiber_g", "desc"): "fiber_desc",
    ("flean_score.adjusted_score", "desc"): "flean_score_desc",
    ("stats.sugar_penalty_percentiles.global_percentile", "asc"): "relevance",
}
