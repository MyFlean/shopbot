# shopping_bot/data_fetchers/product_reviews.py
"""
fetch_product_reviews – mock review API wrapper.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from ..enums import ShoppingFunction
from ..models import UserContext
from . import register_fetcher


async def fetch_product_reviews(ctx: UserContext) -> Dict[str, Any]:
    """
    Returns last few reviews + aggregate rating.
    """
    await asyncio.sleep(0.05)
    return {
        "average_rating": 4.5,
        "reviews": [
            {"user": "Alice", "rating": 5, "comment": "Great laptop!"},
            {"user": "Bob", "rating": 4, "comment": "Solid value."},
        ],
    }


register_fetcher(ShoppingFunction.FETCH_PRODUCT_REVIEWS, fetch_product_reviews)
