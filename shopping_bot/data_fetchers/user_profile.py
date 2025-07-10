# shopping_bot/data_fetchers/user_profile.py
"""
fetch_user_profile – pulls user meta (mock).
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from ..enums import ShoppingFunction
from ..models import UserContext
from . import register_fetcher


async def fetch_user_profile(ctx: UserContext) -> Dict[str, Any]:
    """
    Example: hydrate from CRM; here we just echo stored permanent data.
    """
    await asyncio.sleep(0.02)
    return ctx.permanent.get("profile", {"name": "User", "email": "user@example.com"})


register_fetcher(ShoppingFunction.FETCH_USER_PROFILE, fetch_user_profile)
