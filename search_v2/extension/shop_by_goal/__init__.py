"""
Shop by Goal — backend architecture preparation only. No API implemented.

Design principle: a "goal" (Keto, High Protein, Gluten Free, ...) is a named
filter preset layered on the SAME shared taxonomy (taxonomy/,
category_paths/category_hierarchies) — not a second, parallel hierarchy. A
goal can be combined with an ordinary category scope (e.g. "Keto snacks")
because both facets read the same document fields.

Evidence gathered directly against the local index (not assumed) —
per-goal readiness:

Ready today, no indexing change (existing category_data.tags /
stats.*_percentiles already carry the signal):
  - Gluten Free, Lactose Free, Vegan, Vegetarian -> category_data.tags.dietary_tags
    (confirmed present: "gluten_free", "nut_free" on real documents)
  - High Protein   -> stats.protein_percentiles + tags.highlight_tags.protein_tags
  - Low Sugar      -> stats.sweetener_penalty_percentiles + sweetners_sugar_tags
  - High Fiber     -> stats.fiber_percentiles + carbs_fiber_tags
  - Low Carb       -> stats.carbs_penalty_percentiles
  - Gut Health     -> stats.fermentation_percentiles (present, currently unused
    by any existing capability)

Composable from existing fields, needs a rule definition (not new data) —
same pattern as search_v2/ranking/business_ranking.py's per-category rules:
  - Keto            -> low carbs_penalty AND high healthy_fat_percentiles
  - Heart Healthy   -> low saturated_fat_penalty AND low trans_fat_penalty AND low sodium_penalty
  - Diabetic Friendly -> low sweetener_penalty AND low carbs_penalty AND high fiber
  - Weight Loss     -> low energy/calories tag AND high fiber AND high protein

Not yet supported by any indexed signal found — would need new tagging at
indexing time (search repo, document_transformer.py), not a shopbot-main
change:
  - Kids Nutrition, Sports Nutrition, Immunity

Architectural note for whoever extends the indexing pipeline: category_data
is mapped as plain `object` (search_v2/indexing/mapping_builder.py), so
category_data.tags.dietary_tags/ingredient_tags are dynamically-mapped
`text`, not `keyword` — confirmed live (an aggregation on
category_data.tags.dietary_tags fails with "Text fields are not optimised
for operations that require per-document field data"). Exact-match goal
filtering needs a keyword sub-field on these tags; this is a mapping
extension to make when Shop by Goal is actually implemented, not before.

No routes, no query functions, no filter presets implemented here yet —
this module exists so the taxonomy stays single and shared when that work
starts.
"""
