"""
search_v2/extension/
─────────────────────
Native Search V2 implementations of business capabilities that today exist
only in shopping_bot/data_fetchers/es_products.py (the legacy V1 fetcher).

Each subpackage owns exactly one business capability, reimplemented against
Search V2's schema/index/retrieval — not a copy of V1's logic. See
SEARCH_V2_MIGRATION_ARCHITECTURE.md (shopbot-main repo root) for the full
migration plan, FEATURE_MIGRATION_MATRIX.md for capability-by-capability
status, and ENDPOINT_MIGRATION_MATRIX.md for endpoint-by-endpoint status.

No implementation exists yet in any subpackage as of this scaffold — see
MIGRATION_STATUS.md for current phase.
"""
