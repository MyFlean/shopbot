"""
Native Search V2 query-driven search. Replaces search_gateway/gateway.py's
SearchGateway class — see core.py for the pipeline and rationale.
"""
from .core import search, warmup

__all__ = ["search", "warmup"]
