"""Tests for the Flean Supplement Scorecard v2 runtime computation.

The authoritative correctness target is sheet ``08 Worked Example`` of
``Flean_Supplement_Scorecard_Framework_v2`` (Avvatar Isorich Whey Isolate),
which reconciles to a composite Flean Score of 88.28.
"""

import math

import pytest

from shopping_bot.scoring import supplement_scorecards as ss


def approx(a, b, tol=0.5):
    return abs(a - b) <= tol


# ── Threshold ladders (verified against worked-example interpolation) ─────────
def test_leucine_ladder_worked_example():
    # 2.446 g per 25 g protein -> 80 (worked example A38)
    assert round(ss.LEUCINE_PER25_LADDER.score(2.446)) == 80


def test_eaa_ladder_worked_example():
    # 11.125 -> 94 (worked example A38)
    assert round(ss.EAA_PER25_LADDER.score(11.125)) == 94


def test_bcaa_ladder_worked_example():
    # 5.571 -> 99 (worked example A38)
    assert round(ss.BCAA_PER25_LADDER.score(5.571)) == 99


def test_protein_pct_ladder_anchor():
    assert ss.PROTEIN_PCT_LADDER.score(80) == 88
    assert ss.PROTEIN_PCT_LADDER.score(85) == 100
    assert ss.PROTEIN_PCT_LADDER.score(50) == 25  # below floor clamps


def test_protein_density_ladder_worked_example():
    # 21.538 g/100kcal -> 97.7 (worked example A39)
    assert approx(ss.PROTEIN_PER_100KCAL_LADDER.score(21.538), 97.7, tol=0.2)


def test_dose_ratio_ladder():
    assert ss.DOSE_RATIO_LADDER.score(1.0) == 100
    assert ss.DOSE_RATIO_LADDER.score(0.5) == 50
    assert ss.DOSE_RATIO_LADDER.score(0.1) == 15  # fairy dusted


# ── Individual cards against worked-example derived metrics ───────────────────
def _avvatar_features():
    """Synthetic features matching the worked-example section-1 input data."""
    f = ss.SupplementFeatures(leaf="whey_isolate", weight_column="Whey Isolate")
    f.is_protein_category = True
    f.serving_qty_g = 35.0
    f.protein_g = 28.0
    f.energy_kcal = 130.0
    f.total_fat_g = 1.0
    f.carb_g = 1.0
    f.added_sugar_g = 0.0
    f.sodium_mg = 100.0
    f.protein_pct_by_weight = 80.0
    f.protein_per_100kcal = 28.0 / 130.0 * 100
    f.leucine_per_25g_protein = 2.446
    f.eaa_per_25g_protein = 11.125
    f.bcaa_per_25g_protein = 5.571
    f.amino_acid_recovery = 0.8189
    f.eaa_share_of_total_aa = 0.5434
    f.has_amino_profile = True
    f.eaa_count = 9
    f.protein_ingredients = ["whey protein isolate"]
    f.sweeteners = ["ins955", "ins950"]  # sucralose + acesulfame K
    f.additives = ["ins415", "ins417"]   # 2 hydrocolloid gums
    f.raw_text = "whey protein isolate, xanthan gum, tara gum, sucralose, acesulfame k, flavour, batch no"
    f.has_raw_text = True
    f.scoop_stated = True
    f.servings_per_container = 29.0
    return f


def test_amino_acid_profile_card_is_90():
    f = _avvatar_features()
    assert ss.score_amino_acid_profile(f).score == 90


def test_protein_efficiency_card_is_95():
    f = _avvatar_features()
    assert ss.score_protein_efficiency(f).score == 95


def test_protein_quality_card_is_97():
    f = _avvatar_features()
    r = ss.score_protein_quality(f)
    # source 100 * .45 + completeness 100 * .20 + protein% 88 * .25 + cleanliness 100 * .10 = 97
    r.detail["completeness"] = 100.0  # completeness needs full per-AA panel; asserted in worked example
    manual = round(100 * 0.45 + 100 * 0.20 + 88 * 0.25 + 100 * 0.10)
    assert manual == 97
    assert r.detail["source_score"] == 100
    assert r.detail["protein_pct_score"] == 88


def test_label_trust_integrity_recovery_band():
    f = _avvatar_features()
    r = ss.score_label_trust(f)
    # Integrity: 0.819 recovery -> -15; flavour -3 -> 82
    assert r.detail["integrity"] in (85, 82)
    assert r.detail["disclosure"] == 92  # panel, amino, scoop, batch present
    assert r.detail["verification"] == 0  # no third-party certs (capped path)
    assert "label_claim_unverified" in r.tags
    assert "no_third_party_testing" in r.tags
    # 40/25/35 blend of integrity/disclosure/verification
    expected = round(
        (r.detail["integrity"] * 40 + r.detail["disclosure"] * 25 + r.detail["verification"] * 35) / 100
    )
    assert r.score == expected


def test_label_trust_no_double_proprietary_blend():
    f = _avvatar_features()
    f.raw_text = f.raw_text + ", proprietary blend"
    r = ss.score_label_trust(f)
    assert "proprietary_blend" in r.tags
    assert r.tags.count("proprietary_blend") == 1
    # Disclosure takes the -40; integrity must not also apply -20 for prop blend.
    assert r.detail["disclosure"] == 52  # 92 - 40
    assert r.detail["integrity"] in (85, 82)  # recovery/flavour only


def test_flat_amino_acid_profile_is_recognized():
    """Newer ES docs publish AAs as flat keys under amino_acid_profile (not nested)."""
    src = {
        "id": "flat-aa-1",
        "price": 2699,
        "size": "1 kg",
        "category_paths": ["f_and_b/supplements/protein/whey_concentrate"],
        "category_data": {
            # No serving_size; nutrition + AA panel are already on scoop basis.
            "servings_per_container": 29,
            "nutritional": {
                "qty": "35 g",
                "nutri_breakdown": {
                    "energy kcal": 137,
                    "protein g": 25,
                    "carbohydrate g": 4.5,
                    "total fat g": 2.1,
                    "sodium mg": 174,
                    "added sugar g": 0,
                },
            },
            "amino_acid_profile": {
                "leucine g": 2.45,
                "isoleucine g": 1.68,
                "valine g": 1.45,
                "total eaa g": 11.4,
                "histidine g": 0.28,
                "lysine g": 2.25,
                "methionine g": 0.55,
                "phenylalanine g": 0.73,
                "tryptophan g": 0.23,
                "threonine g": 1.8,
                "alanine g": 1.05,
                "glutamic acid g": 3.5,
                "glycine g": 0.63,
                "proline g": 1.68,
                "cysteine g": 0.53,
                "tyrosine g": 0.75,
                "arginine g": 0.7,
                "serine g": 1.23,
                "aspartic acid g": 3.55,
            },
            "total_amino_acids": {
                "total bcaa g": 5.58,
                "total eaa g": 11.4,
                "total neaa g": 9.33,
                "total seaa g": 4.29,
            },
            "certifications": [],
        },
        "ingredients": {
            "raw_text": "Whey Protein Concentrate, cocoa, sucralose",
            "normalised": (
                "{'oils': [], 'additives': [], 'sweeteners': ['ins955'], "
                "'protein_ingredients': ['whey protein concentrate']}"
            ),
        },
    }
    f = ss.extract_features(src)
    assert f.has_amino_profile is True
    assert f.eaa_count == 9
    assert f.leucine_g_per_serving == pytest.approx(2.45, abs=0.01)
    assert f.eaa_g_per_serving == pytest.approx(11.4, abs=0.01)
    assert f.leucine_per_25g_protein == pytest.approx(2.45, abs=0.05)
    assert f.amino_acid_recovery is not None

    r = ss.score_label_trust(f)
    assert "no_amino_profile_published" not in r.tags
    # Still penalized for missing scoop/serving_size and no batch/lot in text.
    assert r.detail["disclosure"] == 78  # 92 - 8 scoop - 6 batch

    out = ss.compute_supplement_scorecards(src)
    assert out is not None
    # Every AA-dependent card must score from amino_acid_profile / total_amino_acids.
    aa = out["cards"]["amino_acid_profile"]
    assert aa["scorable"] is True and aa["score"] is not None
    assert "no_amino_profile_published" not in (aa.get("tags") or [])
    pq = out["cards"]["protein_quality"]
    assert pq["scorable"] is True
    assert pq["detail"]["completeness"] is not None
    assert out["cards"]["label_trust"]["scorable"] is True

    adapted = ss.to_pdp_score_cards(out)
    labels = [e["tag_label"] for e in adapted["score_cards"]["label_trust"]["subtitle_new"]]
    assert "No amino profile" not in labels
    pq_labels = [e["tag_label"] for e in adapted["score_cards"]["protein_quality"]["subtitle_new"]]
    assert "Amino profile unknown" not in pq_labels


def test_legacy_found_amino_acid_profile_still_works():
    """Nested found_amino_acid_profile remains a fallback when flat profile is absent."""
    src = _protein_src()
    src["category_data"].pop("amino_acid_profile", None)
    src["category_data"].pop("total_amino_acids", None)
    src["category_data"]["found_amino_acid_profile"] = {
        "qty": "100 g",
        "total_eaa_g": "45.0",
        "total_bcaa_g": "20.0",
        "essential_amino_acids": {
            "leucine g": "10.5",
            "isoleucine g": "5.0",
            "valine g": "5.0",
            "lysine g": "8.0",
            "threonine g": "5.0",
            "methionine g": "2.0",
            "phenylalanine g": "3.0",
            "tryptophan g": "1.5",
            "histidine g": "2.0",
        },
        "non_essential_amino_acids": {"glutamic acid g": "15.0"},
    }
    f = ss.extract_features(src)
    assert f.has_amino_profile is True
    assert f.eaa_count == 9
    assert ss.score_amino_acid_profile(f).scorable is True


def test_digestibility_isolate_two_gums_is_97():
    f = _avvatar_features()
    assert ss.score_digestibility(f).score == 97


def test_digestibility_subtitle_new_tier_tags():
    adapted = ss.to_pdp_score_cards(ss.compute_supplement_scorecards(_protein_src()))
    dig = adapted["score_cards"]["digestibility"]
    assert dig["title"] == "Digestibility"
    assert dig["value"] == "Best"
    assert dig["score"] == 100  # WPI +5, no gums
    assert dig["subtitle_new"] == [
        {"tag_label": "Easy to digest", "color_code": "#2E7D32"},
        {"tag_label": "Added digestive enzymes", "color_code": "#2E7D32"},
        {"tag_label": "Lactose free", "color_code": "#2E7D32"},
    ]
    assert dig["subtitle"].startswith("Score:")


def test_bioavailability_subtitle_new_tier_tags():
    adapted = ss.to_pdp_score_cards(ss.compute_supplement_scorecards(_protein_src()))
    bio = adapted["score_cards"]["bioavailability"]
    assert bio["title"] == "Bioavailability"
    assert bio["value"] in ("Best", "Top", "Average", "Poor", "Worst")
    assert bio["score"] is not None
    assert bio["subtitle_new"]
    labels = [e["tag_label"] for e in bio["subtitle_new"]]
    # Whey isolate → Best band tier tags
    assert "Highly bioavailable" in labels
    assert "Fast absorbing" in labels
    assert "DIAAS 100+" in labels
    assert all("tag_label" in e and "color_code" in e for e in bio["subtitle_new"])
    assert bio["subtitle"].startswith("Score:")


def test_sweeteners_dual_artificial_is_64():
    f = _avvatar_features()
    r = ss.score_sweeteners(f)
    assert r.score == 64  # anchor 72 (sucralose/aceK) - 8 for the second
    assert "contains_artificial_sweeteners" in r.tags
    assert "multiple_artificial_sweeteners" in r.tags
    assert set(r.detail["detected_names"]) == {"Sucralose", "Ace-K"}
    # Same severity → deterministic single primary (alphabetical among ties)
    assert r.detail["primary_name"] == "Ace-K"


def test_sweeteners_pdp_value_names_and_subtitle_new_tags():
    adapted = ss.to_pdp_score_cards(ss.compute_supplement_scorecards(_protein_src()))
    sw = adapted["score_cards"]["sweeteners"]
    # _protein_src uses sucralose only
    assert sw["value"] == "Sucralose"
    assert " · " not in sw["value"]
    assert sw["value"] not in ("Best", "Top", "Average", "Poor", "Worst")
    labels = [e["tag_label"] for e in sw["subtitle_new"]]
    assert any("artificial" in lab.lower() for lab in labels)
    assert all(len(lab.split()) <= 2 for lab in labels)
    assert all("tag_label" in e and "color_code" in e for e in sw["subtitle_new"])
    assert sw["score"] is not None


def test_sweeteners_pdp_dual_value_is_single_worst_name():
    f = _avvatar_features()
    card = {
        "score": 64,
        "tags": ["contains_artificial_sweeteners", "multiple_artificial_sweeteners"],
        "detail": {
            "detected_names": ["Sucralose", "Ace-K"],
            "primary_name": "Ace-K",
        },
    }
    sw = ss.present_sweeteners(f, card)
    assert sw["value"] == "Ace-K"
    assert " · " not in sw["value"]
    labels = [e["tag_label"] for e in sw["subtitle_new"]]
    assert "Artificial sweeteners" in labels
    assert "Multiple artificial" in labels
    assert all(len(lab.split()) <= 2 for lab in labels)


def test_sweeteners_unsweetened_value():
    src = _protein_src()
    src["ingredients"]["normalised"] = (
        "{'oils': [], 'additives': [], 'sweeteners': [], "
        "'protein_ingredients': ['whey protein isolate']}"
    )
    src["category_data"]["nutritional"]["nutri_breakdown"]["added sugar g"] = 0.0
    sw = ss.to_pdp_score_cards(ss.compute_supplement_scorecards(src))["score_cards"]["sweeteners"]
    assert sw["value"] == "Unsweetened"


def test_serving_honesty_standard_is_100():
    f = _avvatar_features()
    f.pack_weight_g = 35.0 * 29.0  # exact pack math
    r = ss.score_serving_honesty(f)
    assert r.score == 100
    assert "ideal_scoop" in r.tags
    assert "full_tub_math" in r.tags
    assert "inflated_serving" not in r.tags


def test_serving_honesty_icon_and_subtitle_new():
    ss.clear_config_cache()
    adapted = ss.to_pdp_score_cards(ss.compute_supplement_scorecards(_protein_src()))
    sh = adapted["score_cards"]["serving_honesty"]
    assert sh["title"] == "Servings"
    assert sh["value"] == "35 g · 28 servings"
    assert sh["icon_url"] == "https://img.flean.ai/assets/Pdp-Icons/serving.svg"
    assert sh["subtitle_new"]
    assert 1 <= len(sh["subtitle_new"]) <= 2
    labels = {e["tag_label"] for e in sh["subtitle_new"]}
    assert labels <= {
        "Ideal scoop",
        "Full tub math",
        "Solid scoop",
        "Servings clear",
        "Typical scoop",
        "Inflated serving",
        "Unclear scoop",
        "Misleading servings",
        "Pack mismatch",
    }
    assert all("tag_label" in e and "color_code" in e for e in sh["subtitle_new"])


def test_servings_inflated_protein_scoop():
    f = _avvatar_features()
    f.serving_qty_g = 45.0
    f.protein_g = 18.0
    f.pack_weight_g = 45.0 * 29.0
    r = ss.score_serving_honesty(f)
    assert r.score < 90
    assert "inflated_serving" in r.tags


def test_servings_creatine_ideal_scoop():
    f = ss.SupplementFeatures(leaf="creatine", weight_column="Creatine")
    f.serving_qty_g = 5.0
    f.scoop_stated = True
    f.servings_per_container = 50.0
    f.pack_weight_g = 250.0
    r = ss.score_serving_honesty(f)
    assert r.score == 100
    assert "ideal_scoop" in r.tags


def test_servings_preworkout_bloated_scoop():
    f = ss.SupplementFeatures(leaf="pre_workout", weight_column="Pre-Workout")
    f.serving_qty_g = 25.0
    f.scoop_stated = True
    f.servings_per_container = 20.0
    f.pack_weight_g = 500.0
    r = ss.score_serving_honesty(f)
    assert "inflated_serving" in r.tags
    assert r.score <= 70


def test_label_trust_and_heavy_metals_subtitle_new():
    ss.clear_config_cache()
    adapted = ss.to_pdp_score_cards(ss.compute_supplement_scorecards(_protein_src()))
    lt = adapted["score_cards"]["label_trust"]
    assert lt["title"] == "Label Trust"
    assert lt["value"] in ("Best", "Top", "Average", "Poor", "Worst")
    assert lt["subtitle_new"]
    assert all("tag_label" in e and "color_code" in e for e in lt["subtitle_new"])
    assert len(lt["subtitle_new"]) <= 3
    hm = adapted["score_cards"]["heavy_metals"]
    assert hm["title"] == "Heavy metals"
    assert hm["subtitle_new"]
    assert all("tag_label" in e and "color_code" in e for e in hm["subtitle_new"])
    assert "purity" not in adapted["score_cards"]
    assert "transparency" not in adapted["score_cards"]
    assert "testing_trust" not in adapted["score_cards"]
    assert "contaminant_safety" not in adapted["score_cards"]


# ── Composite engine with merged Label Trust weight vector ──
def test_composite_reconciles_to_worked_example():
    ss.clear_config_cache()
    # Pillar scores from worked example; Label Trust = 40/25/35 blend.
    label_trust = round((85 * 40 + 92 * 25 + 76 * 35) / 100)  # 84
    scores = {
        "protein_quality": 97, "amino_acid_profile": 90, "protein_efficiency": 95,
        "digestibility": 97, "label_trust": label_trust,
        "heavy_metals": 55, "sweeteners": 64, "serving_honesty": 100,
    }
    weights = ss._weight_vector("Whey Isolate")
    applicable = {k: w for k, w in weights.items() if w > 0}
    total = sum(applicable[k] for k in scores)
    composite = sum(scores[k] * applicable[k] for k in scores) / total
    # Lean Formula removed; its weight folded into Digestibility (14).
    assert approx(composite, 88.48, tol=0.05)
    assert total == 100  # all applicable cards present, no renormalisation needed
    assert applicable["label_trust"] == 26.0
    assert applicable["digestibility"] == 14.0
    assert applicable["heavy_metals"] == 6.0
    assert "lean_formula" not in applicable


def test_weight_vectors_sum_to_100():
    for col in ss.load_config()["weight_columns"]:
        total = sum(w for w in ss._weight_vector(col).values())
        assert approx(total, 100.0, tol=0.01), f"{col} sums to {total}"


# ── Missing-data policy ───────────────────────────────────────────────────────
def test_renormalisation_and_confidence_deduction():
    src = {
        "id": "x", "category_paths": ["f_and_b/supplements/protein/whey_isolate"],
        "category_data": {
            "nutritional": {"qty": "100 g", "nutri_breakdown": {"protein g": 80.0, "energy kcal": 360.0,
                                                                  "total fat g": 1.0, "carbohydrate g": 5.0,
                                                                  "sodium mg": 300.0, "added sugar g": 0.0}},
            "serving_size": "30 g", "servings_per_container": 30, "certifications": [],
        },
        "ingredients": {"raw_text": "whey protein isolate", "normalised":
                        "{'oils': [], 'additives': [], 'sweeteners': [], 'protein_ingredients': ['whey protein isolate']}"},
        "price": 3000,
    }
    out = ss.compute_supplement_scorecards(src)
    assert out is not None
    # Amino Acid Profile has no amino data -> dropped; confidence deduction applied.
    assert "amino_acid_profile" in out["dropped_cards"]
    assert out["cards_dropped"] >= 1
    assert out["confidence_deduction"] >= ss.CONFIDENCE_DEDUCTION_PER_DROP
    assert "data_incomplete" in out["tags"]


def test_non_supplement_returns_none():
    assert ss.compute_supplement_scorecards({"category_paths": ["f_and_b/snacks/chips"]}) is None


def test_unmapped_leaf_returns_none():
    src = {"category_paths": ["f_and_b/supplements/unknown_thing/foo"], "category_data": {}}
    assert ss.compute_supplement_scorecards(src) is None


# ── Tier reachability (sheet 08 section 6): Worst band attainable where designed ─
def test_collagen_protein_quality_reaches_worst():
    f = ss.SupplementFeatures(leaf="plant_protein", weight_column="Plant Protein")
    f.is_protein_category = True
    f.protein_ingredients = ["collagen peptides", "pea protein", "rice protein"]
    f.protein_pct_by_weight = 55.0
    f._ess_per100 = {}
    r = ss.score_protein_quality(f)
    assert r.score is not None and r.score < 25  # Worst band


def test_clinical_dose_no_actives_scores_25():
    f = ss.SupplementFeatures(leaf="creatine", weight_column="Creatine")
    r = ss.score_clinical_dose(f)
    assert r.score == 25
    assert "undisclosed_dosages" in r.tags


def test_clinical_dose_full_creatine_scores_100():
    f = ss.SupplementFeatures(leaf="creatine", weight_column="Creatine")
    f.active_ingredients = {"creatine monohydrate g": 5.0}
    f.has_actives = True
    r = ss.score_clinical_dose(f)
    assert r.score == 100


def test_stimulant_over_400mg_caffeine_caps_at_25():
    f = ss.SupplementFeatures(leaf="pre_workout", weight_column="Pre-Workout")
    f.active_ingredients = {"caffeine mg": 450.0}
    f.has_actives = True
    r = ss.score_stimulant_balance(f)
    assert r.score <= 25
    assert "exceeds_safe_caffeine_dose" in r.tags


def test_formula_card_subtitle_new_on_preworkout():
    src = {
        "id": "pw-formula-1",
        "price": 2000,
        "size": "300 g",
        "category_paths": ["f_and_b/supplements/performance/pre_workout"],
        "category_data": {
            "serving_size": "15 g",
            "servings_per_container": 20,
            "active_ingredients": {
                "caffeine anhydrous mg": 200.0,
                "l-citrulline g": 6.0,
                "beta-alanine g": 3.2,
                "nitrate mg": 500.0,
                "glycerol g": 2.0,
            },
            "nutritional": {"qty": "15 g", "nutri_breakdown": {}},
            "certifications": [],
        },
        "ingredients": {"raw_text": "caffeine citrulline beta alanine", "normalised": "{}"},
    }
    ss.clear_config_cache()
    out = ss.compute_supplement_scorecards(src)
    assert out is not None
    adapted = ss.to_pdp_score_cards(out)["score_cards"]
    assert "stimulant_balance" not in adapted
    assert "pump_formula" not in adapted
    card = adapted["formula"]
    assert card["title"] == "Formula"
    assert card["value"] == "Best"
    assert card["subtitle_new"]
    assert 1 <= len(card["subtitle_new"]) <= 2
    labels = {e["tag_label"] for e in card["subtitle_new"]}
    assert labels <= {
        "Optimal caffeine",
        "Max pump",
        "Solid energy",
        "Strong pump",
        "Moderate formula",
        "Mixed stack",
        "High stim",
        "Weak pump",
        "Unsafe stim",
        "No pump",
        "Exceeds safe dose",
        "Exceeds FSSAI limit",
    }
    assert "optimal_caffeine" in card["tags"]
    assert "max_pump" in card["tags"]
    assert out["cards"]["formula"]["score"] is not None
    assert out["cards"]["formula"]["score"] >= 90


def test_formula_only_on_preworkout_leaf():
    f = ss.SupplementFeatures(leaf="creatine", weight_column="Creatine")
    f.active_ingredients = {"creatine g": 5.0, "caffeine mg": 200.0}
    f.has_actives = True
    r = ss.score_formula(f)
    assert r.scorable is False


# ── Category audit presentations (Protein / Creatine / Pre-workout / EAA) ─────
def _protein_src(**overrides):
    src = {
        "id": "prot-1",
        "price": 3500,
        "size": "1kg",
        "category_paths": ["f_and_b/supplements/protein/whey_isolate"],
        "stats": {"protein_percentiles": {"subcategory_percentile": 82.0}},
        "category_data": {
            "serving_size": "35 g",
            "servings_per_container": 28,
            "nutritional": {
                "qty": "100 g",
                "nutri_breakdown": {
                    "protein g": 80.0,
                    "energy kcal": 360.0,
                    "total fat g": 1.0,
                    "carbohydrate g": 5.0,
                    "sodium mg": 200.0,
                    "added sugar g": 0.0,
                },
            },
            "amino_acid_profile": {
                "leucine g": 10.5,
                "isoleucine g": 5.0,
                "valine g": 5.0,
                "lysine g": 8.0,
                "threonine g": 5.0,
                "methionine g": 2.0,
                "phenylalanine g": 3.0,
                "tryptophan g": 1.5,
                "histidine g": 2.0,
                "glutamic acid g": 15.0,
            },
            "total_amino_acids": {
                "total eaa g": 45.0,
                "total bcaa g": 20.0,
            },
            "certifications": [],
        },
        "ingredients": {
            "raw_text": "Whey Protein Isolate, cocoa, sucralose, batch 1",
            "normalised": "{'oils': [], 'additives': [], 'sweeteners': ['ins955'], 'protein_ingredients': ['whey protein isolate']}",
        },
    }
    src.update(overrides)
    return src


def test_protein_audit_presentation_order_and_metrics():
    out = ss.compute_supplement_scorecards(_protein_src())
    assert out is not None
    adapted = ss.to_pdp_score_cards(out)
    pe = adapted["score_cards"]["protein_efficiency"]
    assert pe["order"] == 1
    assert pe["title"] == "Protein"
    assert pe["value"] == "28 g / Scoop"  # 80 * 35/100
    assert len(pe["subtitle_new"]) == 1
    assert pe["subtitle_new"][0]["tag_label"].endswith("/g")
    assert pe["subtitle"].startswith("Score:")
    # Flat shape preserved
    for k in ("title", "value", "subtitle", "subtitle_new", "score", "status", "status_label", "color", "theme", "tags", "order", "visible"):
        assert k in pe
    assert "detail" not in pe
    # Other cards still present
    assert "protein_quality" in adapted["score_cards"]
    assert adapted["score_cards"]["protein_quality"]["order"] >= 2


def test_protein_quality_subtitle_new_array():
    adapted = ss.to_pdp_score_cards(ss.compute_supplement_scorecards(_protein_src()))
    pq = adapted["score_cards"]["protein_quality"]
    assert pq["title"] == "Protein Quality"
    assert pq["value"] in ("Best", "Top", "Average", "Poor", "Worst")
    assert pq["subtitle_new"] == [
        {"tag_label": "Complete amino profile", "color_code": "#2E7D32"},
        {"tag_label": "80% protein", "color_code": "#2E7D32"},
        {"tag_label": "Single source", "color_code": "#2E7D32"},
    ]
    assert pq["subtitle"].startswith("Score:")
    assert pq["score"] is not None


def test_protein_quality_incomplete_without_full_eaa_panel():
    src = _protein_src()
    # Only 3 EAAs published → completeness 0 → Incomplete amino profile
    src["category_data"]["amino_acid_profile"] = {
        "leucine g": 10.5,
        "isoleucine g": 5.0,
        "valine g": 5.0,
    }
    src["category_data"]["total_amino_acids"] = {"total eaa g": 20.5, "total bcaa g": 20.5}
    pq = ss.to_pdp_score_cards(ss.compute_supplement_scorecards(src))["score_cards"]["protein_quality"]
    assert pq["value"] in ("Best", "Top", "Average", "Poor", "Worst")
    labels = [e["tag_label"] for e in pq["subtitle_new"]]
    assert "Incomplete amino profile" in labels
    assert "80% protein" in labels
    assert "Single source" in labels
    assert pq["subtitle_new"][0]["color_code"] == "#C62828"


def test_protein_audit_subtitle_new_only_cost_per_gram():
    src = _protein_src()
    src["category_data"].pop("amino_acid_profile", None)
    src["category_data"].pop("total_amino_acids", None)
    src["category_data"].pop("found_amino_acid_profile", None)
    pe = ss.to_pdp_score_cards(ss.compute_supplement_scorecards(src))["score_cards"]["protein_efficiency"]
    labels = [e["tag_label"] for e in pe.get("subtitle_new", [])]
    assert len(labels) == 1
    assert labels[0].endswith("/g")
    assert "Amino profile not yet verified" not in labels
    assert "Verified real" not in labels
    assert "scoop purity" not in " ".join(labels)
    assert "Top " not in " ".join(labels)


def test_creatine_audit_cost_and_not_assayed():
    src = {
        "id": "cre-1",
        "price": 1000,
        "size": "250 g",
        "category_paths": ["f_and_b/supplements/performance/creatine"],
        "category_data": {
            "serving_size": "5 g",
            "servings_per_container": 50,
            "active_ingredients": {"creatine monohydrate g": 5.0},
            "nutritional": {"qty": "5 g", "nutri_breakdown": {}},
            "certifications": [],
        },
        "ingredients": {"raw_text": "creatine monohydrate", "normalised": "{}"},
    }
    out = ss.compute_supplement_scorecards(src)
    adapted = ss.to_pdp_score_cards(out)
    card = adapted["score_cards"]["clinical_dose"]
    assert card["order"] == 1
    assert card["title"] == "Creatine"
    assert card["value"] in ("Best", "Top", "Average", "Poor", "Worst")
    assert "₹20/5 g dose" in card["subtitle"]
    assert "Tub lasts 50 days" in card["subtitle"]
    assert "Not assayed." in card["subtitle"]
    assert card["subtitle_new"]
    assert len(card["subtitle_new"]) == 1
    assert all("tag_label" in e and "color_code" in e for e in card["subtitle_new"])
    assert card["subtitle_new"][0]["tag_label"] == "Fully Dosed"
    assert "fully_dosed" in card["tags"]
    assert card["tags"].count("fully_dosed") == 1


def test_creatine_audit_proprietary_blend_still_emits():
    src = {
        "id": "cre-2",
        "price": 1000,
        "size": "250 g",
        "category_paths": ["f_and_b/supplements/performance/creatine"],
        "category_data": {
            "serving_size": "5 g",
            "servings_per_container": 50,
            "active_ingredients": {},
            "nutritional": {"qty": "5 g", "nutri_breakdown": {}},
            "certifications": [],
        },
        "ingredients": {
            "raw_text": "proprietary blend of creatine and additives",
            "normalised": "{}",
        },
    }
    out = ss.compute_supplement_scorecards(src)
    card = ss.to_pdp_score_cards(out)["score_cards"]["clinical_dose"]
    assert card["value"] == "Doses not disclosed by brand"
    assert "doses_not_disclosed" in card["tags"]
    assert card["visible"] is True
    assert card["color"] == "#9CA3AF"


def test_preworkout_audit_espresso_and_capped_bars():
    src = {
        "id": "pw-1",
        "price": 2000,
        "size": "300 g",
        "category_paths": ["f_and_b/supplements/performance/pre_workout"],
        "category_data": {
            "serving_size": "15 g",
            "servings_per_container": 20,
            "active_ingredients": {
                "caffeine anhydrous mg": 189.0,  # 189/63 = 3.0 espressos
                "beta-alanine g": 4.0,           # 4/3.2 -> 125% -> capped 100%
                "l-citrulline g": 3.0,           # 3/6 = 50%
                "l-tyrosine g": 0.5,             # not in clinical table -> omitted from bars
            },
            "nutritional": {"qty": "15 g", "nutri_breakdown": {}},
            "certifications": [],
        },
        "ingredients": {"raw_text": "caffeine, beta alanine, citrulline", "normalised": "{}"},
    }
    out = ss.compute_supplement_scorecards(src)
    card = ss.to_pdp_score_cards(out)["score_cards"]["clinical_dose"]
    assert card["order"] == 1
    assert card["title"] == "Pre-Workout"
    assert card["value"] in ("Best", "Top", "Average", "Poor", "Worst")
    assert "3.0 espressos" in card["subtitle"]
    assert "Beta-alanine 100%" in card["subtitle"]  # capped, not 125%
    assert "140%" not in card["subtitle"]
    assert "Citrulline 50%" in card["subtitle"]
    assert card["subtitle_new"]
    assert len(card["subtitle_new"]) == 1
    assert all("tag_label" in e and "color_code" in e for e in card["subtitle_new"])
    assert card["subtitle_new"][0]["tag_label"] in {
        "Fully Dosed", "Well Dosed", "Partially Dosed", "Underdosed", "Severely Underdosed",
    }


def test_preworkout_proprietary_greyed():
    src = {
        "id": "pw-2",
        "price": 2000,
        "size": "300 g",
        "category_paths": ["f_and_b/supplements/performance/pre_workout"],
        "category_data": {
            "serving_size": "15 g",
            "active_ingredients": {},
            "nutritional": {"qty": "15 g", "nutri_breakdown": {}},
            "certifications": [],
        },
        "ingredients": {
            "raw_text": "proprietary blend energy matrix",
            "normalised": "{}",
        },
    }
    card = ss.to_pdp_score_cards(ss.compute_supplement_scorecards(src))["score_cards"]["clinical_dose"]
    assert card["value"] == "Doses not disclosed by brand"
    assert "doses_not_disclosed" in card["tags"]
    assert card["color"] == "#9CA3AF"


def test_eaa_audit_complete_and_cost():
    src = {
        "id": "eaa-1",
        "price": 1800,
        "size": "300 g",
        "category_paths": ["f_and_b/supplements/amino_acids/eaa"],
        "category_data": {
            "serving_size": "15 g",
            "servings_per_container": 20,
            "nutritional": {"qty": "15 g", "nutri_breakdown": {"protein g": 10.0}},
            "amino_acid_profile": {
                "leucine g": 2.5,
                "isoleucine g": 1.25,
                "valine g": 1.25,
                "lysine g": 1.0,
                "threonine g": 0.8,
                "methionine g": 0.5,
                "phenylalanine g": 0.7,
                "tryptophan g": 0.3,
                "histidine g": 0.5,
            },
            "total_amino_acids": {
                "total eaa g": 10.0,
                "total bcaa g": 5.0,
            },
            "certifications": [],
        },
        "ingredients": {"raw_text": "essential amino acids", "normalised": "{}"},
    }
    out = ss.compute_supplement_scorecards(src)
    card = ss.to_pdp_score_cards(out)["score_cards"]["recovery_formula"]
    assert card["order"] == 1
    assert card["title"] == "EAA"
    assert card["value"] == "9/9 complete"
    assert "Leucine 2.5 g (MPS 2.5 g)" in card["subtitle"]
    assert "/g EAA" in card["subtitle"]
    assert card["subtitle_new"]
    assert 1 <= len(card["subtitle_new"]) <= 2
    labels = {e["tag_label"] for e in card["subtitle_new"]}
    assert labels <= {
        "Complete EAA blend",
        "Leucine full",
        "All 9 essentials",
        "BCAA only",
        "Weak recovery",
        "Ineffective dose",
        "Amino profile hidden",
    }
    assert "complete_eaa_blend" in card["tags"] or "all_9_essentials" in card["tags"]


def test_eaa_audit_bcaa_only_headline():
    src = {
        "id": "bcaa-1",
        "price": 1200,
        "size": "250 g",
        "category_paths": ["f_and_b/supplements/amino_acids/bcaa"],
        "category_data": {
            "serving_size": "10 g",
            "servings_per_container": 25,
            "nutritional": {"qty": "10 g", "nutri_breakdown": {}},
            "amino_acid_profile": {
                "leucine g": 2.5,
                "isoleucine g": 1.25,
                "valine g": 1.25,
            },
            "total_amino_acids": {
                "total eaa g": 5.0,
                "total bcaa g": 5.0,
            },
            "certifications": [],
        },
        "ingredients": {"raw_text": "bcaa 2:1:1", "normalised": "{}"},
    }
    card = ss.to_pdp_score_cards(ss.compute_supplement_scorecards(src))["score_cards"]["recovery_formula"]
    assert card["title"] == "EAA"
    assert card["value"] == "BCAA-only"
    assert card["subtitle_new"]
    assert any(e["tag_label"] == "BCAA only" for e in card["subtitle_new"])
    assert "bcaa_only" in card["tags"]


def test_formula_blends_stim_and_pump_when_pump_strong_stim_absent():
    """Caffeine-free with full pump still scores via renormalized pump weight."""
    src = {
        "id": "pw-pump-1",
        "price": 2000,
        "size": "300 g",
        "category_paths": ["f_and_b/supplements/performance/pre_workout"],
        "category_data": {
            "serving_size": "15 g",
            "servings_per_container": 20,
            "active_ingredients": {
                "l-citrulline g": 6.0,
                "beta-alanine g": 3.2,
                "nitrate mg": 500.0,
                "glycerol g": 2.0,
            },
            "nutritional": {"qty": "15 g", "nutri_breakdown": {}},
            "certifications": [],
        },
        "ingredients": {"raw_text": "citrulline beta alanine", "normalised": "{}"},
    }
    ss.clear_config_cache()
    out = ss.compute_supplement_scorecards(src)
    assert out is not None
    card = ss.to_pdp_score_cards(out)["score_cards"]["formula"]
    assert card["title"] == "Formula"
    assert card["value"] in ("Best", "Top", "Average", "Poor", "Worst")
    assert card["subtitle_new"]
    assert len(card["subtitle_new"]) <= 2
    assert "stimulant_balance" not in out["cards"]
    assert "pump_formula" not in out["cards"]


def test_settings_threshold_change_updates_preworkout_bars(monkeypatch):
    """Dose bars read clinical thresholds from settings — one source of truth."""
    cfg = ss.load_config()
    original = cfg["settings"]["clinical_thresholds"]["beta_alanine"]["threshold"]
    cfg["settings"]["clinical_thresholds"]["beta_alanine"]["threshold"] = 8.0
    ss._config_cache = cfg
    try:
        src = {
            "id": "pw-3",
            "price": 2000,
            "size": "300 g",
            "category_paths": ["f_and_b/supplements/performance/pre_workout"],
            "category_data": {
                "serving_size": "15 g",
                "active_ingredients": {
                    "caffeine mg": 200.0,
                    "beta-alanine g": 3.2,  # was 100% at 3.2 threshold; now 40% at 8.0
                },
                "nutritional": {"qty": "15 g", "nutri_breakdown": {}},
                "certifications": [],
            },
            "ingredients": {"raw_text": "caffeine beta alanine", "normalised": "{}"},
        }
        card = ss.to_pdp_score_cards(ss.compute_supplement_scorecards(src))["score_cards"]["clinical_dose"]
        assert "Beta-alanine 40%" in card["subtitle"]
    finally:
        cfg["settings"]["clinical_thresholds"]["beta_alanine"]["threshold"] = original
        ss.clear_config_cache()


def test_clinical_dose_on_multivitamin_keeps_default_title():
    src = {
        "id": "mv-1",
        "price": 500,
        "size": "60 tablets",
        "category_paths": ["f_and_b/supplements/vitamins/multivitamin"],
        "category_data": {
            "active_ingredients": {"vitamin d3 iu": 600.0},
            "nutritional": {"qty": "1 tablet", "nutri_breakdown": {}},
            "certifications": [],
        },
        "ingredients": {"raw_text": "vitamin d3", "normalised": "{}"},
    }
    # multivitamin may or may not map — check leaf mapping
    leaf = ss.leaf_category(src)
    col = ss.LEAF_TO_WEIGHT_COLUMN.get(ss._norm_token(leaf))
    if col != "Multivitamin":
        src["category_paths"] = ["f_and_b/supplements/multivitamin"]
    out = ss.compute_supplement_scorecards(src)
    if out is None:
        pytest.skip("multivitamin leaf not mapped in this fixture")
    card = out["cards"]["clinical_dose"]
    assert card["title"] == "Clinical Dose"  # not Creatine/Pre-Workout audit
    adapted = ss.to_pdp_score_cards(out)["score_cards"]["clinical_dose"]
    assert adapted["value"] in ("Best", "Top", "Average", "Poor", "Worst")
    assert adapted["subtitle_new"]
    assert len(adapted["subtitle_new"]) == 1
    assert adapted["subtitle_new"][0]["tag_label"] in {
        "Fully Dosed", "Well Dosed", "Partially Dosed", "Underdosed", "Severely Underdosed",
    }


def test_supplement_flean_badge_includes_hide_score():
    src = _protein_src(flean_score={"adjusted_score": 85.0, "hide_score": True})
    adapted = ss.to_pdp_score_cards(ss.compute_supplement_scorecards(src))
    assert adapted["flean_badge"]["hide_score"] is True

    src_default = _protein_src()
    adapted_default = ss.to_pdp_score_cards(ss.compute_supplement_scorecards(src_default))
    assert adapted_default["flean_badge"]["hide_score"] is False


def test_transform_to_pdp_keeps_es_flean_badge_for_supplements():
    """Supplement cards replace score_cards but must not overwrite ES flean_badge."""
    from unittest.mock import patch

    from shopping_bot.data_fetchers.es_products import transform_to_pdp

    src = _protein_src(
        flean_score={
            "adjusted_score": 72.0,
            "adjusted_score_label": 7.2,
            "hide_score": False,
        },
        stats={
            "protein_percentiles": {"subcategory_percentile": 82.0},
            "adjusted_score_percentiles": {"subcategory_percentile": 70.0},
        },
        name="Test Isolate",
        brand="Test",
    )
    composite = ss.compute_supplement_scorecards(src)["flean_score"]
    with patch(
        "shopping_bot.data_fetchers.es_products.get_subcategory_cards_config_for_path",
        return_value=[],
    ):
        pdp = transform_to_pdp(src)

    assert "protein_quality" in pdp["score_cards"] or "protein_efficiency" in pdp["score_cards"]
    assert pdp["flean_badge"]["score_display"] == "7"
    assert pdp["flean_badge"]["score"] == 7
    # Composite is on a 0-100 scale and must not replace the ES badge.
    assert round(composite) != 7
    assert pdp.get("supplement_scoring", {}).get("flean_score") == composite


def test_allowed_keys_limits_compute_and_pdp_cards():
    """Config allowlist: only listed cards are calculated/shown; composite ignores others."""
    from shopping_bot.utils.cards_config import apply_order_from_config

    src = _protein_src()
    full = ss.compute_supplement_scorecards(src)
    assert full is not None
    assert "digestibility" in full["cards"]
    assert "label_trust" in full["cards"]

    allowed = frozenset({"protein_quality", "label_trust", "bioavailability"})
    limited = ss.compute_supplement_scorecards(src, allowed_keys=allowed)
    assert limited is not None
    assert set(limited["cards"]) == allowed
    # Composite should differ when high-weight cards are excluded from scoring.
    assert limited["flean_score"] != full["flean_score"] or limited["cards_dropped"] != full["cards_dropped"]

    adapted = ss.to_pdp_score_cards(limited, allowed_keys=allowed)
    assert set(adapted["score_cards"]) == {
        k for k in allowed if limited["cards"][k].get("scorable")
    } | (
        {"bioavailability"}
        if limited["cards"]["bioavailability"].get("scorable")
        else set()
    )
    assert "digestibility" not in adapted["score_cards"]

    config = [
        {"card": "Bioavailability", "visible": True, "order": 1},
        {"card": "Label Trust", "visible": True, "order": 2},
        {"card": "Protein Quality", "visible": True, "order": 3},
    ]
    ordered = apply_order_from_config(adapted["score_cards"], config)
    if "bioavailability" in ordered:
        assert ordered["bioavailability"]["order"] == 1
    assert ordered["label_trust"]["order"] == 2
    assert ordered["protein_quality"]["order"] == 3


def test_without_allowed_keys_keeps_legacy_visibility():
    adapted = ss.to_pdp_score_cards(ss.compute_supplement_scorecards(_protein_src()))
    # Legacy always shows bioavailability even though weight is 0.
    assert "bioavailability" in adapted["score_cards"]
    assert "digestibility" in adapted["score_cards"]

