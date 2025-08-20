# shopping_bot/data_fetchers/__init__.py
"""
Simplified data fetcher registry focused on Elasticsearch.
All product-related functions route to the ES implementation.
"""

from __future__ import annotations
from typing import Any, Awaitable, Callable, Dict

from ..enums import BackendFunction

# Registry for all data fetchers
_REGISTRY: Dict[BackendFunction, Callable[..., Awaitable[Any]]] = {}

def register_fetcher(
    function: BackendFunction,
    handler: Callable[..., Awaitable[Any]],
) -> None:
    """Register a fetcher function with its handler"""
    _REGISTRY[function] = handler

def get_fetcher(function: BackendFunction) -> Callable[..., Awaitable[Any]]:
    """Get the handler for a specific function"""
    if function not in _REGISTRY:
        raise ValueError(f"No fetcher registered for {function}")
    return _REGISTRY[function]

# Import the ES implementation (this registers the handlers)
from . import es_products  # noqa: E402, F401

# Verify all functions are registered
def verify_registry():
    """Ensure all backend functions have handlers"""
    missing = []
    for func in BackendFunction:
        if func not in _REGISTRY:
            missing.append(func)
    
    if missing:
        print(f"WARNING: Missing fetchers for: {missing}")
    else:
        print(f"✅ All {len(BackendFunction)} backend functions registered")

# Run verification on import
verify_registry()

__all__ = ["register_fetcher", "get_fetcher"]