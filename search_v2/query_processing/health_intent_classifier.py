from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Tuple


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
            "gym", "muscle gain", "bodybuilding", "high protein",
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
        secondary_preferences=("high_protein",),
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
        primary_preferences=("low_saturated_fat", "low_sodium"),
        secondary_preferences=("high_fiber",),
    ),
    HealthIntentDefinition(
        category="blood_pressure",
        triggers=("hypertension", "bp", "high bp"),
        primary_preferences=("low_sodium",),
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


@dataclass(frozen=True)
class HealthIntentResult:
    detected: bool = False
    categories: Tuple[str, ...] = ()
    matched_phrases: Tuple[str, ...] = ()
    primary_preferences: Tuple[str, ...] = ()
    secondary_preferences: Tuple[str, ...] = ()


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
