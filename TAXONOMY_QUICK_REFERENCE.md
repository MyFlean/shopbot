# Taxonomy-Guided Categorization - Quick Reference

## For Developers

### How It Works (30 Second Version)
1. User asks: "cold coffee"
2. LLM receives F&B taxonomy JSON in prompt
3. LLM returns: `category_paths: ["f_and_b/beverages/tea_coffee_and_more/iced_coffee_and_tea"]`
4. ES query uses exact path
5. Results returned

### Key Files
- **Taxonomy Source**: `shopping_bot/llm_service.py` (lines 4776-4881, embedded fallback)
- **Optional Override**: `shopping_bot/taxonomies/fnb_hierarchy.json`
- **Prompt Injection**: `shopping_bot/llm_service.py` (lines 2982-2984, 3106-3108)
- **ES Path Handler**: `shopping_bot/data_fetchers/es_products.py` (lines 204-294)

### Path Format
```
Full Format (LLM generates):
  f_and_b/food/{l2}/{l3}
  f_and_b/beverages/{l2}/{l3}
  f_and_b/{food|beverages}/{l2}  (L2-only for ambiguous)

Examples:
  ✅ f_and_b/food/light_bites/chips_and_crisps
  ✅ f_and_b/beverages/tea_coffee_and_more/iced_coffee_and_tea
  ✅ f_and_b/food/frozen_foods  (L2 only)
  ❌ light_bites/chips_and_crisps  (legacy, still works but not recommended)
```

---

## For Product/QA

### Test Cases

#### Single Exact Match
```
Query: "banana chips"
Expected: ["f_and_b/food/light_bites/chips_and_crisps"]
```

#### Ranked Alternatives
```
Query: "ice cream"
Expected: [
  "f_and_b/food/frozen_treats/ice_cream_tubs",
  "f_and_b/food/frozen_treats/ice_cream_cups",
  "f_and_b/food/frozen_treats/kulfi"
]
```

#### Beverages
```
Query: "soft drinks"
Expected: ["f_and_b/beverages/sodas_juices_and_more/soft_drinks"]

Query: "green tea"
Expected: ["f_and_b/beverages/tea_coffee_and_more/green_and_herbal_tea"]
```

#### L2 Fallback
```
Query: "frozen snacks"
Expected: ["f_and_b/food/frozen_foods"]
```

### What to Monitor
1. **Path Accuracy**: Do returned paths exist in taxonomy?
2. **Ranking Quality**: Is the first path the most relevant?
3. **Hallucination**: Any invented paths not in taxonomy?

### Where to Check Logs
```bash
# LLM output
grep "CORE:LLM2_OUT_FULL" logs/app.log

# Category filter construction
grep "CAT_PATH_FILTER" logs/app.log
```

---

## Complete F&B Taxonomy (Copy-Paste Ready)

### Food Categories

#### light_bites
- chips_and_crisps
- nachos
- savory_namkeen
- dry_fruit_and_nut_snacks
- popcorn
- energy_bars

#### frozen_treats
- ice_cream_tubs
- ice_cream_cups
- ice_cream_cones
- ice_cream_sticks
- ice_cream_cakes_and_sandwiches
- light_ice_cream
- frozen_pop_cubes
- kulfi

#### breakfast_essentials
- muesli_and_oats
- dates_and_seeds
- breakfast_cereals

#### packaged_meals
- papads_and_pickles_and_chutneys
- baby_food
- pasta_and_soups
- baking_mixes_and_ingredients
- ready_to_cook_meals
- ready_to_eat_meals

#### dairy_and_bakery
- batter_and_mix
- butter
- paneer_and_cream
- cheese
- vegan_beverages
- yogurt_and_shrikhand
- curd_and_probiotic_drinks
- bread_and_buns
- eggs
- gourmet_specialties

#### sweet_treats
- pastries_and_cakes
- candies_gums_and_mints
- chocolates
- premium_chocolates
- indian_mithai
- dessert_mixes

#### noodles_and_vermicelli
- vermicelli_and_noodles

#### biscuits_and_crackers
- glucose_and_marie_biscuits
- cream_filled_biscuits
- rusks_and_khari
- digestive_biscuits
- wafer_biscuits
- cookies
- crackers

#### frozen_foods
- non_veg_frozen_snacks
- frozen_raw_meats
- frozen_vegetables_and_pulp
- frozen_vegetarian_snacks
- frozen_sausages_salami_and_ham
- momos_and_similar
- frozen_roti_and_paratha

#### spreads_and_condiments
- ketchup_and_sauces
- honey_and_spreads
- peanut_butter

### Beverages Categories

#### sodas_juices_and_more
- soda_and_mixers
- flavored_milk_drinks
- instant_beverage_mixes
- fruit_juices
- energy_and_non_alcoholic_drinks
- soft_drinks
- iced_coffee_and_tea
- bottled_water
- enhanced_hydration

#### tea_coffee_and_more
- iced_coffee_and_tea
- green_and_herbal_tea
- tea
- beverage_mix
- coffee

#### dairy_and_bakery
- milk

---

## FAQ

**Q: What if LLM returns wrong path?**  
A: Check logs for `CORE:LLM2_OUT_FULL`. If path doesn't exist in taxonomy, it's a hallucination. File a bug with the query.

**Q: Can we add new categories?**  
A: Yes. Update `shopping_bot/taxonomies/fnb_hierarchy.json` (or embedded fallback in `llm_service.py` lines 4776-4881).

**Q: What about personal care?**  
A: Separate taxonomy, different prompt branch. This implementation is F&B only.

**Q: Why 1-3 paths?**  
A: Balances precision (single match) with coverage (alternatives for ambiguous queries). More than 3 dilutes relevance.

**Q: Backward compatible?**  
A: Yes. Legacy relative paths (`light_bites/chips_and_crisps`) still work via fallback logic.

**Q: Performance impact?**  
A: ~400 tokens added to prompt. Negligible latency increase (<50ms).

---

## Troubleshooting

### Issue: Path Not Found in ES
**Symptom**: ES returns 0 results despite valid path  
**Check**:
1. Does ES mapping have `category_paths` or `category_paths.keyword`?
2. Is path format exact match: `f_and_b/food/...` vs `f_and_b/beverages/...`?
3. Check debug log: "using category_paths.keyword" vs "using wildcard"

### Issue: Wrong Category
**Symptom**: Query returns unrelated products  
**Check**:
1. Is LLM returning correct path? Check `CORE:LLM2_OUT_FULL`
2. Are there multiple paths? First path should be most relevant
3. Is query ambiguous? Consider adding example to prompt

### Issue: Hallucinated Path
**Symptom**: Path doesn't exist in taxonomy  
**Fix**:
1. Add missing category to taxonomy
2. OR add example showing correct categorization
3. OR strengthen "CRITICAL" rule in prompt

---

**Last Updated**: 2025-01-08  
**Version**: 1.0  
**Status**: Production Ready

