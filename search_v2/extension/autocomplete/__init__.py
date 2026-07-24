"""
Response-shaping layer for suggestions, kept separate from suggestions/ per
the approved directory spec.

/rs/v1/search/suggest (flat) and /rs/v2/search/suggest (grouped-by-brand)
differ only in how they shape build_suggest_query()'s results, not in the
underlying retrieval — see suggestions/ for the shared query logic.
"""
