# shopping_bot/utils/helpers.py
"""
Side-effect-free utility helpers reused across the bot.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Tuple

# ─────────────────────────────────────────────
# Budget parsing
# ─────────────────────────────────────────────
_BUDGET_RGX = re.compile(r"(\d[\d,]*)\s*[-–]\s*(\d[\d,]*)")


def parse_budget_range(raw: str) -> Tuple[int, int] | None:
    """
    '$1,000-1,500' → (1000, 1500)
    Returns None when pattern not found.
    """
    m = _BUDGET_RGX.search(raw.replace("$", "").replace("₹", ""))
    if not m:
        return None
    low, high = (int(p.replace(",", "")) for p in m.groups())
    return (low, high) if low <= high else (high, low)


def price_within(price: int | float, budget: Tuple[int, int] | None) -> bool:
    return True if budget is None else budget[0] <= price <= budget[1]


# ─────────────────────────────────────────────
# JSON extraction from LLM output
# ─────────────────────────────────────────────
_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


def extract_json_block(text: str) -> Dict[str, Any]:
    """
    Returns first {...} block parsed as dict, {} on failure.
    """
    m = _JSON_BLOCK.search(text)
    if not m:
        return {}
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return {}


# ─────────────────────────────────────────────
# Misc small helpers
# ─────────────────────────────────────────────
def iso_now() -> str:
    return datetime.now().isoformat()


def unique(seq: List[Any]) -> List[Any]:
    seen: set[Any] = set()
    out: List[Any] = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
