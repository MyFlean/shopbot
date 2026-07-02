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
    # Aliases
    "calories": "category_data.nutritional.nutri_breakdown_updated.energy_kcal",
    "protein": "category_data.nutritional.nutri_breakdown_updated.protein_g",
    "sugar": "category_data.nutritional.nutri_breakdown_updated.sugar_g",
    "fat": "category_data.nutritional.nutri_breakdown_updated.fat_g",
    "fiber": "category_data.nutritional.nutri_breakdown_updated.fiber_g",
    "fibre": "category_data.nutritional.nutri_breakdown_updated.fiber_g",
    "sodium": "category_data.nutritional.nutri_breakdown_updated.sodium_mg",
}

# ── Dietary label normalization ────────────────────────────────────────────────
DIETARY_LABEL_ALIASES: Dict[str, str] = {
    "glutenfree": "GLUTEN FREE",
    "gluten-free": "GLUTEN FREE",
    "gluten free": "GLUTEN FREE",
    "vegan": "VEGAN",
    "vegetarian": "VEGETARIAN",
    "palmoilfree": "PALM OIL FREE",
    "palm oil free": "PALM OIL FREE",
    "palm-oil-free": "PALM OIL FREE",
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


def normalize_dietary_label(label: str) -> str:
    """Normalize a dietary label to its canonical uppercase form."""
    low = label.strip().lower()
    return DIETARY_LABEL_ALIASES.get(low, label.strip().upper())


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

    # Nutritional / macro constraints
    macro_filters: Optional[List[MacroFilter]] = None

    # Personal care soft signals → produce should clauses
    skin_types: Optional[List[str]] = None
    hair_types: Optional[List[str]] = None
    skin_concerns: Optional[List[str]] = None
    hair_concerns: Optional[List[str]] = None
    avoid_ingredients_pc: Optional[List[str]] = None   # PC side-effects exclusions

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
                dietary_labels = [normalize_dietary_label(x) for x in dl_raw if x] or None
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
            macro_filters=macro_filters,
            skin_types=skin_types,
            hair_types=hair_types,
            skin_concerns=skin_concerns,
            hair_concerns=hair_concerns,
            avoid_ingredients_pc=avoid_ingredients_pc,
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
        if len(normalized) == 1:
            fc.append({"term": {"brand.exact_normalized": normalized[0]}})
        elif normalized:
            fc.append({"terms": {"brand.exact_normalized": normalized}})

    price_range: Dict[str, Any] = {}
    if sf.price_min is not None:
        price_range["gte"] = sf.price_min
    if sf.price_max is not None:
        price_range["lte"] = sf.price_max
    if price_range:
        fc.append({"range": {"price": price_range}})

    if sf.in_stock_only:
        fc.append(_build_in_stock_filter())

    if sf.dietary_labels:
        fc.append({"terms": {"package_claims.dietary_labels": sf.dietary_labels}})

    if sf.min_flean_percentile is not None:
        fc.append({"range": {
            "stats.adjusted_score_percentiles.subcategory_percentile": {
                "gte": sf.min_flean_percentile
            }
        }})

    if sf.macro_filters:
        for mf in sf.macro_filters:
            clause = mf.es_clause()
            if clause:
                fc.append(clause)

    # ── Must-not clauses ──────────────────────────────────────────────────────

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
        macro_filters=_merge_list(base.macro_filters, overlay.macro_filters),
        skin_types=_merge_list(base.skin_types, overlay.skin_types),
        hair_types=_merge_list(base.hair_types, overlay.hair_types),
        skin_concerns=_merge_list(base.skin_concerns, overlay.skin_concerns),
        hair_concerns=_merge_list(base.hair_concerns, overlay.hair_concerns),
        avoid_ingredients_pc=_merge_list(base.avoid_ingredients_pc, overlay.avoid_ingredients_pc),
        sort_by=overlay.sort_by or base.sort_by,
        offset=overlay.offset if overlay.offset else base.offset,
    )
