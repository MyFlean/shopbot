"""
search_v2/retrieval/filters.py
───────────────────────────────
Unified filter engine for Search V2.

SearchFilters is the canonical typed representation of every filter dimension
the platform supports. All entry points (REST API, ShopBot gateway, NL
extractor) produce a SearchFilters object; build_filter_clauses() converts it
to the OpenSearch bool-query clause groups needed by the query builders.

This is the single source of truth for filter construction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ── Availability channel paths ─────────────────────────────────────────────────
_AVAILABILITY_IN_STOCK_PATHS = [
    "availability.in_stock",
    "availability.blinkit.in_stock",
    "availability.zepto.in_stock",
]

# ── Nutritional field map ─────────────────────────────────────────────────────
# Single source of truth for every nutrition dimension the schema exposes —
# query_processing/nl_filter_extractor.py derives its NL parsing vocabulary
# from this map rather than maintaining its own separate list, so adding a
# new nutrient here automatically makes it understood in natural-language
# queries too (see nl_filter_extractor.py's _NUTRIENT_ALIASES).
NUTRIENT_FIELD_MAP: Dict[str, str] = {
    "protein_g": "category_data.nutritional.nutri_breakdown_updated.protein_g",
    "sugar_g": "category_data.nutritional.nutri_breakdown_updated.sugar_g",
    "fat_g": "category_data.nutritional.nutri_breakdown_updated.fat_g",
    "fiber_g": "category_data.nutritional.nutri_breakdown_updated.fiber_g",
    "sodium_mg": "category_data.nutritional.nutri_breakdown_updated.sodium_mg",
    "energy_kcal": "category_data.nutritional.nutri_breakdown_updated.energy_kcal",
    "carbs_g": "category_data.nutritional.nutri_breakdown_updated.carbs_g",
    "saturated_fat_g": "category_data.nutritional.nutri_breakdown_updated.saturated_fat_g",
    "trans_fat_g": "category_data.nutritional.nutri_breakdown_updated.trans_fat_g",
    # Aliases — every surface word a user or product-name might use for the
    # same field, all pointing at the same ES path as their canonical key.
    "calories": "category_data.nutritional.nutri_breakdown_updated.energy_kcal",
    "calorie": "category_data.nutritional.nutri_breakdown_updated.energy_kcal",
    "kcal": "category_data.nutritional.nutri_breakdown_updated.energy_kcal",
    "cal": "category_data.nutritional.nutri_breakdown_updated.energy_kcal",
    "cals": "category_data.nutritional.nutri_breakdown_updated.energy_kcal",
    "energy": "category_data.nutritional.nutri_breakdown_updated.energy_kcal",
    "protein": "category_data.nutritional.nutri_breakdown_updated.protein_g",
    "proteins": "category_data.nutritional.nutri_breakdown_updated.protein_g",
    "sugar": "category_data.nutritional.nutri_breakdown_updated.sugar_g",
    "sugars": "category_data.nutritional.nutri_breakdown_updated.sugar_g",
    "fat": "category_data.nutritional.nutri_breakdown_updated.fat_g",
    "fats": "category_data.nutritional.nutri_breakdown_updated.fat_g",
    "fiber": "category_data.nutritional.nutri_breakdown_updated.fiber_g",
    "fibre": "category_data.nutritional.nutri_breakdown_updated.fiber_g",
    "sodium": "category_data.nutritional.nutri_breakdown_updated.sodium_mg",
    "salt": "category_data.nutritional.nutri_breakdown_updated.sodium_mg",
    "carbs": "category_data.nutritional.nutri_breakdown_updated.carbs_g",
    "carb": "category_data.nutritional.nutri_breakdown_updated.carbs_g",
    "carbohydrate": "category_data.nutritional.nutri_breakdown_updated.carbs_g",
    "carbohydrates": "category_data.nutritional.nutri_breakdown_updated.carbs_g",
    "saturated_fat": "category_data.nutritional.nutri_breakdown_updated.saturated_fat_g",
    "saturated fat": "category_data.nutritional.nutri_breakdown_updated.saturated_fat_g",
    "trans_fat": "category_data.nutritional.nutri_breakdown_updated.trans_fat_g",
    "trans fat": "category_data.nutritional.nutri_breakdown_updated.trans_fat_g",
}

# Canonical keys — the subset of NUTRIENT_FIELD_MAP that MacroFilter.nutrient
# should actually be set to (the *_g/_mg/_kcal keys, not their aliases).
# Derived automatically: any key whose ES field path isn't already pointed to
# by a "shorter-named" key is treated as canonical — in practice this is
# simply the *_g/_mg/_kcal-suffixed keys, computed here rather than hardcoded
# a second time so this list can never drift from NUTRIENT_FIELD_MAP itself.
CANONICAL_NUTRIENT_KEYS: List[str] = [
    k for k in NUTRIENT_FIELD_MAP if k.endswith(("_g", "_mg", "_kcal"))
]

# ── Dietary label normalization ────────────────────────────────────────────────
DIETARY_LABEL_ALIASES: Dict[str, str] = {
    "glutenfree": "GLUTEN FREE",
    "gluten-free": "GLUTEN FREE",
    "gluten free": "GLUTEN FREE",
    "vegan": "VEGAN",
    "vegetarian": "VEGETARIAN",
    "palmoilfree": "NO PALM OIL",
    "palm oil free": "NO PALM OIL",
    "palm-oil-free": "NO PALM OIL",
    "sugarfree": "SUGAR FREE",
    "sugar free": "SUGAR FREE",
    "sugar-free": "SUGAR FREE",
    "no added sugar": "NO ADDED SUGAR",
    "lowsodium": "LOW SODIUM",
    "low sodium": "LOW SODIUM",
    "lowsugar": "LOW SUGAR",
    "low sugar": "LOW SUGAR",
    "organic": "ORGANIC",
    "dairyfree": "DAIRY FREE",
    "dairy free": "DAIRY FREE",
    "dairy-free": "DAIRY FREE",
    "nutfree": "NUT FREE",
    "nut free": "NUT FREE",
    "soyfree": "SOY FREE",
    "soy free": "SOY FREE",
    "keto": "KETO",
    "highprotein": "HIGH PROTEIN",
    "high protein": "HIGH PROTEIN",
    "lowfat": "LOW FAT",
    "low fat": "LOW FAT",
    "wholegrain": "WHOLE GRAIN",
    "whole grain": "WHOLE GRAIN",
    "whole-grain": "WHOLE GRAIN",
    "nopreservatives": "NO PRESERVATIVES",
    "no preservatives": "NO PRESERVATIVES",
    "nongmo": "NON GMO",
    "non gmo": "NON GMO",
    "non-gmo": "NON GMO",
}

# Canonical dietary labels that duplicate an active goal/diet registry ID.
# When goal_diet_ids is set, skip these tag-only filters — the registry applies
# hybrid inclusion (nutrition + optional tags), not mandatory tag gating.
DIETARY_LABEL_TO_GOAL_DIET: Dict[str, str] = {
    "KETO": "keto",
    "VEGAN": "vegan",
    "VEGETARIAN": "vegetarian",
    "GLUTEN FREE": "gluten_free",
    "DAIRY FREE": "dairy_free",
    "LOW SUGAR": "low_sugar",
    "LOW SODIUM": "low_sodium",
    "HIGH PROTEIN": "high_protein",
    "LOW FAT": "low_fat",
    "NUT FREE": "nut_free",
}


def normalize_dietary_label(label: str) -> str:
    """Normalize a dietary label to its canonical uppercase form."""
    low = label.strip().lower()
    return DIETARY_LABEL_ALIASES.get(low, label.strip().upper())


def _to_dietary_tag_key(label: str) -> str:
    """Map canonical/alias dietary label text to tag-style key (e.g. GLUTEN FREE -> gluten_free)."""
    return " ".join(str(label or "").strip().lower().split()).replace("-", "_").replace(" ", "_")


def _effective_dietary_labels(sf: "SearchFilters") -> List[str]:
    """Drop dietary tag filters already covered by an active goal/diet registry ID."""
    labels = sf.dietary_labels or []
    if not labels or not sf.goal_diet_ids:
        return list(labels)
    active = set(sf.goal_diet_ids)
    return [
        label
        for label in labels
        if DIETARY_LABEL_TO_GOAL_DIET.get(normalize_dietary_label(str(label))) not in active
    ]


# ── Nutrition profile filter → ES range clause ────────────────────────────────
# Uses subcategory_percentile (consistent with V2 scoring rules).
# Thresholds mirror V1: top-quartile for "high" profiles (≥75th),
# bottom-half for "low" profiles (≤50th).
# ── Product Intent Identification boost (see SearchFilters.product_type*) ────
# Scale chosen relative to lexical_query_builder.py's own boost ladder
# (name.exact_normalized=15.0, leaf_category commodity=8.0,
# category_hierarchies=0.8) — strong enough that a medium-confidence product
# type match meaningfully outranks generic term overlap, without approaching
# an exact full-name match. The category-leaf fallback is weighted half as
# much: it's a coarser, taxonomy-level signal, not a phrase-level match.
PRODUCT_TYPE_BOOST: float = 12.0
PRODUCT_TYPE_CATEGORY_BOOST: float = 6.0

_NUTRITION_PROFILE_CLAUSES: Dict[str, Dict[str, Any]] = {
    "high_protein": {"range": {"stats.protein_percentiles.subcategory_percentile":           {"gte": 75}}},
    "high_fiber":   {"range": {"stats.fiber_percentiles.subcategory_percentile":             {"gte": 75}}},
    "low_carb":     {"range": {"stats.carbs_penalty_percentiles.subcategory_percentile":     {"lte": 50}}},
    "low_sugar":    {"range": {"stats.sugar_penalty_percentiles.subcategory_percentile":     {"lte": 50}}},
    "low_sodium":   {"range": {"stats.sodium_penalty_percentiles.subcategory_percentile":    {"lte": 50}}},
    "low_fat":      {"range": {"stats.total_fat_penalty_percentiles.subcategory_percentile": {"lte": 50}}},
}


def _parse_goal_diet_ids(d: Dict[str, Any]) -> Optional[List[str]]:
    raw = d.get("goal_diet_ids") or d.get("goal_ids") or d.get("diet_ids")
    if raw is None:
        return None
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        return parts or None
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()] or None
    return None


def _normalize_legacy_nutrition_profiles(
    nutrition_profiles: Optional[List[str]],
    goal_diet_ids: Optional[List[str]],
) -> tuple[Optional[List[str]], Optional[List[str]]]:
    """
    Map legacy nutrition_profiles to canonical goal_diet_ids without double-filtering.

    Profiles with a known mapping become goal_diet_ids; unmapped profiles stay on
    nutrition_profiles for backward compatibility.
    """
    from search_v2.goal_diet.registry_loader import NUTRITION_PROFILE_TO_GOAL_DIET

    if not nutrition_profiles:
        return nutrition_profiles, goal_diet_ids

    mapped_ids = list(goal_diet_ids or [])
    legacy_profiles: List[str] = []
    for profile in nutrition_profiles:
        key = str(profile).strip().lower()
        mapped = NUTRITION_PROFILE_TO_GOAL_DIET.get(key)
        if mapped and mapped not in mapped_ids:
            mapped_ids.append(mapped)
        elif not mapped:
            legacy_profiles.append(profile)

    return (legacy_profiles or None), (mapped_ids or None)


@dataclass
class MacroFilter:
    """A single nutritional constraint: <nutrient> <operator> <value>."""
    nutrient: str    # e.g. "protein_g" — see NUTRIENT_FIELD_MAP
    operator: str    # "gte", "lte", "gt", "lt"
    value: float

    def es_field(self) -> Optional[str]:
        return NUTRIENT_FIELD_MAP.get(self.nutrient)

    def es_clause(self) -> Optional[Dict[str, Any]]:
        f = self.es_field()
        if f is None:
            return None
        return {"range": {f: {self.operator: self.value}}}


@dataclass
class SearchFilters:
    """
    Canonical typed representation of every filter dimension.

    Entry points:
      SearchFilters.from_dict(d)   — converts V1 param dict or REST API dict
      NLFilterExtractor.extract()  — produces one via NL extraction
      Direct construction          — for tests
    """
    # Category
    category_group: Optional[str] = None
    category_paths: Optional[List[str]] = None   # prefix-matched
    leaf_category: Optional[str] = None

    # Brand (multiple supported)
    brands: Optional[List[str]] = None

    # Price
    price_min: Optional[float] = None
    price_max: Optional[float] = None

    # Availability
    in_stock_only: bool = False

    # Dietary / labels
    dietary_labels: Optional[List[str]] = None
    health_claims: Optional[List[str]] = None

    # Ingredient exclusions (must_not)
    excluded_ingredients: Optional[List[str]] = None

    # Quality threshold
    min_flean_percentile: Optional[float] = None
    min_flean_score: Optional[float] = None

    # Nutritional / macro constraints
    macro_filters: Optional[List[MacroFilter]] = None

    # Personal care soft signals → produce should clauses
    skin_types: Optional[List[str]] = None
    hair_types: Optional[List[str]] = None
    skin_concerns: Optional[List[str]] = None
    hair_concerns: Optional[List[str]] = None
    avoid_ingredients_pc: Optional[List[str]] = None   # PC side-effects exclusions

    # Ingredient tag filters — exact matches against category_data.tags.ingredient_tags
    # (e.g. "no_palm_oil", "preservative_free"). Each tag is an AND requirement.
    ingredient_tags: Optional[List[str]] = None

    # Food type: "veg" excludes products with "Non Veg" in description;
    # "nonveg" requires it. Mirrors V1 description-marker approach.
    food_type: Optional[str] = None

    # Percentile-based nutrition profile filters (see _NUTRITION_PROFILE_CLAUSES)
    nutrition_profiles: Optional[List[str]] = None

    # Canonical Shop by Goals/Diets IDs — resolved to ES clauses in build_filter_clauses().
    goal_diet_ids: Optional[List[str]] = None

    # Product Intent Identification (query_processing/product_intent_extractor.py).
    # product_type          — the resolved head-noun phrase ("yogurt", "chips", ...).
    # product_type_mode     — "filter" (hard-gate retrieval to this product type,
    #                          high confidence) or "boost" (strong should-clause,
    #                          medium confidence, never excludes anything).
    # product_type_category — the term's dominant category_hierarchies leaf, used
    #                          as a secondary OR signal alongside the exact
    #                          product_type match (catches relevant products whose
    #                          name doesn't literally contain the resolved phrase).
    # None/None/None (the default) is a complete no-op — existing callers that
    # never set these fields get byte-for-byte the same clauses as before.
    product_type: Optional[str] = None
    product_type_mode: Optional[str] = None
    product_type_category: Optional[str] = None

    # Fresh Produce Identification (query_processing/canonical_produce.py).
    # Set ONLY when the cleaned query exactly matched a curated vernacular
    # produce alias or canonical name (e.g. "aam", "aloo", "bhindi",
    # "mango"). Unlike product_type (a text signal matched against an
    # indexed field), this is a hard, authoritative allowlist of real
    # catalog product ids belonging to that produce family — see
    # build_filter_clauses() below, which uses it INSTEAD OF the
    # product_type wildcard/category clause whenever it's set, and
    # hybrid_search_orchestrator.py, which never relaxes it away on zero
    # results (a genuine "no fresh X in stock" should stay empty, not fall
    # back to processed foods containing the same word). None (the default)
    # is a complete no-op.
    product_ids: Optional[List[str]] = None
    product_ids_exact: bool = False

    # Pagination / sort
    sort_by: Optional[str] = None
    offset: int = 0

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SearchFilters":
        """Construct from a V1 param dict or REST API request dict."""
        d = d or {}

        category_group = d.get("category_group")

        cat_paths: List[str] = []
        if isinstance(d.get("category_paths"), list):
            cat_paths = [str(p) for p in d["category_paths"] if p]
        if isinstance(d.get("category_path"), str) and d["category_path"]:
            if d["category_path"] not in cat_paths:
                cat_paths.append(d["category_path"])
        if isinstance(d.get("category_path_prefix"), str) and d["category_path_prefix"]:
            if d["category_path_prefix"] not in cat_paths:
                cat_paths.append(d["category_path_prefix"])
        category_paths = cat_paths or None

        leaf_category = d.get("leaf_category")

        brands_raw = d.get("brands") or ([d["brand"]] if d.get("brand") else None)
        brands: Optional[List[str]] = None
        if brands_raw:
            if isinstance(brands_raw, list):
                brands = [str(b) for b in brands_raw if b] or None
            elif isinstance(brands_raw, str):
                brands = [brands_raw]

        def _to_float(v: Any) -> Optional[float]:
            if v is None:
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        price_min = _to_float(d.get("price_min"))
        price_max = _to_float(d.get("price_max"))

        in_stock_only = bool(
            d.get("in_stock_only")
            or d.get("availability_zepto_in_stock")
            or d.get("in_stock")
        )

        dl_raw = d.get("dietary_labels") or d.get("dietary_terms")
        dietary_labels: Optional[List[str]] = None
        if dl_raw:
            if isinstance(dl_raw, list):
                dietary_labels = list(dict.fromkeys(
                    normalize_dietary_label(str(x)) for x in dl_raw if x
                )) or None
            elif isinstance(dl_raw, str):
                dietary_labels = [normalize_dietary_label(dl_raw)]

        hc_raw = d.get("health_claims")
        health_claims: Optional[List[str]] = None
        if isinstance(hc_raw, list):
            health_claims = [str(x) for x in hc_raw if x] or None
        elif isinstance(hc_raw, str) and hc_raw:
            health_claims = [hc_raw]

        excl_raw = d.get("excluded_ingredients") or d.get("exclude_ingredients")
        excluded_ingredients: Optional[List[str]] = None
        if isinstance(excl_raw, list):
            excluded_ingredients = [str(x) for x in excl_raw if x] or None
        elif isinstance(excl_raw, str) and excl_raw:
            excluded_ingredients = [excl_raw]

        min_flean = _to_float(
            d.get("min_flean_percentile") or d.get("min_quality") or d.get("quality_threshold")
        )
        min_flean_score = _to_float(d.get("min_flean_score"))
        if d.get("healthy_only") is True or str(d.get("healthy_only", "")).lower() in ("true", "1", "yes"):
            min_flean = max(float(min_flean or 0), 70.0)

        macro_filters: Optional[List[MacroFilter]] = None
        macro_raw = d.get("macro_filters")
        if isinstance(macro_raw, list):
            mfs: List[MacroFilter] = []
            for item in macro_raw:
                if isinstance(item, dict):
                    nutrient = item.get("nutrient")
                    operator = item.get("operator")
                    value = _to_float(item.get("value"))
                    if nutrient and operator and value is not None:
                        mfs.append(MacroFilter(str(nutrient), str(operator), value))
            macro_filters = mfs or None

        def _to_list(v: Any) -> Optional[List[str]]:
            if isinstance(v, list):
                return [str(x) for x in v if x] or None
            if isinstance(v, str) and v:
                return [v]
            return None

        skin_types = _to_list(d.get("skin_types") or d.get("skin_type"))
        hair_types = _to_list(d.get("hair_types") or d.get("hair_type"))
        skin_concerns = _to_list(d.get("skin_concerns"))
        hair_concerns = _to_list(d.get("hair_concerns"))
        avoid_raw = d.get("avoid_ingredients") or d.get("avoid_ingredients_pc")
        avoid_ingredients_pc = _to_list(avoid_raw)

        ingredient_tags = _to_list(d.get("ingredient_tags"))

        food_type_raw = d.get("food_type")
        food_type: Optional[str] = str(food_type_raw).strip().lower() if food_type_raw else None

        np_raw = d.get("nutrition_profiles")
        nutrition_profiles: Optional[List[str]] = None
        if isinstance(np_raw, list):
            nutrition_profiles = [str(p) for p in np_raw if p] or None
        elif isinstance(np_raw, str) and np_raw:
            nutrition_profiles = [np_raw]

        goal_diet_ids = _parse_goal_diet_ids(d)
        nutrition_profiles, goal_diet_ids = _normalize_legacy_nutrition_profiles(
            nutrition_profiles, goal_diet_ids
        )

        sort_by = d.get("sort_by") or d.get("sort")
        offset_raw = d.get("offset") or d.get("from") or 0
        try:
            offset = int(offset_raw)
        except (TypeError, ValueError):
            offset = 0

        return cls(
            category_group=category_group,
            category_paths=category_paths,
            leaf_category=leaf_category,
            brands=brands,
            price_min=price_min,
            price_max=price_max,
            in_stock_only=in_stock_only,
            dietary_labels=dietary_labels,
            health_claims=health_claims,
            excluded_ingredients=excluded_ingredients,
            min_flean_percentile=min_flean,
            min_flean_score=min_flean_score,
            macro_filters=macro_filters,
            skin_types=skin_types,
            hair_types=hair_types,
            skin_concerns=skin_concerns,
            hair_concerns=hair_concerns,
            avoid_ingredients_pc=avoid_ingredients_pc,
            ingredient_tags=ingredient_tags,
            food_type=food_type,
            nutrition_profiles=nutrition_profiles,
            goal_diet_ids=goal_diet_ids,
            sort_by=sort_by,
            offset=offset,
        )


@dataclass
class FilterClauses:
    """
    The three OpenSearch bool-query clause lists produced by filter building.

    filter_clauses   — hard filters (must match, do not affect score)
    must_not_clauses — exclusions (must NOT match)
    should_clauses   — soft boosts (affect score; used for PC compatibility signals)
    """
    filter_clauses: List[Dict[str, Any]] = field(default_factory=list)
    must_not_clauses: List[Dict[str, Any]] = field(default_factory=list)
    should_clauses: List[Dict[str, Any]] = field(default_factory=list)


def _build_in_stock_filter() -> Dict[str, Any]:
    return {
        "bool": {
            "should": [{"term": {path: True}} for path in _AVAILABILITY_IN_STOCK_PATHS],
            "minimum_should_match": 1,
        }
    }


def build_filter_clauses(sf: SearchFilters) -> FilterClauses:
    """
    Convert a SearchFilters into the three OpenSearch clause lists.

    This is the SINGLE implementation of filter-to-clause conversion.
    Every query builder in search_v2 calls this — there is no other place
    where filters are converted to ES clauses.
    """
    fc: List[Dict[str, Any]] = []
    mn: List[Dict[str, Any]] = []
    sh: List[Dict[str, Any]] = []

    # ── Hard filters (bool.filter) ────────────────────────────────────────────

    if sf.category_group:
        fc.append({"term": {"category_group": sf.category_group}})

    if sf.leaf_category:
        fc.append({"term": {"leaf_category": sf.leaf_category}})

    if sf.category_paths:
        paths = [p for p in sf.category_paths if p]
        if len(paths) == 1:
            fc.append({"prefix": {"category_paths": paths[0]}})
        elif paths:
            fc.append({
                "bool": {
                    "should": [{"prefix": {"category_paths": p}} for p in paths],
                    "minimum_should_match": 1,
                }
            })

    if sf.brands:
        normalized = [b.strip().lower() for b in sf.brands if b.strip()]
        # brand.exact_normalized was a planned keyword sub-field
        # (mapping_builder.py in the search repo defines it) that the
        # currently-running index predates and doesn't actually have —
        # found during the vision_flow.py migration (real brand filters
        # were silently matching zero documents). brand_phonetic.keyword
        # holds the same raw, un-analyzed brand string and is confirmed
        # present — see search_v2/retrieval/aggregations.py's module
        # docstring for the full investigation.
        # brand_phonetic.keyword preserves original casing (no normalizer),
        # unlike the planned-but-absent brand.exact_normalized (which used a
        # lower_keyword normalizer) — case_insensitive:true on the term
        # query achieves the same case-insensitive exact match instead.
        if len(normalized) == 1:
            fc.append({"term": {"brand_phonetic.keyword": {"value": normalized[0], "case_insensitive": True}}})
        elif normalized:
            fc.append({"bool": {"should": [
                {"term": {"brand_phonetic.keyword": {"value": b, "case_insensitive": True}}} for b in normalized
            ], "minimum_should_match": 1}})

    price_range: Dict[str, Any] = {}
    if sf.price_min is not None:
        price_range["gte"] = sf.price_min
    if sf.price_max is not None:
        price_range["lte"] = sf.price_max
    if price_range:
        fc.append({"range": {"price": price_range}})

    if sf.in_stock_only:
        fc.append(_build_in_stock_filter())

    effective_dietary_labels = _effective_dietary_labels(sf)
    if effective_dietary_labels:
        # Dietary preferences are sourced from category_data.tags.dietary_tags.
        for label in effective_dietary_labels:
            tag_key = _to_dietary_tag_key(str(label).strip())
            if not tag_key:
                continue
            fc.append(
                {
                    "bool": {
                        "should": [
                            {"term": {"category_data.tags.dietary_tags": tag_key}},
                            {"term": {"category_data.tags.dietary_tags.keyword": tag_key}},
                        ],
                        "minimum_should_match": 1,
                    }
                }
            )

    if sf.min_flean_percentile is not None:
        fc.append({"range": {
            "stats.adjusted_score_percentiles.subcategory_percentile": {
                "gte": sf.min_flean_percentile
            }
        }})

    if sf.min_flean_score is not None:
        fc.append({
            "script": {
                "script": {
                    "lang": "painless",
                    "source": """
                        if (doc.containsKey('flean_score.adjusted_score_label')
                            && !doc['flean_score.adjusted_score_label'].empty) {
                            def rawBadge = doc['flean_score.adjusted_score_label'].value;
                            double badge = -1.0;
                            if (rawBadge instanceof Number) {
                                badge = ((Number) rawBadge).doubleValue();
                            } else {
                                try {
                                    badge = Double.parseDouble(rawBadge.toString());
                                } catch (Exception ignored) {}
                            }
                            if (badge >= 0) {
                                double roundedBadge = Math.floor(badge + 0.5);
                                return roundedBadge >= params.min_badge;
                            }
                        }

                        if (doc.containsKey('flean_score.adjusted_score')
                            && !doc['flean_score.adjusted_score'].empty) {
                            def rawAdjusted = doc['flean_score.adjusted_score'].value;
                            double adjusted = -1.0;
                            if (rawAdjusted instanceof Number) {
                                adjusted = ((Number) rawAdjusted).doubleValue();
                            } else {
                                try {
                                    adjusted = Double.parseDouble(rawAdjusted.toString());
                                } catch (Exception ignored) {}
                            }
                            if (adjusted >= 0) {
                                double score10 = adjusted / 10.0;
                                double roundedScore10 = Math.floor(score10 + 0.5);
                                return roundedScore10 >= params.min_badge;
                            }
                        }

                        return false;
                    """,
                    "params": {"min_badge": float(sf.min_flean_score)},
                }
            }
        })

    if sf.macro_filters:
        for mf in sf.macro_filters:
            clause = mf.es_clause()
            if clause:
                fc.append(clause)

    if sf.ingredient_tags:
        for tag in sf.ingredient_tags:
            s = str(tag).strip()
            if s:
                fc.append({
                    "bool": {
                        "should": [
                            {"term": {"category_data.tags.ingredient_tags": s}},
                            {"term": {"category_data.tags.ingredient_tags.keyword": s}},
                        ],
                        "minimum_should_match": 1,
                    }
                })

    if sf.food_type == "nonveg":
        fc.append({"match_phrase": {"description": "Non Veg"}})

    if sf.nutrition_profiles:
        for profile in sf.nutrition_profiles:
            clause = _NUTRITION_PROFILE_CLAUSES.get(profile)
            if clause:
                fc.append(clause)

    if sf.goal_diet_ids:
        from search_v2.goal_diet.merge import merge_goal_diet_plans

        merged_plan = merge_goal_diet_plans(sf.goal_diet_ids)
        fc.extend(merged_plan.filter_clauses)
        mn.extend(merged_plan.must_not_clauses)

    # ── Product Intent Identification (see SearchFilters.product_type*) ──────
    # "filter" mode (high confidence) gates admission to the candidate pool
    # via an OR of two independent signals — either is sufficient:
    #
    # (a) SUBSTRING match against product_type. Each product's own indexed
    #     product_type comes from resolve_head_term() picking THAT product's
    #     own highest-confidence trailing n-gram, which is frequently a more
    #     specific compound than the query's own resolved term (query "chips"
    #     resolves to "chips"; a product literally named "...Hot Chips Spicy"
    #     resolves to "chips spicy", a DIFFERENT string, because that 2-gram
    #     has higher confidence for that specific name — see
    #     indexing/product_type_lexicon_builder.py's resolve_head_term()
    #     docstring). An exact term match would silently exclude every real
    #     flavor/variant/pack-size product this catalog has. A substring
    #     match recovers those without weakening precision (wildcard queries
    #     against a keyword field are an existing pattern in this codebase —
    #     see lexical_query_builder.py's _wildcard_clause()).
    #
    # (b) name CONTAINS the resolved term as a phrase, AND category leaf ==
    #     the resolved dominant_category — needed because product_type's own
    #     resolution window is bounded (MAX_NGRAM_WORDS=3 trailing tokens —
    #     see product_type_lexicon_builder.py). A name with 4+ words trailing
    #     the true head noun ("Granola Bar Assorted Nutrition Bar" — 3 words
    #     follow "granola bar") never gets a chance to resolve to it at all,
    #     regardless of confidence; product_type ends up something unrelated
    #     ("nutrition bar") even though the product plainly IS a granola bar.
    #     (b) recovers these using fields ALREADY indexed (name,
    #     category_hierarchies) — no reindex, no lexicon regeneration.
    #     Requiring BOTH name-phrase AND category-leaf (not just category
    #     leaf alone) is deliberate and load-bearing: a category-leaf-only
    #     fallback was tried and reverted because it let CATALOG MISTAGGING
    #     through (a bread/jerky/rice-cake product wrongly tagged under a
    #     "chips_and_crisps" leaf, with no relation to "chips" in its name at
    #     all, matched on category alone). AND-ing a genuine name-phrase hit
    #     back in closes that hole — verified against the real catalog that
    #     none of those previously-wrong documents contain the query phrase
    #     in their name, so they still can't get in through (b) either.
    #
    # "boost" mode (medium confidence) is unaffected — the category-leaf
    # fallback there only ever adds relevance SCORE via a should-clause, never
    # controls admission, so it doesn't have either failure mode.
    # ── Fresh Produce Identification (see SearchFilters.product_ids) ─────────
    # A hard, authoritative id allowlist — takes priority over and REPLACES
    # the product_type wildcard/category clause below entirely (does not AND
    # with it): a genuine family member's own indexed product_type field can
    # be a vernacular word the wildcard wouldn't match (e.g. "Onion (Pyaz)"
    # indexes as product_type="pyaz", not "onion") — ANDing the two would
    # wrongly exclude real family members that the id allowlist already
    # correctly includes.
    if sf.product_ids:
        fc.append({"terms": {"id": list(sf.product_ids)}})
    elif sf.product_type and sf.product_type_mode in ("filter", "boost"):
        if sf.product_type_mode == "filter":
            filter_should: List[Dict[str, Any]] = [
                {"wildcard": {"product_type": {"value": f"*{sf.product_type}*", "case_insensitive": True}}},
            ]
            if sf.product_type_category:
                filter_should.append({
                    "bool": {
                        "must": [
                            {"match_phrase": {"name": sf.product_type}},
                            {
                                "nested": {
                                    "path": "category_hierarchies",
                                    "query": {"term": {"category_hierarchies.segments": sf.product_type_category}},
                                    "score_mode": "max",
                                }
                            },
                        ]
                    }
                })
            fc.append({"bool": {"should": filter_should, "minimum_should_match": 1}})
        else:
            boosted_should: List[Dict[str, Any]] = [
                {"term": {"product_type": {"value": sf.product_type, "boost": PRODUCT_TYPE_BOOST}}}
            ]
            if sf.product_type_category:
                boosted_should.append({
                    "nested": {
                        "path": "category_hierarchies",
                        "query": {
                            "term": {
                                "category_hierarchies.segments": {
                                    "value": sf.product_type_category,
                                    "boost": PRODUCT_TYPE_CATEGORY_BOOST,
                                }
                            }
                        },
                        "score_mode": "max",
                    }
                })
            sh.append({"bool": {"should": boosted_should, "minimum_should_match": 1}})

    # ── Must-not clauses ──────────────────────────────────────────────────────

    if sf.food_type == "veg":
        mn.append({"match_phrase": {"description": "Non Veg"}})

    if sf.excluded_ingredients:
        for ingredient in sf.excluded_ingredients:
            s = str(ingredient).strip()
            if s:
                mn.append({"match": {"ingredients.raw_text": s}})

    if sf.avoid_ingredients_pc:
        for ingredient in sf.avoid_ingredients_pc:
            s = str(ingredient).strip()
            if not s:
                continue
            mn.append({
                "nested": {
                    "path": "side_effects",
                    "query": {
                        "bool": {
                            "must": [
                                {"match": {"side_effects.effect_name": s}},
                                {"range": {"side_effects.severity_score": {"gte": 0.3}}},
                            ]
                        }
                    }
                }
            })

    # ── Should clauses (PC soft signals) ─────────────────────────────────────

    compat_types: List[str] = []
    for t in (sf.skin_types or []) + (sf.hair_types or []):
        s = str(t).strip().lower()
        if s and s not in compat_types:
            compat_types.append(s)

    for st in compat_types[:4]:
        sh.append({
            "nested": {
                "path": "skin_compatibility",
                "query": {
                    "bool": {
                        "must": [
                            {"term": {"skin_compatibility.skin_type": st}},
                            {"range": {"skin_compatibility.sentiment_score": {"gte": 0.6}}},
                            {"range": {"skin_compatibility.confidence_score": {"gte": 0.3}}},
                        ]
                    }
                },
                "score_mode": "max",
                "boost": 5.0,
            }
        })

    concerns: List[str] = []
    for t in (sf.skin_concerns or []) + (sf.hair_concerns or []):
        s = str(t).strip().lower()
        if s and s not in concerns:
            concerns.append(s)

    if concerns:
        sh.append({
            "nested": {
                "path": "efficacy",
                "query": {
                    "bool": {
                        "must": [
                            {
                                "multi_match": {
                                    "query": " ".join(concerns),
                                    "fields": ["efficacy.aspect_name^3.0"],
                                    "fuzziness": "AUTO",
                                    "type": "best_fields",
                                }
                            },
                            {"range": {"efficacy.sentiment_score": {"gte": 0.7}}},
                        ]
                    }
                },
                "score_mode": "max",
                "boost": 3.0,
            }
        })

    if sf.health_claims:
        for claim in sf.health_claims[:4]:
            s = str(claim).strip()
            if s:
                sh.append({"match": {"package_claims.health_claims": {"query": s, "boost": 2.0}}})

    return FilterClauses(
        filter_clauses=fc,
        must_not_clauses=mn,
        should_clauses=sh,
    )


def merge_filters(base: SearchFilters, overlay: SearchFilters) -> SearchFilters:
    """
    Merge two SearchFilters. overlay scalar values take precedence over base.
    List fields are deduplicated-merged (base + overlay).
    """
    def _merge_list(a: Optional[List], b: Optional[List]) -> Optional[List]:
        if not a and not b:
            return None
        merged: List = list(a or [])
        for item in (b or []):
            if item not in merged:
                merged.append(item)
        return merged or None

    return SearchFilters(
        category_group=overlay.category_group or base.category_group,
        category_paths=_merge_list(base.category_paths, overlay.category_paths),
        leaf_category=overlay.leaf_category or base.leaf_category,
        brands=_merge_list(base.brands, overlay.brands),
        price_min=overlay.price_min if overlay.price_min is not None else base.price_min,
        price_max=overlay.price_max if overlay.price_max is not None else base.price_max,
        in_stock_only=base.in_stock_only or overlay.in_stock_only,
        dietary_labels=_merge_list(base.dietary_labels, overlay.dietary_labels),
        health_claims=_merge_list(base.health_claims, overlay.health_claims),
        excluded_ingredients=_merge_list(base.excluded_ingredients, overlay.excluded_ingredients),
        min_flean_percentile=(
            overlay.min_flean_percentile if overlay.min_flean_percentile is not None
            else base.min_flean_percentile
        ),
        min_flean_score=(
            overlay.min_flean_score if overlay.min_flean_score is not None
            else base.min_flean_score
        ),
        macro_filters=_merge_list(base.macro_filters, overlay.macro_filters),
        skin_types=_merge_list(base.skin_types, overlay.skin_types),
        hair_types=_merge_list(base.hair_types, overlay.hair_types),
        skin_concerns=_merge_list(base.skin_concerns, overlay.skin_concerns),
        hair_concerns=_merge_list(base.hair_concerns, overlay.hair_concerns),
        avoid_ingredients_pc=_merge_list(base.avoid_ingredients_pc, overlay.avoid_ingredients_pc),
        ingredient_tags=_merge_list(base.ingredient_tags, overlay.ingredient_tags),
        food_type=overlay.food_type or base.food_type,
        nutrition_profiles=_merge_list(base.nutrition_profiles, overlay.nutrition_profiles),
        goal_diet_ids=_merge_list(base.goal_diet_ids, overlay.goal_diet_ids),
        product_type=overlay.product_type or base.product_type,
        product_type_mode=overlay.product_type_mode or base.product_type_mode,
        product_type_category=overlay.product_type_category or base.product_type_category,
        product_ids=overlay.product_ids or base.product_ids,
        product_ids_exact=overlay.product_ids_exact or base.product_ids_exact,
        sort_by=overlay.sort_by or base.sort_by,
        offset=overlay.offset if overlay.offset else base.offset,
    )
