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
from typing import List, Optional, Tuple

from search_v2.retrieval.filters import (
    DIETARY_LABEL_ALIASES,
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

# ── Macro constraint patterns ─────────────────────────────────────────────────
# (compiled_pattern, nutrient_key, operator)
_MACRO_PATTERNS: List[Tuple[re.Pattern, str, str]] = [
    (re.compile(r"\b(?:high|rich\s+in|good\s+source\s+of)\s+protein\b", re.IGNORECASE), "protein_g", "gte"),
    (re.compile(r"\b(?:low|less|no|zero|without|sugar[\-\s]free)\s+sugar\b", re.IGNORECASE), "sugar_g", "lte"),
    (re.compile(r"\b(?:low|less|no|zero)\s+fat\b", re.IGNORECASE), "fat_g", "lte"),
    (re.compile(r"\b(?:low|less|no|zero)\s+(?:calorie[s]?|cal[s]?)\b", re.IGNORECASE), "energy_kcal", "lte"),
    (re.compile(r"\b(?:high|rich\s+in|good\s+source\s+of)\s+(?:fiber|fibre)\b", re.IGNORECASE), "fiber_g", "gte"),
    (re.compile(r"\b(?:low|less|no|zero)\s+(?:sodium|salt)\b", re.IGNORECASE), "sodium_mg", "lte"),
]

# ── Ingredient exclusion patterns ─────────────────────────────────────────────
_EXCL_WITHOUT = re.compile(r"\b(?:without|no\b|free\s+from)\s+([\w][\w\s]{2,29})", re.IGNORECASE)

# These labels are already handled by dietary patterns — don't double-capture them
_EXCL_DIETARY_STOPWORDS = {
    label.lower()
    for label in DIETARY_LABEL_ALIASES
}
_EXCL_KNOWN_ADJECTIVES = {"artificial", "added", "preservative", "preservatives"}


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

        # ── 1. Price (BETWEEN first — most specific) ─────────────────────────
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

        # ── 2. Macro constraints (before dietary — macro patterns are more specific) ─
        s = self._settings
        threshold_map = {
            ("protein_g", "gte"): getattr(s, "MACRO_HIGH_PROTEIN_G", 15.0),
            ("sugar_g", "lte"): getattr(s, "MACRO_LOW_SUGAR_G", 5.0),
            ("fat_g", "lte"): getattr(s, "MACRO_LOW_FAT_G", 3.0),
            ("energy_kcal", "lte"): getattr(s, "MACRO_LOW_CAL_KCAL", 100.0),
            ("fiber_g", "gte"): getattr(s, "MACRO_HIGH_FIBER_G", 6.0),
            ("sodium_mg", "lte"): getattr(s, "MACRO_LOW_SODIUM_MG", 140.0),
        }

        for pattern, nutrient, operator in _MACRO_PATTERNS:
            m = pattern.search(remaining)
            if m:
                threshold = threshold_map.get((nutrient, operator))
                if threshold is not None:
                    macro_filters.append(MacroFilter(nutrient=nutrient, operator=operator, value=threshold))
                    remaining = remaining[:m.start()] + remaining[m.end():]
                    signals.append(f"macro={nutrient}{operator}{threshold}")

        # ── 3. Dietary labels (after macros — avoids double-capturing "high protein") ─
        for pattern, canonical in _DIETARY_PATTERNS:
            m = pattern.search(remaining)
            if m:
                if canonical not in dietary_labels:
                    dietary_labels.append(canonical)
                    remaining = remaining[:m.start()] + remaining[m.end():]
                    signals.append(f"dietary={canonical}")

        # ── 4. Ingredient exclusions ──────────────────────────────────────────
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

        # ── 5. Clean remaining query text ─────────────────────────────────────
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
            ),
            signals_found=signals,
        )
