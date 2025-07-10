# shopping_bot/data_fetchers/purchase_history.py
"""
Tiny mock of `fetch_purchase_history`.

Meant to illustrate how each fetcher lives in its own module,
registers itself, and can be unit-tested independently.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, List

from ..enums import ShoppingFunction
from ..models import UserContext
from . import register_fetcher


async def fetch_purchase_history(ctx: UserContext) -> Dict[str, List[Dict[str, Any]]]:
    """
    Pretend to hit an Orders micro-service and return past purchases.
    """
    await asyncio.sleep(0.05)  # simulate latency
    return {
        "orders": [
            {
                "id": "A1",
                "product": "Wireless Mouse",
                "date": datetime(2024, 12, 15).isoformat(),
            }
        ]
    }


register_fetcher(ShoppingFunction.FETCH_PURCHASE_HISTORY, fetch_purchase_history)
