# shopping_bot/data_fetchers/product_inventory.py
"""
Mock implementation of `fetch_product_inventory`.

In production you would replace the hard-coded list with:

• SQL / NoSQL queries
• External API calls
• Vector DB similarity searches
• …anything slow or costly that shouldn’t run twice in 5 min
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from ..enums import ShoppingFunction
from ..models import UserContext
from . import register_fetcher


# ————————————————————————————————————————————————
# Public coroutine (signature enforced by bot_core)
# ————————————————————————————————————————————————
async def fetch_product_inventory(ctx: UserContext) -> Dict[str, List[Dict[str, Any]]]:
    """
    Returns a list of products that *roughly* match the user's
    budget + preferences stored in `ctx.session`.

    For the prototype we filter the static catalogue with trivial logic.
    """
    budget_range = _parse_budget(ctx.session.get("budget", ""))
    prefs = ctx.session.get("preferences", "").lower()

    catalogue = [
        {"id": "1", "name": "Gaming Laptop A", "price": 1200, "tags": ["gaming"]},
        {"id": "2", "name": "Business Laptop B", "price": 800, "tags": ["business"]},
        {"id": "3", "name": "Ultralight Laptop C", "price": 1400, "tags": ["portable"]},
    ]

    # Simulate network / DB latency so you can see await behaviour in dev
    await asyncio.sleep(0.1)

    def matches(item: Dict[str, Any]) -> bool:
        in_budget = True
        if budget_range:
            in_budget = budget_range[0] <= item["price"] <= budget_range[1]

        has_pref = True
        if prefs:
            has_pref = any(tag in prefs for tag in item["tags"])

        return in_budget and has_pref

    return {"products": [p for p in catalogue if matches(p)]}


# ————————————————————————————————————————————————
# Helper utilities
# ————————————————————————————————————————————————
def _parse_budget(raw: str) -> tuple[int, int] | None:
    """
    Very naive '$1000-$1500' → (1000, 1500) parser.
    Extend with currency detection / localisation later.
    """
    import re

    match = re.search(r"(\d+)\s*[-–]\s*(\d+)", raw.replace(",", ""))
    if match:
        low, high = map(int, match.groups())
        return (low, high) if low <= high else (high, low)
    return None


# Register with the global REGISTRY at import time
register_fetcher(ShoppingFunction.FETCH_PRODUCT_INVENTORY, fetch_product_inventory)
