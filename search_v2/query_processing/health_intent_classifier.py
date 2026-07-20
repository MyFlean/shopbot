from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Tuple

from search_v2.query_processing.typo_correction import damerau_levenshtein, _auto_fuzziness_budget


@dataclass(frozen=True)
class HealthIntentDefinition:
    category: str
    triggers: Tuple[str, ...]
    primary_preferences: Tuple[str, ...] = ()
    secondary_preferences: Tuple[str, ...] = ()


HEALTH_INTENT_REGISTRY: Tuple[HealthIntentDefinition, ...] = (
    HealthIntentDefinition(
        category="fitness",
        triggers=(
            "gym", "muscle gain", "muscle building", "muscle mass",
            "weight gain", "gain weight", "mass gain", "bulking",
            "bodybuilding", "high protein",
            "pre workout", "pre-workout", "post workout", "post-workout",
            "recovery", "endurance",
        ),
        primary_preferences=("high_protein",),
        secondary_preferences=("low_sugar",),
    ),
    HealthIntentDefinition(
        category="weight",
        triggers=("weight loss", "fat loss", "cutting", "dieting", "low calorie"),
        primary_preferences=("low_calorie",),
        secondary_preferences=("high_fiber",),
    ),
    HealthIntentDefinition(
        category="diabetes",
        triggers=("diabetes", "diabetic", "sugar patient", "sugar control"),
        primary_preferences=("low_sugar", "no_added_sugar"),
        secondary_preferences=("high_fiber",),
    ),
    HealthIntentDefinition(
        category="heart",
        triggers=("heart healthy", "cardiac", "cholesterol", "heart care"),
        primary_preferences=("low_saturated_fat", "low_sodium", "low_trans_fat"),
        secondary_preferences=("high_fiber", "high_potassium"),
    ),
    HealthIntentDefinition(
        category="blood_pressure",
        triggers=("hypertension", "bp", "high bp"),
        primary_preferences=("low_sodium",),
        secondary_preferences=("high_potassium",),
    ),
    HealthIntentDefinition(
        category="digestive",
        triggers=("digestion", "gut health", "constipation"),
        primary_preferences=("high_fiber",),
    ),
    HealthIntentDefinition(
        category="keto",
        triggers=("keto", "ketogenic", "low carb"),
        primary_preferences=("low_carb",),
    ),
    HealthIntentDefinition(
        category="general",
        triggers=("healthy eating", "eating healthy", "wellness", "energy", "immunity"),
        primary_preferences=(),
    ),
)

_PATTERNS: Dict[str, re.Pattern] = {
    trigger: re.compile(r"\b" + re.escape(trigger) + r"\b")
    for definition in HEALTH_INTENT_REGISTRY
    for trigger in definition.triggers
}

# (definition, [(trigger, trigger_tokens), ...]) — precomputed once for the fuzzy
# fallback below, mirroring HEALTH_INTENT_REGISTRY's own per-definition grouping.
_TRIGGER_TOKENS: List[Tuple[HealthIntentDefinition, List[Tuple[str, Tuple[str, ...]]]]] = [
    (definition, [(trigger, tuple(trigger.split())) for trigger in definition.triggers])
    for definition in HEALTH_INTENT_REGISTRY
]


@dataclass(frozen=True)
class HealthIntentResult:
    detected: bool = False
    categories: Tuple[str, ...] = ()
    matched_phrases: Tuple[str, ...] = ()
    primary_preferences: Tuple[str, ...] = ()
    secondary_preferences: Tuple[str, ...] = ()


def _fuzzy_window_match(query_tokens: List[str], trigger_tokens: Tuple[str, ...]) -> bool:
    n, m = len(query_tokens), len(trigger_tokens)
    if m > n:
        return False
    for start in range(n - m + 1):
        matched = True
        for i, tword in enumerate(trigger_tokens):
            qword = query_tokens[start + i]
            if qword == tword:
                continue
            budget = _auto_fuzziness_budget(len(tword))
            if (
                budget == 0
                or qword[0] != tword[0]
                or abs(len(qword) - len(tword)) > budget
                or damerau_levenshtein(qword, tword, max_distance=budget) > budget
            ):
                matched = False
                break
        if matched:
            return True
    return False


def classify_health_intent(raw_query: str) -> HealthIntentResult:
    text = (raw_query or "").strip().lower()
    if not text:
        return HealthIntentResult()

    matched_definitions: List[HealthIntentDefinition] = []
    matched_phrases: List[str] = []
    for definition in HEALTH_INTENT_REGISTRY:
        for trigger in definition.triggers:
            if _PATTERNS[trigger].search(text):
                matched_definitions.append(definition)
                matched_phrases.append(trigger)
                break

    if not matched_definitions:
        # Lightweight fallback, only attempted when exact trigger matching found
        # nothing at all — fuzzy-matches query tokens against this small, fixed
        # trigger registry only (never the catalog vocabulary), reusing the
        # existing Damerau-Levenshtein implementation from typo_correction.py.
        query_tokens = text.split()
        for definition, triggers in _TRIGGER_TOKENS:
            for trigger, trigger_tokens in triggers:
                if _fuzzy_window_match(query_tokens, trigger_tokens):
                    matched_definitions.append(definition)
                    matched_phrases.append(trigger)
                    break

    if not matched_definitions:
        return HealthIntentResult()

    categories = tuple(dict.fromkeys(d.category for d in matched_definitions))
    primary = tuple(dict.fromkeys(p for d in matched_definitions for p in d.primary_preferences))
    secondary = tuple(dict.fromkeys(p for d in matched_definitions for p in d.secondary_preferences))

    return HealthIntentResult(
        detected=True,
        categories=categories,
        matched_phrases=tuple(matched_phrases),
        primary_preferences=primary,
        secondary_preferences=secondary,
    )
