"""
Native V2 consumption of the ONE existing shared taxonomy — not a second one.

Confirmed, not assumed: shopping_bot/data/category_mapping.json is already
the single canonical taxonomy (subcategory id -> ES category_paths string),
loaded by shopping_bot/routes/product_api.py's _load_category_mapping() and
already consumed by both the legacy fetcher and, as of category_browsing/,
Search V2 (via unified_search.py's _resolve_subcategory_es_path(), reused
as-is, not reimplemented). category_paths/category_hierarchies fields carry
this same taxonomy natively in the V2 index already — no indexing change
needed and none introduced here.

shopping_bot/data/home/categories.json is a SEPARATE, non-competing
artifact: home-page tile display config (icons, labels, ordering) for
/rs/api/v1/home/categories, not a taxonomy. Both ultimately describe the
same category tree; only category_mapping.json is the query-resolution
source of truth, and this module does not duplicate it — bestsellers/ and
category_browsing/ both resolve through the same shared function.
"""
