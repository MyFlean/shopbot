# shopping_bot/data_fetchers/__init__.py
"""
Entry-point + registry for all data-fetcher coroutines.

Order of operations:
1. Define _REGISTRY and helper fns
2. Import every sibling module (each module calls register_fetcher)
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from ..enums import ShoppingFunction

# ─────────────────────────────────────────────
# 1) Registry & helpers (must come first!)
# ─────────────────────────────────────────────
_REGISTRY: Dict[ShoppingFunction, Callable[..., Awaitable[Any]]] = {}


def register_fetcher(
    func: ShoppingFunction,
    handler: Callable[..., Awaitable[Any]],
) -> None:
    """Called by each fetcher module to expose itself."""
    _REGISTRY[func] = handler


def get_fetcher(func: ShoppingFunction) -> Callable[..., Awaitable[Any]]:
    return _REGISTRY[func]


# ─────────────────────────────────────────────
# 2) Import sibling modules (side-effect: they call register_fetcher)
#    Keep these *after* the definitions above.
# ─────────────────────────────────────────────
from . import (  # noqa: E402, F401
    product_inventory,
    purchase_history,
    user_profile,
    product_details,
    similar_products,
    product_reviews,
)

__all__ = ["register_fetcher", "get_fetcher"]
