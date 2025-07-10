# shopping_bot/data_fetchers/similar_products.py
"""
fetch_similar_products – toy implementation.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from ..enums import ShoppingFunction
from ..models import UserContext
from . import register_fetcher


async def fetch_similar_products(ctx: UserContext) -> Dict[str, List[Dict[str, Any]]]:
    await asyncio.sleep(0.05)
    return {
        "similar": [
            {"id": "3", "name": "Similar Laptop C", "price": 1100},
            {"id": "4", "name": "Similar Laptop D", "price": 1250},
        ]
    }


register_fetcher(ShoppingFunction.FETCH_SIMILAR_PRODUCTS, fetch_similar_products)
