"""
search_v2/query_processing/nl_filter_extractor.py
────────────────────────────────────────────────────
Deterministic regex-based natural-language filter extraction.

No LLMs. No external services. Pure pattern matching.

Converts a raw user query string into:
  - A clean query text (filter keywords stripped)
  - A SearchFilters object capturing the extracted constraints

Examples:
  "apple under ₹100"         → query="apple",        price_max=100
  "whey protein under 2000"  → query="whey protein", price_max=2000
  "high protein bread"       → query="bread",         macro=protein>=threshold
  "low sugar biscuits"       → query="biscuits",      macro=sugar<=threshold
  "gluten free oats"         → query="oats",          dietary=["GLUTEN FREE"]
  "vegan protein powder"     → query="protein powder",dietary=["VEGAN"]
  "chips without palm oil"   → query="chips",         excluded=["palm oil"]
  "rice between 100 and 300" → query="rice",          price_min=100,price_max=300
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from search_v2.retrieval.filters import (
    CANONICAL_NUTRIENT_KEYS,
    DIETARY_LABEL_ALIASES,
    NUTRIENT_FIELD_MAP,
    MacroFilter,
    SearchFilters,
)

# ── Price patterns ────────────────────────────────────────────────────────────
_CURRENCY = r"(?:₹|rs\.?\s*|inr\s*)"
_NUM = r"(\d[\d,]*(?:\.\d+)?)"

_PRICE_BETWEEN = re.compile(
    rf"(?:between|from)\s*{_CURRENCY}?{_NUM}\s*(?:and|to|-)\s*{_CURRENCY}?{_NUM}",
    re.IGNORECASE,
)
_PRICE_UNDER = re.compile(
    rf"(?:under|below|upto|up\s+to|<|max(?:imum)?|at\s+most)\s*{_CURRENCY}?{_NUM}",
    re.IGNORECASE,
)
_PRICE_ABOVE = re.compile(
    rf"(?:above|over|>|min(?:imum)?|at\s+least)\s*{_CURRENCY}?{_NUM}",
    re.IGNORECASE,
)
_PRICE_STANDALONE = re.compile(rf"{_CURRENCY}{_NUM}", re.IGNORECASE)
_PRICE_QUALITATIVE = re.compile(r"\b(?:cheap(?:est)?|budget|affordable|inexpensive|low[\s-]?cost)\b", re.IGNORECASE)


def _parse_num(s: str) -> float:
    return float(s.replace(",", ""))


# ── Dietary label patterns ─────────────────────────────────────────────────────
# Sorted longest-first to prevent partial shadowing by shorter phrases.
_DIETARY_PATTERNS: List[Tuple[re.Pattern, str]] = sorted(
    [
        (re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE), canonical)
        for phrase, canonical in DIETARY_LABEL_ALIASES.items()
    ],
    key=lambda x: -len(x[0].pattern),
)

# ── Nutrient vocabulary (schema-driven, NOT product/catalog-specific) ────────
# Derived from retrieval/filters.py's NUTRIENT_FIELD_MAP — the SINGLE source
# of truth for every nutrition dimension the schema exposes — rather than
# maintaining a second, parallel list here. Adding a new nutrient (or a new
# surface synonym for an existing one) to NUTRIENT_FIELD_MAP is therefore
# enough to make it understood in natural-language queries too; nothing in
# this file needs to change. This is schema/grammar vocabulary (like
# _CURRENCY/_PRICE_* above), not a per-product word list.
#
# Maps every alias word -> its CANONICAL nutrient key (protein_g, sugar_g,
# ...) by finding which canonical key shares the same ES field path.
_FIELD_PATH_TO_CANONICAL: Dict[str, str] = {
    NUTRIENT_FIELD_MAP[key]: key for key in CANONICAL_NUTRIENT_KEYS
}
_NUTRIENT_ALIASES: Dict[str, str] = {
    alias: _FIELD_PATH_TO_CANONICAL[field_path]
    for alias, field_path in NUTRIENT_FIELD_MAP.items()
    if field_path in _FIELD_PATH_TO_CANONICAL
}
# Nutrients whose users conventionally give in grams even though the stored
# field is in a different unit (sodium_mg) — the numeric extractor below
# converts g -> mg for these so "sodium under 0.5g" still resolves correctly
# against the mg-native field. Empty by default effect for every other
# nutrient (already gram/kcal-native, no conversion needed).
_GRAMS_TO_STORED_UNIT_MULTIPLIER: Dict[str, float] = {"sodium_mg": 1000.0}

_NUTRIENT_WORD = r"(?:" + "|".join(
    re.escape(w) for w in sorted(_NUTRIENT_ALIASES, key=len, reverse=True)
) + r")"

# ── Numeric nutrient constraints — "under 200 calories", "protein under 20g" ──
# Comparison-operator vocabulary is grammatical/functional, not catalog-
# specific (same category as the price operator words above) — matches ANY
# nutrient dimension generically via _NUTRIENT_WORD, no per-nutrient phrasing.
_NUM_RAW = r"\d+(?:\.\d+)?"
_NUTRIENT_UNIT = r"(?:g|grams?|mg|milligrams?|kcal|cals?|calories?)?"
_NUTRIENT_UNIT_REQUIRED = r"(?:g|grams?|mg|milligrams?|kcal|cals?|calories?)"
_OP_GTE = r"(?:more\s+than|greater\s+than|at\s+least|minimum\s+of|minimum|min|over|above)"
_OP_LTE = r"(?:less\s+than|no\s+more\s+than|at\s+most|maximum\s+of|maximum|max|under|below|up\s*to)"

_NUMERIC_NUTRIENT_PATTERNS: List[Tuple[re.Pattern, str]] = []
for _op, _op_key in ((_OP_GTE, "gte"), (_OP_LTE, "lte")):
    _NUMERIC_NUTRIENT_PATTERNS.append((
        re.compile(
            rf"\b{_op}\b\s*(?P<num>{_NUM_RAW})\s*(?P<unit>{_NUTRIENT_UNIT})\s*(?P<nutrient>{_NUTRIENT_WORD})\b",
            re.IGNORECASE,
        ),
        _op_key,
    ))
    _NUMERIC_NUTRIENT_PATTERNS.append((
        re.compile(
            rf"\b(?P<nutrient>{_NUTRIENT_WORD})\b\s*{_op}\b\s*(?P<num>{_NUM_RAW})\s*(?P<unit>{_NUTRIENT_UNIT_REQUIRED})\b",
            re.IGNORECASE,
        ),
        _op_key,
    ))

# ── Qualitative macro constraint patterns ("high protein", "low sugar") ──────
# Data-driven over the SAME nutrient vocabulary above — one (direction,
# threshold-setting) pair per nutrient the business has defined a threshold
# for (see config/settings.py). Adding a new nutrient's qualitative threshold
# is a one-line settings addition, not new regex/parsing code.
# (nutrient_key, operator, settings_attr, default_threshold)
_QUALITATIVE_MACRO_DIRECTIONS: List[Tuple[str, str, str, float]] = [
    ("protein_g", "gte", "MACRO_HIGH_PROTEIN_G", 15.0),
    ("sugar_g", "lte", "MACRO_LOW_SUGAR_G", 5.0),
    ("fat_g", "lte", "MACRO_LOW_FAT_G", 3.0),
    ("energy_kcal", "lte", "MACRO_LOW_CAL_KCAL", 100.0),
    ("fiber_g", "gte", "MACRO_HIGH_FIBER_G", 6.0),
    ("sodium_mg", "lte", "MACRO_LOW_SODIUM_MG", 140.0),
    ("carbs_g", "lte", "MACRO_LOW_CARBS_G", 15.0),
]
_QUALITATIVE_HIGH = r"(?:high|rich\s+in|good\s+source\s+of)"
_QUALITATIVE_LOW = r"(?:low|less|no|zero|without|free\s+from)"

_MACRO_PATTERNS: List[Tuple[re.Pattern, str, str]] = []
for _nutrient_key, _operator, _setting_attr, _default in _QUALITATIVE_MACRO_DIRECTIONS:
    _word = r"(?:" + "|".join(
        re.escape(w) for w, key in _NUTRIENT_ALIASES.items() if key == _nutrient_key
    ) + r")"
    _qual = _QUALITATIVE_HIGH if _operator == "gte" else _QUALITATIVE_LOW
    _MACRO_PATTERNS.append((
        re.compile(rf"\b{_qual}\s+{_word}\b", re.IGNORECASE), _nutrient_key, _operator,
    ))

# ── Ingredient exclusion patterns ─────────────────────────────────────────────
_EXCL_WITHOUT = re.compile(r"\b(?:without|no\b|free\s+from)\s+([\w][\w\s]{2,29})", re.IGNORECASE)

# These labels are already handled by dietary patterns — don't double-capture them
_EXCL_DIETARY_STOPWORDS = {
    label.lower()
    for label in DIETARY_LABEL_ALIASES
}
_EXCL_KNOWN_ADJECTIVES = {"artificial", "added", "preservative", "preservatives"}

_LIFESTYLE_INTENT_PATTERNS: List[Tuple[re.Pattern, List[str]]] = [
    (re.compile(r"\b(?:gym|workout|work[\s-]?out|post[\s-]?workout|muscle|bodybuilding)\b", re.IGNORECASE), ["high_protein"]),
    (re.compile(r"\b(?:weight\s*loss|slimming|fat\s*loss)\b", re.IGNORECASE), ["low_carb", "low_sugar"]),
]


@dataclass
class NLExtractionResult:
    """Output of NLFilterExtractor.extract()."""
    clean_query: str
    filters: SearchFilters = field(default_factory=SearchFilters)
    signals_found: List[str] = field(default_factory=list)


class NLFilterExtractor:
    """
    Extracts structured filters from natural-language query text.

    Usage:
        extractor = NLFilterExtractor()
        result = extractor.extract("high protein bread below ₹200")
        # result.clean_query  → "bread"
        # result.filters.macro_filters  → [MacroFilter("protein_g", "gte", 15.0)]
        # result.filters.price_max      → 200.0
    """

    def __init__(self, settings=None):
        from search_v2.config.settings import SETTINGS as _SETTINGS
        self._settings = settings or _SETTINGS

    def extract(self, raw_query: str) -> NLExtractionResult:
        """Extract filters and return clean_query + SearchFilters."""
        text = raw_query.strip()
        remaining = text
        signals: List[str] = []

        price_min: Optional[float] = None
        price_max: Optional[float] = None
        dietary_labels: List[str] = []
        macro_filters: List[MacroFilter] = []
        excluded_ingredients: List[str] = []

        # ── 1. Numeric nutrient constraints — MUST run before price. Patterns
        #     like "under 200 calories" share operator words ("under") with
        #     the price patterns below, but are disambiguated by requiring a
        #     nutrient word immediately adjacent to the number, which the
        #     price patterns don't check — so running this first means a
        #     genuine nutrient query never gets misread as a price filter.
        seen_nutrients: set = set()
        for _pattern, _operator in _NUMERIC_NUTRIENT_PATTERNS:
            m = _pattern.search(remaining)
            if not m:
                continue
            nutrient_key = _NUTRIENT_ALIASES.get(m.group("nutrient").lower())
            if nutrient_key is None or nutrient_key in seen_nutrients:
                continue
            value = float(m.group("num"))
            unit = (m.group("unit") or "").lower()
            if unit in ("g", "gram", "grams"):
                value *= _GRAMS_TO_STORED_UNIT_MULTIPLIER.get(nutrient_key, 1.0)
            macro_filters.append(MacroFilter(nutrient=nutrient_key, operator=_operator, value=value))
            seen_nutrients.add(nutrient_key)
            remaining = remaining[:m.start()] + remaining[m.end():]
            signals.append(f"macro={nutrient_key}{_operator}{value}")

        # ── 2. Price (BETWEEN first — most specific) ─────────────────────────
        m = _PRICE_BETWEEN.search(remaining)
        if m:
            price_min = _parse_num(m.group(1))
            price_max = _parse_num(m.group(2))
            remaining = remaining[:m.start()] + remaining[m.end():]
            signals.append(f"price_range={price_min}-{price_max}")

        if price_max is None:
            m = _PRICE_UNDER.search(remaining)
            if m:
                price_max = _parse_num(m.group(1))
                remaining = remaining[:m.start()] + remaining[m.end():]
                signals.append(f"price_max={price_max}")

        if price_min is None:
            m = _PRICE_ABOVE.search(remaining)
            if m:
                price_min = _parse_num(m.group(1))
                remaining = remaining[:m.start()] + remaining[m.end():]
                signals.append(f"price_min={price_min}")

        if price_min is None and price_max is None:
            m = _PRICE_STANDALONE.search(remaining)
            if m:
                price_max = _parse_num(m.group(1))
                remaining = remaining[:m.start()] + remaining[m.end():]
                signals.append(f"price_max_currency={price_max}")

        sort_by: Optional[str] = None
        if price_min is None and price_max is None:
            m = _PRICE_QUALITATIVE.search(remaining)
            if m:
                sort_by = "price_asc"
                remaining = remaining[:m.start()] + remaining[m.end():]
                signals.append("sort=price_asc")

        # ── 3. Qualitative macro constraints ("high protein", "low sugar") ───
        # Threshold per (nutrient, direction) sourced from settings — same
        # table drives both this loop and _MACRO_PATTERNS' construction above.
        s = self._settings
        threshold_map = {
            (nutrient_key, operator): getattr(s, setting_attr, default)
            for nutrient_key, operator, setting_attr, default in _QUALITATIVE_MACRO_DIRECTIONS
        }

        for pattern, nutrient, operator in _MACRO_PATTERNS:
            if nutrient in seen_nutrients:
                continue  # already captured with an explicit number in step 1
            m = pattern.search(remaining)
            if m:
                threshold = threshold_map.get((nutrient, operator))
                if threshold is not None:
                    macro_filters.append(MacroFilter(nutrient=nutrient, operator=operator, value=threshold))
                    seen_nutrients.add(nutrient)
                    remaining = remaining[:m.start()] + remaining[m.end():]
                    signals.append(f"macro={nutrient}{operator}{threshold}")

        # ── 3b. Lifestyle intent phrases ("gym snacks", "weight loss snacks") ──
        nutrition_profiles: List[str] = []
        for pattern, profiles in _LIFESTYLE_INTENT_PATTERNS:
            m = pattern.search(remaining)
            if m:
                for profile in profiles:
                    if profile not in nutrition_profiles:
                        nutrition_profiles.append(profile)
                remaining = remaining[:m.start()] + remaining[m.end():]
                signals.append(f"nutrition_profile={','.join(profiles)}")

        # ── 4. Dietary labels (after macros — avoids double-capturing "high protein") ─
        for pattern, canonical in _DIETARY_PATTERNS:
            m = pattern.search(remaining)
            if m:
                if canonical not in dietary_labels:
                    dietary_labels.append(canonical)
                    remaining = remaining[:m.start()] + remaining[m.end():]
                    signals.append(f"dietary={canonical}")

        # ── 5. Ingredient exclusions ──────────────────────────────────────────
        for m in list(_EXCL_WITHOUT.finditer(remaining)):
            raw_ingredient = m.group(1).strip()
            # Remove trailing 's' for mild singularization (preservatives → preservative)
            ingredient = raw_ingredient.rstrip("s") if raw_ingredient.endswith("s") and len(raw_ingredient) > 4 else raw_ingredient
            ing_lower = ingredient.lower().strip()
            # Skip if already captured as a dietary label
            if (
                ing_lower in _EXCL_DIETARY_STOPWORDS
                or ing_lower in _EXCL_KNOWN_ADJECTIVES
                or len(ingredient) < 3
                or ingredient in excluded_ingredients
            ):
                continue
            excluded_ingredients.append(ingredient)
            remaining = remaining[:m.start()] + remaining[m.end():]
            signals.append(f"exclude={ingredient}")

        # ── 6. Clean remaining query text ─────────────────────────────────────
        clean = " ".join(remaining.split()).strip()
        clean = re.sub(r"^(?:and|or|,)+\s*|\s*(?:and|or|,)+$", "", clean).strip()
        if not clean:
            clean = raw_query.strip()

        return NLExtractionResult(
            clean_query=clean,
            filters=SearchFilters(
                price_min=price_min,
                price_max=price_max,
                dietary_labels=dietary_labels or None,
                macro_filters=macro_filters or None,
                excluded_ingredients=excluded_ingredients or None,
                sort_by=sort_by,
                nutrition_profiles=nutrition_profiles or None,
            ),
            signals_found=signals,
        )
