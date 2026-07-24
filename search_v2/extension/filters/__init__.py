"""
Native V2 dynamic filter capabilities beyond what search_v2/extension/search/core.py
already computes for the query-driven /rs/v1/search path.

The gateway's own _compute_dynamic_filters (price bounds + facet
aggregations) is already correct and V2-native for that one endpoint. This
module is for filter-building needs specific to non-search endpoints (home,
catalogue) that currently build filters via
shopping_bot/routes/product_api.py's _build_filters_from_query_args().
"""
