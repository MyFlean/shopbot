"""
Utility helpers
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Tuple

_BUDGET_RGX = re.compile(r"(\d[\d,]*)\s*[-–]\s*(\d[\d,]*)")


def parse_budget_range(raw: str) -> Tuple[int, int] | None:
    m = _BUDGET_RGX.search(raw.replace("$", "").replace("₹", ""))
    if not m:
        return None
    low, high = (int(p.replace(",", "")) for p in m.groups())
    return (low, high) if low <= high else (high, low)


def price_within(price: int | float, budget: Tuple[int, int] | None) -> bool:
    return True if budget is None else budget[0] <= price <= budget[1]

_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


def extract_json_block(text: str) -> Dict[str, Any]:
    m = _JSON_BLOCK.search(text)
    if not m:
        return {}
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return {}


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

# NEW small helpers

def trim_history(history: List[Dict[str, Any]], max_len: int) -> None:
    if max_len <= 0:
        return
    overflow = len(history) - max_len
    if overflow > 0:
        del history[:overflow]


def safe_get(d: Dict[str, Any], key: str, default: Any = None) -> Any:
    try:
        return d[key]
    except Exception:
        return default