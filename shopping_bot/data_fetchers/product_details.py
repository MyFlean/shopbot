# shopping_bot/data_fetchers/product_details.py
"""
fetch_product_details – placeholder hitting a pretend PIM service.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from ..enums import ShoppingFunction
from ..models import UserContext
from . import register_fetcher


async def fetch_product_details(ctx: UserContext) -> Dict[str, Any]:
    """
    Would normally call product-catalogue micro-service by SKU.
    """
    await asyncio.sleep(0.05)
    return {
        "specifications": {
            "processor": "Intel i7",
            "memory": "16 GB DDR5",
            "storage": "1 TB SSD",
            "gpu": "RTX 4060",
        }
    }


register_fetcher(ShoppingFunction.FETCH_PRODUCT_DETAILS, fetch_product_details)
