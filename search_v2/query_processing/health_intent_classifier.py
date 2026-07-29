from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Tuple

from search_v2.goal_diet.registry_loader import get_compiled_registry
from search_v2.query_processing.typo_correction import damerau_levenshtein, _auto_fuzziness_budget


@dataclass(frozen=True)
class HealthIntentResult:
    """Health Intake output — canonical goal/diet IDs and matched phrases only."""

    detected: bool = False
    goal_diet_ids: Tuple[str, ...] = ()
    matched_phrases: Tuple[str, ...] = ()


def classify_health_intent(raw_query: str) -> HealthIntentResult:
    text = (raw_query or "").strip().lower()
    if not text:
        return HealthIntentResult()

    registry = get_compiled_registry()
    matched_ids: List[str] = []
    matched_phrases: List[str] = []
    seen_ids: set[str] = set()

    for pattern, definition_id, trigger in registry.trigger_index:
        if definition_id in seen_ids:
            continue
        if pattern.search(text):
            matched_ids.append(definition_id)
            matched_phrases.append(trigger)
            seen_ids.add(definition_id)

    if not matched_ids:
        fuzzy_match = _fuzzy_trigger_match(text, registry)
        if fuzzy_match:
            matched_ids.append(fuzzy_match[0])
            matched_phrases.append(fuzzy_match[1])

    if not matched_ids:
        return HealthIntentResult()

    return HealthIntentResult(
        detected=True,
        goal_diet_ids=tuple(dict.fromkeys(matched_ids)),
        matched_phrases=tuple(matched_phrases),
    )


def _fuzzy_trigger_match(text: str, registry) -> Tuple[str, str] | None:
    """Lightweight fuzzy fallback against compiled registry triggers only."""
    query_tokens = text.split()
    if not query_tokens:
        return None

    for _pattern, definition_id, trigger in registry.trigger_index:
        trigger_tokens = tuple(trigger.split())
        if _fuzzy_window_match(query_tokens, trigger_tokens):
            return definition_id, trigger
    return None


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
