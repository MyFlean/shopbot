from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from search_v2.config.settings import SETTINGS
from search_v2.query_processing.health_intent_classifier import HealthIntentResult
from search_v2.ranking.business_ranking import apply_business_ranking


class _Item:
    def __init__(self, doc_id: str, source: dict, fused_score: float = 1.0):
        self.doc_id = doc_id
        self.source = source
        self.fused_score = fused_score


def test_health_preference_skipped_when_goal_diet_ids_present():
    items = [_Item("1", {"stats": {"protein_percentiles": {"subcategory_percentile": 90}}})]
    health = HealthIntentResult(detected=True, goal_diet_ids=("high_protein",), matched_phrases=("high protein",))

    ranked = apply_business_ranking(
        items,
        settings=SETTINGS,
        health_intent=health,
        goal_diet_ids=["high_protein"],
    )
    assert "health_preference_rule" not in ranked[0].rule_breakdown
