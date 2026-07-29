# Goal / Diet PDF Audit — Full Rewrite (July 2026)

Source of truth: founder PDF `flean_goal_diet_query_formulas` and plain-English extraction.

**Rewrite objective:** Every YAML must represent the founder's business logic as closely as possible. Proxies are used only when indexed fields are genuinely missing, must be conservative, and must be documented in `blocked_rules`. Category-relative percentile proxies are **not** used for absolute nutritional thresholds.

**Infrastructure added:** `derived_metric` script filters compute PDF absolutes at query time from indexed nutrients:
- `net_carbs` = carbs_g − fiber_g
- `fat_cal_pct` = (fat_g × 9 / energy_kcal) × 100
- `protein_cal_pct` = (protein_g × 4 / energy_kcal) × 100

---

## Pipeline order (unchanged)

1. **Eligibility** — `merge_goal_diet_plans()` → `filter_clauses` (AND across selected tiles)
2. **Exclusion** — `must_not_clauses`
3. **Sort** — `resolve_sort_for_filters()` after eligible set is built

---

## Complete audit table

| Tile | PDF rule (summary) | Previous implementation | Updated implementation | Exact / proxy | Missing field | Proxy reason | Remaining fidelity gap |
|------|-------------------|-------------------------|------------------------|---------------|---------------|--------------|------------------------|
| **High Protein** | ANY: protein≥10g, protein_cal≥15%, high_protein_density tag | protein≥10 OR protein pct≥75 OR tag; exclude score<50, sweetener pct+sugar | protein≥10 OR **derived protein_cal_pct≥15** OR tag; exclude score<50, sweetener pct+sugar>10 | Exact macros + derived pct + tag; sweetener exclude proxied | added_sugar_g, sweetener highlight tags | sweetener_penalty≥75 AND sugar_g>10 | May miss bad-sweetener/low-sugar products; may over-exclude high-natural-sugar |
| **Weight Loss** | ALL: kcal≤400, added sugar≤5, ANY(fiber≥3, fiber bonus, protein_cal≥12%); exclude UP+score<55 OR fat>20 | Same structure but protein pct≥75 proxy | kcal≤400, sugar_g≤5 proxy, ANY(fiber≥3, fiber bonus, **protein_cal_pct≥12**); exclude unchanged | Exact kcal/fiber/fat; sugar and protein_cal proxied | added_sugar_g | sugar_g≤5 conservative; protein_cal_pct now exact via script | Natural sugar >5g excluded; sort not calories_penalty |
| **Muscle Gain** | ALL: protein≥12, kcal≥350, amino tag OR high_protein_density; exclude added sugar>15, trans>0 | Included protein pct≥75 fallback | protein≥12, kcal≥350, **tags only** (no percentile fallback); exclude sugar_g>15, trans>0 | Exact macros + tags; sugar proxied | added_sugar_g | sugar_g>15 conservative | Products without tags but with macros fail (PDF requires tag path) |
| **Gut Health** | ANY(fiber≥6, fiber pct≥70, fermentation bonus) AND not UP; exclude no_fiber tag, artificial sweeteners | Same (fiber_category pct intentional in PDF) | Unchanged structure — PDF allows category fiber pct | Exact fiber/fermentation/UP; sweetener text proxy | highlight carbs_fiber tags, normalised sweeteners | ingredient_text_any | Sort uses fiber percentile not fermentation DESC |
| **Heart Healthy** | ALL: sat≤5, trans=0, sodium≤300, cholesterol≤20; exclude palm oil, negative oil tags | Absolute nutrients OR percentile escape hatches (weakened rules) | **Absolute only:** sat≤5, trans=0, sodium≤300; palm oil text exclude | Exact sat/trans/sodium | cholesterol_mg, oils_fats tags | Rule blocked | Cholesterol>20 products admitted; negative oil tags not excluded |
| **Energy & Focus** | carbs≥30, added≤8, fiber≥3 OR medium_cal tag, protein≥5; exclude added>15, trans>0 | carbs_penalty pct proxy for medium_cal | carbs≥30, sugar_g≤8, **fiber≥3 only**, protein≥5; exclude unchanged | Exact carbs/fiber/protein; sugar proxied | added_sugar_g, medium_calories tag | sugar_g≤8 conservative; medium_cal blocked | medium_cal-only qualifiers excluded |
| **Immunity** | fortification OR 3+ micronutrients; exclude score<50, added>15 | fortification only (partial) | Unchanged fortification paths | Exact score/sugar exclude | immunity_micronutrient_count, added_sugar_g | sugar_g>15 conservative | 3+ micronutrient products without fortification excluded |
| **Clean Eating** | not UP, no harmful additives, preservative free; exclude additives>2, artificial sweeteners | adjusted_score pct≥75 escape hatch (too loose) | not UP, tag/bonus for additives & preservative (no score pct); sweetener text exclude | Exact UP; tag/bonus hybrid | additives_count, normalised sweeteners | text proxy for sweeteners | >2 additives not excluded |
| **Low Sugar** | added≤2 AND total≤8; exclude syrups, negative sweetener tags | sugar pct proxies (loose) | **sugar_g≤2 only** (conservative for both caps); syrup text exclude | Exact total sugar cap (strict) | added_sugar_g, sweetener tags, honey/jaggery conditional | sugar_g≤2 approximates both PDF caps | Excludes 3–8g natural sugar products |
| **Kids Friendly** | no harmful additives, added≤8, trans=0, score≥60; exclude sweeteners, sodium>400, colours | Mostly exact | Unchanged exact structure | Exact trans/score/sodium | added_sugar_g, INS colour codes | sugar_g≤8 conservative | Artificial colours not excluded |
| **Keto** | ALL: net_carbs≤15, added≤2, fat_cal≥40%; exclude syrups, maida; sort net_carbs ASC | carbs_penalty + healthy_fat percentiles (admitted Quaker Oats) | **derived net_carbs≤15, sugar_g≤2, derived fat_cal_pct≥40**; text exclusions | Exact net_carbs + fat_cal via script; sugar proxied | added_sugar_g | sugar_g≤2 conservative | Natural sugar >2g excluded; sort not net_carbs ASC |
| **Vegan** | veg label, no milk allergen, no animal ingredients; exclude milk protein | Hybrid tag + veg label + text | Unchanged hybrid (allergen blocked) | Tag/label/text | allergens, normalised protein | text exclude/any | Milk allergen without text may pass |
| **Vegetarian** | veg label only | veg label OR vegetarian tag | **dietary_label veg only** (PDF literal) | Exact label | — | — | vegetarian tag without label excluded |
| **Diabetic** | added≤1, total≤5, fiber≥3 OR protein≥8, no maida; exclude sweeteners, high carb/low fiber | sugar_penalty percentiles (not in PDF) | **sugar_g≤1**, fiber/protein any_of, maida exclude; carb/fiber exclude | Exact fiber/protein/carb guard; sugar proxied | added_sugar_g, GI field | sugar_g≤1 conservative | 2–5g total sugar products excluded |
| **Gluten Free** | no wheat allergen, no wheat ingredients; exclude wheat categories unless GF label | Tag + text hybrid | Unchanged | Tag + text | allergens, category hierarchy | blocked | Wheat allergen without text may pass |
| **Dairy Free** | no milk allergen, no dairy ingredients | Tag + text hybrid | Unchanged | Tag + text | allergens | text proxy | Same as vegan dairy path |
| **High Fiber** | fiber≥6 OR fiber pct≥80; exclude no_fiber tag, added>12 | Same (PDF allows category pct) | Unchanged | Exact fiber; sugar proxied | added_sugar_g | sugar_g>12 conservative | — |
| **Low Sodium** | sodium≤120; exclude negative sodium tags, missing sodium | sodium≤120 OR sodium_penalty pct (loose) | **sodium≤120 + exists sodium_mg only** | Exact absolute sodium | negative sodium tags | blocked | Negative-tag products with low sodium may remain |
| **Millet Based** | millet in top 3 ingredients; exclude late millet, added>12 | Any millet text match | Millet text/tag match; sugar exclude | Text/tag presence | millet_primary top-3 flag | blocked | Millet-washing products admitted |
| **Nut Free** | nut_free tag required; exclude nut allergens, may contain | Tag OR text fallback (not PDF) | **dietary_tag nut_free only** | Exact tag for inclusion | allergens | may-contain text only | Untagged nut-free products excluded; allergen without warning may remain |
| **Low Fat** | *(not in PDF — extension)* | fat≤5 OR fat_penalty pct | **fat_g≤5 absolute only** | Exact | — | — | Extension tile |

---

## Live validation summary (post-rewrite)

Script: `scripts/validate_all_goal_diet.py`

| Tile | Pool size (approx.) | Known negative check |
|------|----------------------|----------------------|
| keto | ~4 (was ~1,376 with percentile proxies) | **Quaker Oats — correctly EXCLUDED** |
| high_protein | ~thousands | — |
| vegetarian | ~5,776 | — |
| weight_loss | ~1,050 | — |
| nut_free | tag-only (smaller pool) | — |

**Quaker Oats fix confirmed:** Previously admitted via `carbs_penalty≤50` + `healthy_fat≥75` percentiles. Now rejected by `derived_metric net_carbs≤15` (58.5g) and `fat_cal_pct≥40` (12.2%).

---

## 1. Goals/Diets faithful to PDF (given current index)

These tiles enforce all PDF rules that **can** be expressed with indexed fields, with only documented conservative proxies:

| Faithful (core logic) | Caveats |
|-----------------------|---------|
| **Keto** | net_carbs + fat_cal_pct exact via script; sugar_g≤2 proxy for added sugar |
| **High Protein** | protein_g + protein_cal_pct exact; sweetener exclude proxied |
| **Weight Loss** | kcal/fiber/fat exact; sugar + sort proxied |
| **Muscle Gain** | macros exact; tags required per PDF; sugar proxied |
| **Gut Health** | PDF allows category fiber pct — faithful |
| **Heart Healthy** | sat/trans/sodium exact; cholesterol blocked |
| **Low Sugar** | strict sugar_g≤2; tag/honey exclusions blocked |
| **Low Sodium** | absolute sodium + exists guard — faithful |
| **High Fiber** | fiber absolute + PDF category pct — faithful |
| **Vegetarian** | veg label only — faithful |
| **Nut Free** | tag-only inclusion per PDF — faithful |
| **Low Fat** | extension; absolute fat — faithful |

Partially faithful (blocked rules remain):

| Tile | Blocked gaps |
|------|-------------|
| Energy & Focus | medium_calories tag |
| Immunity | micronutrient count |
| Clean Eating | additives count |
| Kids Friendly | INS colour codes |
| Vegan / Dairy Free / Gluten Free | allergen arrays, category hierarchy |
| Millet Based | top-3 ingredient position |
| Diabetic | added vs total sugar distinction (conservative sugar_g≤1) |

---

## 2. Indexing work still required

| Priority | Field | Unblocks |
|----------|-------|----------|
| **P1** | `added_sugar_g` | Keto, Low Sugar, Weight Loss, Diabetic, Kids, High Fiber, Millet |
| **P1** | `ingredients.allergens.contains` | Vegan, Dairy Free, Gluten Free, Nut Free |
| **P2** | `cholesterol_mg` | Heart Healthy full PDF gate |
| **P2** | `goal_metrics.immunity_micronutrient_count` | Immunity micronutrient path |
| **P2** | `tags.millet_primary` | Millet Based anti-washing |
| **P3** | `ingredients.additives_count` | Clean Eating exclusion |
| **P3** | `category_data.tags.highlight_tags.*` | Sweetener/oil/fiber/energy tag rules |
| **P3** | `ingredients.normalised.*` | Structured ingredient buckets |
| **P3** | `category_exclusion_tags` | Gluten Free wheat-category rule |
| **P3** | Script sort for net_carbs ASC | Keto PDF sort order |

Note: `net_carbs`, `fat_cal_pct`, `protein_cal_pct` are now computed at query time — pre-indexing `goal_metrics.*` is optional (would improve sort performance).

---

## 3. Proxies removed in this rewrite

| Proxy removed | Why it violated PDF | Replacement |
|---------------|---------------------|-------------|
| Keto `carbs_penalty≤50` | Category-relative; admitted 68.5g carb oats | `derived_metric net_carbs≤15` |
| Keto `healthy_fat≥75` | Category-relative; admitted 12% fat-cal oats | `derived_metric fat_cal_pct≥40` |
| Keto `sugar_penalty≤50` | Not in PDF inclusion | `sugar_g≤2` |
| High Protein `protein pct≥75` | Not in PDF (replaces protein_cal≥15%) | `derived_metric protein_cal_pct≥15` |
| Weight Loss `protein pct≥75` | Not in PDF | `derived_metric protein_cal_pct≥12` |
| Muscle Gain `protein pct≥75` | Not in PDF tag requirement | Removed — tags only |
| Heart Healthy percentile OR group | Weakened absolute thresholds | Removed |
| Low Sodium `sodium_penalty≤50` | Not absolute ≤120 mg | Removed |
| Low Sugar sugar_penalty percentiles | Not in PDF | Removed — sugar_g≤2 |
| Clean Eating `adjusted_score≥75` | Not in PDF | Removed |
| Diabetic `sugar_penalty_global` | Not in PDF | Removed |
| Nut Free text fallback inclusion | PDF requires tag | Removed |
| Low Fat `fat_penalty≤50` | Extension; not absolute | Removed |

---

## 4. Business-rule ambiguities in PDF

1. **Energy & Focus:** "Fiber ≥3 OR medium_calories tag AND protein ≥5" — grouping unclear; implemented as fiber≥3 (blocked medium_cal path) plus protein≥5 as separate AND requirements.
2. **Muscle Gain:** "Complete amino profile OR high protein density" — when neither tag is indexed, strict PDF interpretation excludes otherwise high-protein products.
3. **Low Sugar vs Keto sugar rules:** PDF distinguishes added vs total sugar; without `added_sugar_g`, conservative total-sugar caps are used with documented over-exclusion risk.
4. **Gluten Free category exclusion:** "Wheat categories unless labelled GF" requires category+label exception logic not yet indexable.
5. **Nut Free vs may-contain:** PDF excludes nut allergens AND may-contain warnings; only text may-contain is enforced until allergen index exists.
6. **Founder lists 9 Diets; codebase has 10** including `low_fat` (extension, not in PDF).

---

## Validation commands

```bash
# Unit tests
./venv/bin/python -m pytest search_v2/tests/test_goal_diet*.py -q

# Live index validation (includes Quaker Oats keto negative check)
./venv/bin/python scripts/validate_all_goal_diet.py

# Single-product keto trace
./venv/bin/python scripts/trace_keto_product.py
```
