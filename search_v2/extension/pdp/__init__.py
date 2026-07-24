"""
Native V2 PDP document fetch — see pdp.py.

The indexing allowlist gap (review_stats, cons_list, structured
ingredients) is fixed in the search repo's document_transformer.py; this
module's job is just the V2-native retrieval, not the transform.
"""
from .pdp import fetch_product, fetch_products_batch

__all__ = ["fetch_product", "fetch_products_batch"]
