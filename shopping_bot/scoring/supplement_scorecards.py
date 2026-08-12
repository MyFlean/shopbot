"""Flean Supplement Scorecard framework v2.0 - runtime computation.

Computes the 15 supplement scorecards defined in the v2 framework workbook
(``Flean_Supplement_Scorecard_Framework_v2``) directly from a product's
Elasticsearch ``_source`` document, at PDP-transform time.

Design
------
Unlike the food/beverage scorecards (which read pre-computed percentiles from
``stats.*_percentiles``), supplement cards are *absolute-threshold formulas*
evaluated from raw label fields (nutrition panel, amino-acid profile, actives,
certifications, parsed ingredient lists). Each card returns a 0-100 score; a
per-leaf-category weight vector (sheet ``02 Category Weights``) combines the
applicable cards into the composite Flean Score.

Every numeric table here is transcribed from, or generated from, the workbook.
Structured tables (weights, DIAAS source ladder, clinical-dose reference, tier
tag lists) live in ``data/config/supplement_scoring_v2.json``. The free-text
threshold ladders (sheet ``01 Scorecards`` column L and sheet ``03 Scoring
Spec``) are encoded as constants below with sheet citations, because parsing
them from prose is error-prone.

The public entrypoint is :func:`compute_supplement_scorecards`.
"""

from __future__ import annotations

import ast
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Collection, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "config" / "supplement_scoring_v2.json"

# ── Tier mapping (README sheet: 90-100 Best | 75-89 Top | 50-74 Average | 25-49 Poor | 0-24 Worst)
# Colors kept consistent with es_products.SCORE_TIERS so the Flutter UI renders identically.
SCORE_TIERS: Tuple[Tuple[int, str, str, str], ...] = (
    (90, "elite", "Best", "#81A18C"),
    (75, "top", "Top", "#8FAF9A2E"),
    (50, "average", "Average", "#F2E9BB80"),
    (25, "subpar", "Poor", "#FFF3EF"),
    (0, "villain", "Worst", "#EF4444"),
)
_TIER_KEY_BY_LABEL = {"Best": "best", "Top": "top", "Average": "average", "Poor": "poor", "Worst": "worst"}


def tier_for_score(score: float) -> Dict[str, str]:
    """Map a 0-100 score to a tier band (status/label/color/theme)."""
    s = max(0.0, min(100.0, float(score)))
    for threshold, status, label, color in SCORE_TIERS:
        if s >= threshold:
            return {"status": status, "label": label, "color": color, "theme": status, "tier_key": _TIER_KEY_BY_LABEL[label]}
    return {"status": "villain", "label": "Worst", "color": "#EF4444", "theme": "villain", "tier_key": "worst"}


# ── Leaf category slug -> weight column (sheet 02) ────────────────────────────
LEAF_TO_WEIGHT_COLUMN: Dict[str, str] = {
    "whey_isolate": "Whey Isolate",
    "whey_hydro": "Whey Isolate",          # hydrolysate: same isolate-tier weights
    "whey_hydrolysate": "Whey Isolate",
    "whey_concentrate": "Whey Concentrate",
    "whey_blend": "Whey Blend",
    "whey": "Whey Concentrate",
    "casein": "Casein",
    "micellar_casein": "Casein",
    "plant_protein": "Plant Protein",
    "plant": "Plant Protein",
    "vegan_protein": "Plant Protein",
    "mass_gainer": "Mass Gainer",
    "gainer": "Mass Gainer",
    "creatine": "Creatine",
    "pre_workout": "Pre-Workout",
    "preworkout": "Pre-Workout",
    "bcaa": "BCAA",
    "eaa": "EAA",
    "intra_workout": "EAA",                # EAA-centric recovery weights
    "multivitamin": "Multivitamin",
    "omega_3": "Omega-3 / Fish Oil",
    "omega3": "Omega-3 / Fish Oil",
    "fish_oil": "Omega-3 / Fish Oil",
}
PROTEIN_LEAVES = frozenset(
    {"Whey Isolate", "Whey Concentrate", "Whey Blend", "Casein", "Plant Protein", "Mass Gainer"}
)

# Source-type descriptor tokens embedded in sheet-01 tier tag columns, replaced
# at runtime by the actually-mapped source so an isolate is never mislabelled.
SOURCE_DESCRIPTOR_TOKENS = frozenset(
    {"whey_isolate", "whey_concentrate", "whey_blend", "casein", "plant_protein", "collagen_only"}
)
SOURCE_NAME_TO_TOKEN: Dict[str, str] = {
    "Whey Protein Isolate": "whey_isolate",
    "Whey Protein Hydrolysate": "whey_hydrolysate",
    "Whey Protein Concentrate (80%)": "whey_concentrate",
    "Whey Concentrate (<70% protein)": "whey_concentrate",
    "Milk Protein Concentrate / Blend": "milk_protein_blend",
    "Egg White Protein": "egg_white_protein",
    "Micellar Casein": "micellar_casein",
    "Calcium / Sodium Caseinate": "caseinate",
    "Soy Protein Isolate": "soy_isolate",
    "Pea + Rice blend (70:30)": "pea_rice_blend",
    "Pea Protein Isolate": "pea_isolate",
    "Soy Protein Concentrate": "soy_concentrate",
    "Hemp Protein": "hemp_protein",
    "Brown Rice Protein": "brown_rice_protein",
    "Wheat / Gluten Protein": "wheat_protein",
    "Collagen Peptides": "collagen_only",
    "Gelatin": "gelatin",
    "Undisclosed 'protein blend'": "undisclosed_protein_blend",
}

# ── Card key <-> display name ─────────────────────────────────────────────────
CARD_KEY_TO_NAME: Dict[str, str] = {
    "protein_quality": "Protein Quality",
    "amino_acid_profile": "Amino Acid Profile",
    "protein_efficiency": "Protein Efficiency",
    "bioavailability": "Bioavailability",
    "digestibility": "Digestibility",
    "label_trust": "Label Trust",
    "heavy_metals": "Heavy metals",
    "sweeteners": "Sweeteners",
    "serving_honesty": "Servings",
    "clinical_dose": "Clinical Dose",
    "formula": "Formula",
    "recovery_formula": "Recovery Formula",
}
NAME_TO_CARD_KEY = {v: k for k, v in CARD_KEY_TO_NAME.items()}
# Legacy flean_card_config / workbook names.
NAME_TO_CARD_KEY["Serving Honesty"] = "serving_honesty"
NAME_TO_CARD_KEY["Stimulant Balance"] = "formula"
NAME_TO_CARD_KEY["Pump Formula"] = "formula"


# ── Threshold ladders (sheet 01 col L / sheet 03). Interpolated ladders. ──────
# Each ladder is a list of (x, score) anchors ascending in x. Values below the
# first anchor clamp to `below`; above the last clamp to the last score.
@dataclass(frozen=True)
class _Ladder:
    anchors: Tuple[Tuple[float, float], ...]
    below: float

    def score(self, x: Optional[float]) -> Optional[float]:
        if x is None:
            return None
        first_x, _ = self.anchors[0]
        if x < first_x:
            return self.below
        last_x, last_s = self.anchors[-1]
        if x >= last_x:
            return last_s
        for (x0, s0), (x1, s1) in zip(self.anchors, self.anchors[1:]):
            if x0 <= x <= x1:
                if x1 == x0:
                    return s1
                frac = (x - x0) / (x1 - x0)
                return s0 + frac * (s1 - s0)
        return last_s


PROTEIN_PCT_LADDER = _Ladder(((55, 25), (60, 42), (65, 54), (70, 66), (75, 78), (80, 88), (85, 100)), below=25)
LEUCINE_PER25_LADDER = _Ladder(((2.00, 40), (2.20, 55), (2.35, 70), (2.50, 85), (2.60, 92), (2.75, 100)), below=25)
EAA_PER25_LADDER = _Ladder(((8.0, 45), (9.0, 60), (10.0, 75), (10.5, 85), (11.0, 92), (11.5, 100)), below=30)
BCAA_PER25_LADDER = _Ladder(((4.0, 55), (4.4, 70), (4.8, 80), (5.2, 90), (5.6, 100)), below=40)
PROTEIN_PER_100KCAL_LADDER = _Ladder(
    ((10, 38), (12, 50), (14, 60), (16, 70), (18, 80), (20, 90), (21, 95), (22, 100)), below=25
)
# Recovery Formula (sheet 01 row 15): absolute per-serving doses.
RECOVERY_LEUCINE_LADDER = _Ladder(((1.4, 28), (1.7, 62), (2.0, 78), (2.2, 88), (2.5, 100)), below=28)
RECOVERY_EAA_LADDER = _Ladder(((4, 25), (5, 62), (6, 78), (8, 90), (10, 100)), below=25)
# Pump Formula (sheet 01 row 14).
PUMP_CITRULLINE_LADDER = _Ladder(((2, 35), (3, 52), (4, 70), (5, 85), (6, 100)), below=18)
PUMP_BETA_ALANINE_LADDER = _Ladder(((1.6, 25), (2.0, 60), (2.4, 75), (2.8, 88), (3.2, 100)), below=25)
# Dose-ratio ladder (sheet 03 rows 16-23): declared / clinical threshold.
DOSE_RATIO_LADDER = _Ladder(((0.35, 15), (0.50, 50), (0.60, 62), (0.70, 72), (0.80, 82), (0.90, 90), (1.00, 100)), below=15)


# ── Sweetener anchors (sheet 01 row 11). Lower = worse. ───────────────────────
SWEETENER_ANCHOR: Dict[str, float] = {
    "stevia": 97, "reba": 97, "steviol": 97,
    "monk_fruit": 97, "monkfruit": 97, "luo_han_guo": 97,
    "thaumatin": 95,
    "erythritol": 88,
    "xylitol": 85, "maltitol": 85, "sorbitol": 85,
    "neotame": 72, "ins961": 72,
    "sucralose": 72, "ins955": 72,
    "acesulfame": 72, "acesulfame_k": 72, "ins950": 72, "ace_k": 72,
    "saccharin": 66, "ins954": 66,
    "aspartame": 65, "ins951": 65,
}
# Canonical display names for PDP value (match the scoring ladder labels).
SWEETENER_DISPLAY_NAME: Dict[str, str] = {
    "stevia": "Stevia", "reba": "Stevia", "steviol": "Stevia",
    "monk_fruit": "Monk fruit", "monkfruit": "Monk fruit", "luo_han_guo": "Monk fruit",
    "erythritol": "Erythritol",
    "xylitol": "Xylitol", "maltitol": "Maltitol", "sorbitol": "Sorbitol",
    "neotame": "Neotame", "ins961": "Neotame",
    "sucralose": "Sucralose", "ins955": "Sucralose",
    "acesulfame": "Ace-K", "acesulfame_k": "Ace-K", "ins950": "Ace-K", "ace_k": "Ace-K",
    "saccharin": "Saccharin", "ins954": "Saccharin",
    "aspartame": "Aspartame", "ins951": "Aspartame",
}
# Non-nutritive sweeteners that count toward the stacking penalty / caution tags.
ARTIFICIAL_SWEETENER_INS = frozenset({"ins950", "ins951", "ins954", "ins955", "ins961"})

_SWEETENER_NEGATIVE_TAGS = frozenset({
    "contains_artificial_sweeteners",
    "multiple_artificial_sweeteners",
    "high_artificial_sweetener_load",
    "high_added_sugar",
})
_SWEETENER_POSITIVE_TAGS = frozenset({
    "unsweetened",
    "stevia_sweetened",
    "monk_fruit_sweetened",
    "naturally_sweetened",
    "low_artificial_sweetener_load",
    "single_natural_sweetener",
})

# Hydrocolloid gums penalised in Digestibility (sheet 06: lecithin INS322 excluded).
HYDROCOLLOID_GUM_INS = frozenset({"ins412", "ins415", "ins417", "ins466"})

# ── Certification -> Testing & Trust credit (sheet 01 row 10). ────────────────
# (points, is_third_party). Names normalised to lowercase snake tokens.
CERT_CREDITS: Dict[str, Tuple[float, bool]] = {
    "nsf_certified_for_sport": (30, True), "nsf_sport": (30, True),
    "informed_sport": (28, True),
    "informed_choice": (26, True),
    "heavy_metal_tested": (25, True), "contaminant_panel": (25, True),
    "trustified_gold": (20, True),
    "labdoor": (18, True),
    "trustified": (14, True), "trustified_standard": (14, True),
    "batch_coa": (15, True), "coa": (15, True),
    "third_party_lab_tested": (10, True), "third_party_tested": (10, True),
    "gmp": (8, False), "iso_22000": (8, False), "iso_9001_2015": (8, False),
    "usfda_registered_facility": (8, False), "haccp": (8, False),
    "fssai": (5, False),
    "qr_authenticity": (5, False),
}
_THIRD_PARTY_CERTS = frozenset(k for k, (_p, tp) in CERT_CREDITS.items() if tp)

# FAO/WHO 2007 amino-acid scoring pattern (mg per g protein) for completeness (sheet 01 row 2).
FAO_PATTERN_MG_PER_G: Dict[str, float] = {
    "histidine": 15, "isoleucine": 30, "leucine": 59, "lysine": 45,
    "methionine": 22, "phenylalanine": 38, "threonine": 23, "tryptophan": 6, "valine": 39,
}
# Flat amino_acid_profile keys (e.g. "leucine g") used by newer ES docs.
_FLAT_EAA_KEYS = tuple(f"{aa} g" for aa in FAO_PATTERN_MG_PER_G)
_FLAT_NEAA_KEYS = (
    "alanine g", "arginine g", "aspartic acid g", "asparagine g", "cysteine g",
    "glutamic acid g", "glutamine g", "glycine g", "proline g", "serine g", "tyrosine g",
)
_FLAT_TOTAL_SKIP = frozenset({
    "qty", "total eaa g", "total_eaa_g", "total bcaa g", "total_bcaa_g",
    "total neaa g", "total_neaa_g", "total seaa g", "total_seaa_g",
    "essential_amino_acids_g", "non_essential_amino_acids_g",
    "conditionally_essential_amino_acids_g",
})

# Free amino acids whose presence as separate ingredients signals nitrogen spiking (sheet 03 row 41).
SPIKING_FREE_AMINOS = frozenset({"glycine", "taurine", "alanine", "creatine"})

# Missing-data / confidence policy (sheet 02 A24, README A9).
CONFIDENCE_DEDUCTION_PER_DROP = 3.0
CONFIDENCE_DEDUCTION_CAP = 12.0


# ══════════════════════════════════════════════════════════════════════════════
# Config loading
# ══════════════════════════════════════════════════════════════════════════════
_config_cache: Optional[Dict[str, Any]] = None


def load_config() -> Dict[str, Any]:
    global _config_cache
    if _config_cache is None:
        with _CONFIG_PATH.open(encoding="utf-8") as f:
            _config_cache = json.load(f)
    return _config_cache


def clear_config_cache() -> None:
    """Invalidate cached config (tests / hot-reload)."""
    global _config_cache
    _config_cache = None


def get_settings() -> Dict[str, Any]:
    return load_config().get("settings") or {}


def get_clinical_thresholds() -> Dict[str, Tuple[float, str, float]]:
    """active_key -> (threshold, unit, importance_weight) from settings."""
    raw = get_settings().get("clinical_thresholds") or {}
    out: Dict[str, Tuple[float, str, float]] = {}
    for key, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        out[key] = (
            float(entry.get("threshold", 0)),
            str(entry.get("unit", "g")),
            float(entry.get("weight", 1.0)),
        )
    return out


def get_clinical_label(active_key: str) -> str:
    entry = (get_settings().get("clinical_thresholds") or {}).get(active_key) or {}
    return str(entry.get("label") or active_key.replace("_", " ").title())


# ══════════════════════════════════════════════════════════════════════════════
# Parsing helpers
# ══════════════════════════════════════════════════════════════════════════════
def _num(x: Any) -> Optional[float]:
    """Coerce a value to float, tolerating strings like '80.63' or '100 g'."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    m = re.search(r"-?\d+(?:\.\d+)?", str(x))
    return float(m.group()) if m else None


def _parse_qty_grams(x: Any) -> Optional[float]:
    """Parse a quantity like '100 g', '32 g', '1kg' to grams."""
    if x is None:
        return None
    s = str(x).strip().lower()
    n = _num(s)
    if n is None:
        return None
    if "kg" in s:
        return n * 1000.0
    return n


def parse_normalised(raw: Any) -> Dict[str, List[str]]:
    """ingredients.normalised is stored as a python-repr / JSON string in ES."""
    obj: Any = raw
    if isinstance(raw, str):
        try:
            obj = json.loads(raw)
        except Exception:
            try:
                obj = ast.literal_eval(raw)
            except Exception:
                obj = {}
    if not isinstance(obj, dict):
        return {"oils": [], "additives": [], "sweeteners": [], "protein_ingredients": []}
    out: Dict[str, List[str]] = {}
    for k in ("oils", "additives", "sweeteners", "protein_ingredients"):
        v = obj.get(k) or []
        out[k] = [str(t).strip().lower() for t in v] if isinstance(v, list) else []
    return out


def _norm_token(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s).strip().lower()).strip("_")


def leaf_category(src: Dict[str, Any]) -> str:
    paths = src.get("category_paths") or []
    if not paths:
        return ""
    longest = max(paths, key=lambda p: len(str(p)) if p else 0)
    seg = str(longest).split("/")
    return seg[-1] if seg else ""


# ══════════════════════════════════════════════════════════════════════════════
# Feature extraction + derived metrics (sheet 06)
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class SupplementFeatures:
    leaf: str
    weight_column: Optional[str]
    is_protein_category: bool = False
    # nutrition (per serving)
    serving_qty_g: Optional[float] = None
    protein_g: Optional[float] = None
    energy_kcal: Optional[float] = None
    total_fat_g: Optional[float] = None
    carb_g: Optional[float] = None
    added_sugar_g: Optional[float] = None
    total_sugar_g: Optional[float] = None
    sodium_mg: Optional[float] = None
    # derived
    protein_pct_by_weight: Optional[float] = None
    protein_per_100kcal: Optional[float] = None
    leucine_per_25g_protein: Optional[float] = None
    eaa_per_25g_protein: Optional[float] = None
    bcaa_per_25g_protein: Optional[float] = None
    leucine_g_per_serving: Optional[float] = None
    eaa_g_per_serving: Optional[float] = None
    amino_acid_recovery: Optional[float] = None
    eaa_share_of_total_aa: Optional[float] = None
    eaa_count: int = 0
    has_amino_profile: bool = False
    leucine_pct_of_protein: Optional[float] = None
    eaa_pct_of_protein: Optional[float] = None
    # ingredients
    protein_ingredients: List[str] = field(default_factory=list)
    additives: List[str] = field(default_factory=list)
    sweeteners: List[str] = field(default_factory=list)
    raw_text: str = ""
    has_raw_text: bool = False
    active_ingredients: Dict[str, float] = field(default_factory=dict)
    has_actives: bool = False
    certifications: List[Dict[str, Any]] = field(default_factory=list)
    servings_per_container: Optional[float] = None
    price: Optional[float] = None
    scoop_stated: bool = False
    pack_weight_g: Optional[float] = None
    has_proprietary_blend: bool = False
    total_caffeine_mg: Optional[float] = None
    creatine_purity_verified: bool = False
    creatine_purity_pct: Optional[float] = None
    protein_percentile: Optional[float] = None


def extract_features(src: Dict[str, Any]) -> SupplementFeatures:
    cd = src.get("category_data") if isinstance(src.get("category_data"), dict) else {}
    nutritional = cd.get("nutritional") or {}
    nutri = nutritional.get("nutri_breakdown") or {}
    qty_basis = _parse_qty_grams(nutritional.get("qty")) or 100.0
    serving = _parse_qty_grams(cd.get("serving_size"))
    leaf = leaf_category(src)
    col = LEAF_TO_WEIGHT_COLUMN.get(_norm_token(leaf))

    f = SupplementFeatures(leaf=leaf, weight_column=col)
    f.is_protein_category = col in PROTEIN_LEAVES if col else False
    f.serving_qty_g = serving
    f.servings_per_container = _num(cd.get("servings_per_container"))
    f.price = _num(src.get("price"))
    f.pack_weight_g = _parse_qty_grams(src.get("size"))
    f.scoop_stated = serving is not None

    def per_serving(key: str) -> Optional[float]:
        v = _num(nutri.get(key))
        if v is None:
            return None
        if serving is None:
            return v  # panel already per-serving basis if no serving size
        return v * serving / qty_basis

    protein_per_basis = _num(nutri.get("protein g"))
    f.protein_g = per_serving("protein g")
    f.energy_kcal = per_serving("energy kcal")
    f.total_fat_g = per_serving("total fat g")
    f.carb_g = per_serving("carbohydrate g")
    f.added_sugar_g = per_serving("added sugar g")
    f.total_sugar_g = per_serving("total sugar g")
    f.sodium_mg = per_serving("sodium mg")

    if protein_per_basis and qty_basis:
        f.protein_pct_by_weight = protein_per_basis / qty_basis * 100.0
    if f.protein_g and f.energy_kcal:
        f.protein_per_100kcal = f.protein_g / f.energy_kcal * 100.0

    # Amino acids — canonical ES fields (config required_inputs):
    #   category_data.amino_acid_profile  (flat per-AA map, or legacy summary totals)
    #   category_data.total_amino_acids   (total eaa/bcaa/neaa/seaa)
    # Legacy fallback: found_amino_acid_profile (nested essential/non-essential dicts).
    profile = cd.get("amino_acid_profile") or {}
    totals = cd.get("total_amino_acids") or {}
    legacy = cd.get("found_amino_acid_profile") or {}
    if not isinstance(profile, dict):
        profile = {}
    if not isinstance(totals, dict):
        totals = {}
    if not isinstance(legacy, dict):
        legacy = {}

    aa_qty = (
        _parse_qty_grams(profile.get("qty"))
        or _parse_qty_grams(legacy.get("qty"))
        or qty_basis
    )

    # 1) Flat amino_acid_profile: {"leucine g": 2.45, "total eaa g": 11.4, ...}
    ess = {
        k: profile[k]
        for k in _FLAT_EAA_KEYS
        if k in profile and _num(profile.get(k)) is not None
    }
    neaa_dict: Dict[str, Any] = {
        k: profile[k]
        for k in _FLAT_NEAA_KEYS
        if k in profile and _num(profile.get(k)) is not None
    }
    if not neaa_dict and ess:
        neaa_dict = {
            k: v for k, v in profile.items()
            if isinstance(k, str)
            and k.endswith(" g")
            and k not in _FLAT_EAA_KEYS
            and k not in _FLAT_TOTAL_SKIP
            and _num(v) is not None
        }

    # 2) Nested dicts under amino_acid_profile (rare) or legacy found_* blob.
    if not ess:
        nested_ess = profile.get("essential_amino_acids") or legacy.get("essential_amino_acids") or {}
        if isinstance(nested_ess, dict):
            ess = dict(nested_ess)
    if not neaa_dict:
        nested_neaa = (
            profile.get("non_essential_amino_acids")
            or legacy.get("non_essential_amino_acids")
            or {}
        )
        if isinstance(nested_neaa, dict):
            neaa_dict = dict(nested_neaa)

    protein_100 = (protein_per_basis / qty_basis * 100.0) if (protein_per_basis and qty_basis) else None

    def per100_aa(v: Any) -> Optional[float]:
        n = _num(v)
        return (n / aa_qty * 100.0) if (n is not None and aa_qty) else None

    leucine_100 = per100_aa(ess.get("leucine g"))
    # Prefer total_amino_acids, then flat profile totals, then legacy/summary keys.
    eaa_100 = (
        per100_aa(totals.get("total eaa g"))
        or per100_aa(totals.get("total_eaa_g"))
        or per100_aa(profile.get("total eaa g"))
        or per100_aa(profile.get("total_eaa_g"))
        or per100_aa(profile.get("essential_amino_acids_g"))
        or per100_aa(legacy.get("total_eaa_g"))
        or per100_aa(legacy.get("total eaa g"))
    )
    if eaa_100 is None and ess:
        # Sum disclosed EAAs when a total isn't published.
        eaa_sum = sum(_num(v) or 0.0 for v in ess.values())
        eaa_100 = (eaa_sum / aa_qty * 100.0) if (aa_qty and eaa_sum > 0) else None
    bcaa_100 = (
        per100_aa(totals.get("total bcaa g"))
        or per100_aa(totals.get("total_bcaa_g"))
        or per100_aa(profile.get("total bcaa g"))
        or per100_aa(profile.get("total_bcaa_g"))
        or per100_aa(legacy.get("total_bcaa_g"))
        or per100_aa(legacy.get("total bcaa g"))
    )
    if bcaa_100 is None and ess:
        bcaa_sum = sum(
            _num(ess.get(k)) or 0.0
            for k in ("leucine g", "isoleucine g", "valine g")
        )
        bcaa_100 = (bcaa_sum / aa_qty * 100.0) if (aa_qty and bcaa_sum > 0) else None
    neaa_100 = None
    if neaa_dict:
        s = sum(_num(v) or 0 for v in neaa_dict.values())
        neaa_100 = s / aa_qty * 100.0 if aa_qty else None
    else:
        neaa_100 = (
            per100_aa(totals.get("total neaa g"))
            or per100_aa(totals.get("total_neaa_g"))
            or per100_aa(profile.get("total neaa g"))
            or per100_aa(profile.get("total_neaa_g"))
            or per100_aa(profile.get("non_essential_amino_acids_g"))
        )
    cond_100 = (
        per100_aa(totals.get("total seaa g"))
        or per100_aa(totals.get("total_seaa_g"))
        or per100_aa(profile.get("total seaa g"))
        or per100_aa(profile.get("total_seaa_g"))
        or per100_aa(profile.get("conditionally_essential_amino_acids_g"))
    )

    f.eaa_count = sum(1 for v in ess.values() if (_num(v) or 0) > 0)
    f.has_amino_profile = bool(ess) or eaa_100 is not None

    if protein_100:
        if leucine_100 is not None:
            f.leucine_per_25g_protein = leucine_100 / protein_100 * 25.0
            f.leucine_pct_of_protein = leucine_100 / protein_100 * 100.0
        if eaa_100 is not None:
            f.eaa_per_25g_protein = eaa_100 / protein_100 * 25.0
            f.eaa_pct_of_protein = eaa_100 / protein_100 * 100.0
        if bcaa_100 is not None:
            f.bcaa_per_25g_protein = bcaa_100 / protein_100 * 25.0
        total_aa_100 = sum(x for x in (eaa_100, neaa_100, cond_100) if x is not None) or None
        if total_aa_100:
            f.amino_acid_recovery = total_aa_100 / protein_100
            if eaa_100 is not None:
                f.eaa_share_of_total_aa = eaa_100 / total_aa_100
    if f.serving_qty_g and aa_qty:
        if leucine_100 is not None:
            f.leucine_g_per_serving = leucine_100 * f.serving_qty_g / 100.0
        if eaa_100 is not None:
            f.eaa_g_per_serving = eaa_100 * f.serving_qty_g / 100.0
    elif aa_qty and qty_basis and abs(aa_qty - qty_basis) < 1e-6:
        # Panel already on serving basis (common when nutritional.qty is the scoop).
        if leucine_100 is not None and f.leucine_g_per_serving is None:
            f.leucine_g_per_serving = _num(ess.get("leucine g"))
        if eaa_100 is not None and f.eaa_g_per_serving is None:
            f.eaa_g_per_serving = (
                _num(totals.get("total eaa g"))
                or _num(totals.get("total_eaa_g"))
                or _num(profile.get("total eaa g"))
                or _num(profile.get("total_eaa_g"))
                or _num(legacy.get("total_eaa_g"))
            )

    # Store detailed AA per-100 for completeness scoring (Protein Quality).
    f._ess_per100 = {  # type: ignore[attr-defined]
        _norm_token(k.replace(" g", "")): per100_aa(v) for k, v in ess.items()
    }

    normalised = parse_normalised((src.get("ingredients") or {}).get("normalised"))
    f.protein_ingredients = normalised["protein_ingredients"]
    f.additives = normalised["additives"]
    f.sweeteners = normalised["sweeteners"]
    raw = (src.get("ingredients") or {}).get("raw_text") or ""
    f.raw_text = str(raw)
    f.has_raw_text = bool(f.raw_text.strip())
    text_l = f.raw_text.lower()
    f.has_proprietary_blend = "proprietary" in text_l and "blend" in text_l

    actives = cd.get("active_ingredients")
    if isinstance(actives, dict) and actives:
        parsed: Dict[str, float] = {}
        for k, v in actives.items():
            n = _num(v)
            if n is not None:
                parsed[str(k).strip().lower()] = n
        f.active_ingredients = parsed
        f.has_actives = bool(parsed)
    f.certifications = [c for c in (cd.get("certifications") or []) if isinstance(c, dict)]

    f.total_caffeine_mg = _sum_caffeine_mg(f)
    f.creatine_purity_verified, f.creatine_purity_pct = _creatine_purity_evidence(f, cd, src)

    stats = src.get("stats") if isinstance(src.get("stats"), dict) else {}
    f.protein_percentile = _num(
        (stats.get("protein_percentiles") or {}).get("subcategory_percentile")
    )
    return f


def _sum_caffeine_mg(f: SupplementFeatures) -> Optional[float]:
    tokens = [str(t).lower() for t in (get_settings().get("caffeine_source_tokens") or [])]
    if not tokens:
        tokens = ["caffeine", "green_tea", "guarana"]
    total = 0.0
    found = False
    for raw_name, val in f.active_ingredients.items():
        tok = _norm_token(raw_name)
        raw_l = raw_name.lower()
        if not any(t.replace(" ", "_") in tok or t in raw_l for t in tokens):
            continue
        unit = "mg" if "mg" in raw_l else ("g" if re.search(r"\bg\b", raw_l) or raw_l.endswith(" g") else "mg")
        total += _to_threshold_unit(val, unit, "mg")
        found = True
    return total if found else None


def _creatine_purity_evidence(
    f: SupplementFeatures,
    cd: Dict[str, Any],
    src: Dict[str, Any],
) -> Tuple[bool, Optional[float]]:
    """Return (verified, assay_pct) when numeric creatine purity evidence exists."""
    # certifications: name creatine_purity_verified or assay fields
    for cert in f.certifications:
        name = _norm_token(cert.get("name") or "")
        if "creatine_purity" in name or name == "creatine_purity_verified":
            pct = _num(cert.get("purity_pct") or cert.get("assay_pct") or cert.get("value"))
            return True, pct
        if "creapure" in name:
            pct = _num(cert.get("purity_pct") or cert.get("assay_pct"))
            return True, pct if pct is not None else 99.9

    tags = cd.get("tags") if isinstance(cd.get("tags"), dict) else {}
    raw_tags = tags.get("raw_tags") or tags.get("ingredient_tags") or []
    if isinstance(raw_tags, list):
        for t in raw_tags:
            if _norm_token(str(t)) in ("creatine_purity_verified", "creapure_sourced"):
                return True, None

    # Structured assay on category_data
    assay = cd.get("creatine_purity_pct") or cd.get("creatine_assay_pct")
    if assay is not None:
        return True, _num(assay)

    # Evidence blob
    evidence = cd.get("evidence") if isinstance(cd.get("evidence"), dict) else {}
    if evidence.get("creatine_purity_verified"):
        return True, _num(evidence.get("creatine_purity_pct"))

    return False, None


# ══════════════════════════════════════════════════════════════════════════════
# Card result container
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class CardResult:
    key: str
    name: str
    score: Optional[float]          # 0-100 (None if NOT SCORABLE)
    scorable: bool
    tags: List[str] = field(default_factory=list)
    detail: Dict[str, Any] = field(default_factory=dict)


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


# ══════════════════════════════════════════════════════════════════════════════
# Source ladder mapping (sheet 04)
# ══════════════════════════════════════════════════════════════════════════════
def map_protein_source(features: SupplementFeatures) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    """Return (source_name, v2_source_score, diaas) from the primary protein ingredient."""
    cfg = load_config()["source_ladder"]
    ings = features.protein_ingredients
    if not ings:
        return None, None, None
    primary = ings[0]

    def val(name: str) -> Tuple[str, float, Optional[float]]:
        e = cfg.get(name, {})
        return name, float(e.get("v2_score", 45)), e.get("diaas")

    p = primary
    if "collagen" in p:
        return val("Collagen Peptides")
    if "gelatin" in p:
        return val("Gelatin")
    if "hydroly" in p:
        return val("Whey Protein Hydrolysate")
    if "isolate" in p and "whey" in p:
        return val("Whey Protein Isolate")
    if "milk protein" in p:
        return val("Milk Protein Concentrate / Blend")
    if "concentrate" in p and "whey" in p:
        pct = features.protein_pct_by_weight
        if pct is not None and pct < 70:
            return val("Whey Concentrate (<70% protein)")
        return val("Whey Protein Concentrate (80%)")
    if "egg" in p:
        return val("Egg White Protein")
    if "caseinate" in p:
        return val("Calcium / Sodium Caseinate")
    if "casein" in p:
        return val("Micellar Casein")
    if "soy" in p and "isolate" in p:
        return val("Soy Protein Isolate")
    if ("pea" in p and "rice" in p) or (len(ings) >= 2 and any("pea" in i for i in ings) and any("rice" in i for i in ings)):
        return val("Pea + Rice blend (70:30)")
    if "pea" in p:
        return val("Pea Protein Isolate")
    if "soy" in p:
        return val("Soy Protein Concentrate")
    if "hemp" in p:
        return val("Hemp Protein")
    if "rice" in p:
        return val("Brown Rice Protein")
    if "wheat" in p or "gluten" in p:
        return val("Wheat / Gluten Protein")
    if "blend" in p:
        return val("Undisclosed 'protein blend'")
    return None, 45.0, None  # unknown named source -> undisclosed default


# ══════════════════════════════════════════════════════════════════════════════
# Individual card scorers.  Each returns CardResult.
# ══════════════════════════════════════════════════════════════════════════════
def _tier_tags(card_name: str, score: Optional[float]) -> List[str]:
    if score is None:
        return []
    cfg = load_config()["cards"].get(card_name, {})
    tt = cfg.get("tier_tags", {})
    return list(tt.get(tier_for_score(score)["tier_key"], []))


def score_protein_quality(f: SupplementFeatures) -> CardResult:
    name = "Protein Quality"
    if not f.protein_ingredients:
        return CardResult("protein_quality", name, None, False, ["no_protein_source_declared"])
    _src, source_score, _diaas = map_protein_source(f)
    source_score = source_score if source_score is not None else 45.0

    # Completeness (sheet 01 row 2): all 9 EAA >=80% FAO -> 100; limiting AA present -> 60; missing EAA -> 0.
    completeness = _completeness_score(f)

    # Protein % sub-weight; renormalise 45/20/25/10 if protein% missing (sheet N-rule row 2).
    protein_pct_score = PROTEIN_PCT_LADDER.score(f.protein_pct_by_weight)
    cleanliness = _formulation_cleanliness(f)

    weights = {"source": 45.0, "completeness": 20.0, "protein_pct": 25.0, "cleanliness": 10.0}
    subs = {"source": source_score, "completeness": completeness, "protein_pct": protein_pct_score, "cleanliness": cleanliness}
    present = {k: v for k, v in subs.items() if v is not None}
    if not present:
        return CardResult("protein_quality", name, None, False)
    wsum = sum(weights[k] for k in present)
    score = sum(present[k] * weights[k] for k in present) / wsum
    score = round(_clamp(score))
    # The sheet-01 tier tag columns embed source-type descriptors (e.g.
    # "whey_concentrate" in the Top band). Replace any such descriptor with the
    # source actually mapped for this product so we never mislabel an isolate.
    tags = [t for t in _tier_tags(name, score) if t not in SOURCE_DESCRIPTOR_TOKENS]
    src_token = SOURCE_NAME_TO_TOKEN.get(_src or "")
    if src_token:
        tags.append(src_token)
    if len(f.protein_ingredients) == 1:
        tags.append("single_source_protein")
    return CardResult(
        "protein_quality", name, score, True, tags,
        {"source": _src, "source_score": source_score, "completeness": completeness,
         "protein_pct_score": protein_pct_score, "cleanliness": cleanliness},
    )


def _completeness_score(f: SupplementFeatures) -> Optional[float]:
    ess = getattr(f, "_ess_per100", {}) or {}
    protein_100 = (f.protein_pct_by_weight)  # protein g per 100 g == protein%
    if not ess or not protein_100:
        return None
    ratios = []
    missing_eaa = False
    for aa, pattern in FAO_PATTERN_MG_PER_G.items():
        g100 = ess.get(aa)
        if g100 is None or g100 <= 0:
            missing_eaa = True
            continue
        mg_per_g = (g100 / protein_100) * 1000.0
        ratios.append(mg_per_g / pattern)
    if missing_eaa:
        return 0.0
    if not ratios:
        return None
    if all(r >= 0.80 for r in ratios):
        return 100.0
    return 60.0


def _formulation_cleanliness(f: SupplementFeatures) -> float:
    n = len(f.protein_ingredients)
    if n <= 1:
        return 100.0
    # % disclosure is not machine-readable in ES; treat multi-source as undisclosed.
    if n == 2:
        return 55.0
    return 30.0


def score_amino_acid_profile(f: SupplementFeatures) -> CardResult:
    name = "Amino Acid Profile"
    if not f.has_amino_profile or f.leucine_per_25g_protein is None:
        return CardResult("amino_acid_profile", name, None, False, ["no_amino_profile_published"])
    leu = LEUCINE_PER25_LADDER.score(f.leucine_per_25g_protein)
    eaa = EAA_PER25_LADDER.score(f.eaa_per_25g_protein)
    bcaa = BCAA_PER25_LADDER.score(f.bcaa_per_25g_protein)
    subs = {"leucine": (leu, 40.0), "eaa": (eaa, 30.0), "bcaa": (bcaa, 30.0)}
    present = {k: (s, w) for k, (s, w) in subs.items() if s is not None}
    wsum = sum(w for _s, w in present.values())
    score = round(_clamp(sum(s * w for s, w in present.values()) / wsum))
    tags = _tier_tags(name, score)
    if f.leucine_per_25g_protein >= 2.50:
        tags.append("leucine_threshold_met")
    return CardResult("amino_acid_profile", name, score, True, tags,
                      {"leucine_sub": leu, "eaa_sub": eaa, "bcaa_sub": bcaa,
                       "leucine_per_25g_protein": f.leucine_per_25g_protein})


def score_protein_efficiency(f: SupplementFeatures) -> CardResult:
    name = "Protein Efficiency"
    dens = PROTEIN_PER_100KCAL_LADDER.score(f.protein_per_100kcal)
    pct = PROTEIN_PCT_LADDER.score(f.protein_pct_by_weight)
    subs = {"density": (dens, 70.0), "protein_pct": (pct, 30.0)}
    present = {k: (s, w) for k, (s, w) in subs.items() if s is not None}
    if not present:
        return CardResult("protein_efficiency", name, None, False)
    wsum = sum(w for _s, w in present.values())
    score = round(_clamp(sum(s * w for s, w in present.values()) / wsum))
    return CardResult("protein_efficiency", name, score, True, _tier_tags(name, score),
                      {"density_sub": dens, "protein_pct_sub": pct})


def score_bioavailability(f: SupplementFeatures) -> CardResult:
    """Display-only (weight 0). Score = v2 source score (sheet 04)."""
    name = "Bioavailability"
    _src, source_score, _d = map_protein_source(f)
    if source_score is None:
        return CardResult("bioavailability", name, None, False)
    score = round(_clamp(source_score))
    return CardResult("bioavailability", name, score, True, _tier_tags(name, score), {"source": _src})


def score_digestibility(f: SupplementFeatures) -> CardResult:
    name = "Digestibility"
    if not f.has_raw_text:
        return CardResult("digestibility", name, None, False)
    score = 100.0
    text = f.raw_text.lower()
    add = set(f.additives)
    # Credits
    if re.search(r"digezyme|prohydrolase|enzyme blend", text):
        score += 12
    elif re.search(r"protease|papain|bromelain", text):
        score += 8
    if "lactase" in text:
        score += 6
    src_name, _s, _d = map_protein_source(f)
    if src_name in ("Whey Protein Isolate", "Whey Protein Hydrolysate"):
        score += 5
    if re.search(r"probiotic|cfu", text):
        score += 4
    # Penalties
    gums = [a for a in add if a in HYDROCOLLOID_GUM_INS]
    n_gums = len(gums)
    score -= 4 * n_gums
    if "ins466" in add or "carboxymethyl" in text:
        score -= 6
    if n_gums >= 3:
        score -= 8
    if src_name == "Whey Concentrate (<70% protein)":
        score -= 10
    score = round(_clamp(score))
    return CardResult("digestibility", name, score, True, _tier_tags(name, score), {"gum_count": n_gums})


def _cert_tokens(f: SupplementFeatures) -> List[Tuple[str, bool]]:
    """Return [(normalised_name, verified)] for declared certifications."""
    out = []
    for c in f.certifications:
        nm = _norm_token(str(c.get("name", "")))
        out.append((nm, bool(c.get("verified", True))))
    return out


def _pillar_label_integrity(f: SupplementFeatures) -> Tuple[Optional[float], List[str], Dict[str, Any]]:
    """Purity pillar without proprietary_blend (deduped into disclosure)."""
    if not f.has_raw_text and f.amino_acid_recovery is None:
        return None, [], {}
    score = 100.0
    tags: List[str] = []
    text = f.raw_text.lower()
    first3 = ", ".join(text.split(",")[:3])
    if re.search(r"maltodextrin|dextrose|starch", first3):
        score -= 20
        tags.append("maltodextrin_bulked")
    elif re.search(r"maltodextrin|dextrose|starch", text):
        score -= 10
    if re.search(r"colour|color|ins1[0-9]{2}", text):
        score -= 8
    if "flavour" in text or "flavor" in text:
        score -= 3
    if f.is_protein_category:
        for amino in SPIKING_FREE_AMINOS:
            if re.search(rf"\b{amino}\b", text):
                score -= 25
                tags.append("suspected_amino_spiking")
                break
    rec = f.amino_acid_recovery
    if rec is not None:
        if rec < 0.75:
            score -= 25
            tags.append("suspected_amino_spiking")
        elif rec < 0.85:
            score -= 15
            tags.append("label_claim_unverified")
        elif rec < 0.90:
            score -= 8
    else:
        tags.append("spiking_check_unavailable")
    floor = {"Whey Isolate": 75, "Whey Concentrate": 70, "Whey Blend": 65}.get(f.weight_column or "")
    if floor and f.protein_pct_by_weight is not None and f.protein_pct_by_weight < floor:
        score -= 15
        tags.append("label_mismatch")
    # Multi-source blend penalty only (proprietary_blend lives in disclosure).
    if not ("proprietary" in text and "blend" in text) and len(f.protein_ingredients) >= 2:
        score -= 10
    return round(_clamp(score)), tags, {"recovery": rec}


def _pillar_disclosure(f: SupplementFeatures) -> Tuple[float, List[str]]:
    """Transparency pillar — always scorable; absence of disclosure is the signal."""
    score = 92.0
    tags: List[str] = []
    text = f.raw_text.lower()
    if "proprietary" in text and "blend" in text:
        score -= 40
        tags.append("proprietary_blend")
    expects_actives = f.weight_column in {
        "Creatine", "Pre-Workout", "BCAA", "EAA", "Multivitamin", "Omega-3 / Fish Oil",
    }
    if expects_actives and not f.has_actives:
        score -= 25
        tags.append("undisclosed_dosages")
    has_panel = f.protein_g is not None or f.energy_kcal is not None
    if not has_panel:
        score -= 20
    if f.is_protein_category and not f.has_amino_profile:
        score -= 12
        tags.append("no_amino_profile_published")
    if not f.scoop_stated or f.servings_per_container is None:
        score -= 8
    if not re.search(r"batch|lot|mfg|manufactur", text):
        score -= 6
    return round(_clamp(score)), tags


def _pillar_verification(f: SupplementFeatures) -> Tuple[float, List[str], Dict[str, Any]]:
    """Testing & Trust pillar — always scorable."""
    score = 0.0
    has_third_party = False
    for nm, verified in _cert_tokens(f):
        credit = None
        for key, (pts, tp) in CERT_CREDITS.items():
            if key in nm:
                credit = (pts, tp)
                break
        if credit is None:
            continue
        pts, tp = credit
        if not verified:
            pts *= 0.5
        score += pts
        has_third_party = has_third_party or tp
    if not has_third_party:
        score = min(score, 30.0)
    score = round(_clamp(score))
    tags: List[str] = []
    if not has_third_party:
        tags.append("no_third_party_testing")
    return score, tags, {"third_party": has_third_party}


_LABEL_TRUST_PILLAR_WEIGHTS = {
    "integrity": 40.0,
    "disclosure": 25.0,
    "verification": 35.0,
}


def score_label_trust(f: SupplementFeatures) -> CardResult:
    """Blend purity + transparency + testing into one Label Trust card."""
    name = "Label Trust"
    integrity, i_tags, i_detail = _pillar_label_integrity(f)
    disclosure, d_tags = _pillar_disclosure(f)
    verification, v_tags, v_detail = _pillar_verification(f)

    pillars: Dict[str, float] = {
        "disclosure": float(disclosure),
        "verification": float(verification),
    }
    if integrity is not None:
        pillars["integrity"] = float(integrity)

    wsum = sum(_LABEL_TRUST_PILLAR_WEIGHTS[k] for k in pillars)
    score = round(_clamp(
        sum(pillars[k] * _LABEL_TRUST_PILLAR_WEIGHTS[k] for k in pillars) / wsum
    ))

    fact_tags: List[str] = []
    for tag in i_tags + d_tags + v_tags:
        if tag not in fact_tags:
            fact_tags.append(tag)

    return CardResult(
        "label_trust", name, score, True,
        _tier_tags(name, score) + fact_tags,
        {
            "integrity": integrity,
            "disclosure": disclosure,
            "verification": verification,
            "recovery": i_detail.get("recovery"),
            "third_party": v_detail.get("third_party"),
        },
    )


def score_heavy_metals(f: SupplementFeatures) -> CardResult:
    name = "Heavy metals"  # always scorable
    score = 55.0
    tags: List[str] = []
    cert_names = " ".join(nm for nm, _v in _cert_tokens(f))
    text = f.raw_text.lower()
    has_hm = bool(re.search(r"heavy_metal|heavy metal", cert_names + " " + text))
    per_batch = bool(re.search(r"per.?batch|batch.?coa|batch level", cert_names + " " + text))
    if has_hm and per_batch:
        score += 30
        tags.append("heavy_metal_tested")
    elif has_hm:
        score += 18
        tags.append("heavy_metal_tested")
    if "microbial" in text or "microbial" in cert_names:
        score += 10
    if "pesticide" in text or "pesticide" in cert_names:
        score += 8
    if "prop65" in cert_names or "prop 65" in text:
        score += 7
    is_plant = f.weight_column == "Plant Protein" or any(
        w in " ".join(f.protein_ingredients) for w in ("rice", "pea", "hemp")
    )
    is_botanical = bool(re.search(r"ashwagandha|extract|herb|botanical", text))
    if is_botanical and not has_hm:
        score -= 15
        tags.append("untested_botanical_blend")
    if is_plant and not has_hm:
        score -= 10
    if "cocoa" in text and not has_hm:
        score -= 8
    score = round(_clamp(score))
    if not has_hm:
        tags.append("no_contaminant_testing")
    return CardResult("heavy_metals", name, score, True, _tier_tags(name, score) + tags)


def _detect_sweetener_display_names(f: SupplementFeatures) -> List[str]:
    """Return canonical sweetener labels present on the product (ordered, unique)."""
    names: List[str] = []
    seen: set = set()

    def _add(label: str) -> None:
        if label not in seen:
            seen.add(label)
            names.append(label)

    for s in f.sweeteners:
        tok = _norm_token(s)
        matched = None
        for key, label in SWEETENER_DISPLAY_NAME.items():
            if key in tok:
                matched = label
                break
        if matched:
            _add(matched)
        elif tok:
            # Unknown non-nutritive token — surface a cleaned token, not the INS code alone.
            _add(tok.replace("_", " ").title())
    if f.added_sugar_g is not None and f.added_sugar_g > 5:
        _add("Added sugar")
    return names


def _sweetener_label_severity(label: str) -> float:
    """Lower = worse (more to avoid). Used to pick the single value name."""
    if label == "Added sugar":
        return 55.0
    if label == "Unsweetened":
        return 100.0
    scores = [
        SWEETENER_ANCHOR[k]
        for k, v in SWEETENER_DISPLAY_NAME.items()
        if v == label and k in SWEETENER_ANCHOR
    ]
    return min(scores) if scores else 72.0


def _worst_sweetener_name(names: List[str]) -> str:
    """Pick the single worst (most avoidable) sweetener label."""
    if not names:
        return "Unsweetened"
    return min(names, key=lambda n: (_sweetener_label_severity(n), n))


def score_sweeteners(f: SupplementFeatures) -> CardResult:
    name = "Sweeteners"
    sweeteners = list(f.sweeteners)
    added_sugar = f.added_sugar_g
    if not sweeteners and not f.has_raw_text:
        return CardResult("sweeteners", name, None, False)
    display_names = _detect_sweetener_display_names(f)
    if not sweeteners:
        # No sweetener system detected.
        base = 55.0 if (added_sugar and added_sugar > 5) else 100.0
        score = round(base)
        if not display_names:
            display_names = ["Added sugar"] if base < 100 else ["Unsweetened"]
        primary = _worst_sweetener_name(display_names)
        return CardResult(
            "sweeteners", name, score, True, _tier_tags(name, score),
            {
                "anchor": base,
                "artificial": 0,
                "detected_names": display_names,
                "primary_name": primary,
            },
        )
    anchors = []
    artificial = 0
    for s in sweeteners:
        tok = _norm_token(s)
        val = None
        for key, v in SWEETENER_ANCHOR.items():
            if key in tok:
                val = v
                break
        if val is None:
            val = 72.0  # unknown non-nutritive sweetener -> mid anchor
        anchors.append(val)
        if tok in ARTIFICIAL_SWEETENER_INS or any(a in tok for a in ARTIFICIAL_SWEETENER_INS):
            artificial += 1
    if added_sugar and added_sugar > 5:
        anchors.append(55.0)
    anchor = min(anchors)
    extra = max(0, len(sweeteners) - 1)
    score = _clamp(anchor - 8 * extra, lo=45.0)
    score = round(score)
    tags = _tier_tags(name, score)
    if artificial >= 1:
        tags.append("contains_artificial_sweeteners")
    if artificial >= 2:
        tags.append("multiple_artificial_sweeteners")
    primary = _worst_sweetener_name(display_names)
    return CardResult(
        "sweeteners", name, score, True, tags,
        {
            "anchor": anchor,
            "artificial": artificial,
            "detected_names": display_names,
            "primary_name": primary,
        },
    )


# ── Actives-based cards (Clinical Dose / Stimulant / Pump / Recovery) ─────────
# Clinical-dose reference thresholds live in settings.clinical_thresholds (JSON).
# Fallback defaults match framework v2 sheet 03 when settings are missing.
_DEFAULT_CLINICAL_THRESHOLDS: Dict[str, Tuple[float, str, float]] = {
    "creatine": (3.0, "g", 1.0),
    "beta_alanine": (3.2, "g", 0.7),
    "citrulline": (6.0, "g", 1.0),
    "caffeine": (200.0, "mg", 1.0),
    "leucine": (2.5, "g", 1.0),
    "betaine": (2.5, "g", 0.5),
    "taurine": (1.5, "g", 0.4),
    "theanine": (150.0, "mg", 0.4),
    "nitrate": (500.0, "mg", 0.8),
    "glycerol": (2.0, "g", 0.5),
    "ashwagandha": (450.0, "mg", 0.6),
    "vitamin_d3": (600.0, "iu", 0.7),
    "epa_dha": (750.0, "mg", 1.0),
}


def _clinical_thresholds() -> Dict[str, Tuple[float, str, float]]:
    loaded = get_clinical_thresholds()
    return loaded if loaded else dict(_DEFAULT_CLINICAL_THRESHOLDS)


# Back-compat alias used by older tests / imports.
CLINICAL_THRESHOLDS = _DEFAULT_CLINICAL_THRESHOLDS

_PRIMARY_ACTIVE_BY_COLUMN = {
    "Creatine": "creatine", "Pre-Workout": "caffeine", "BCAA": "leucine",
    "EAA": "leucine", "Multivitamin": "vitamin_d3", "Omega-3 / Fish Oil": "epa_dha",
}


def _match_active_key(name: str) -> Optional[str]:
    t = _norm_token(name)
    table = {
        "creatine": "creatine", "beta_alanine": "beta_alanine", "betaalanine": "beta_alanine",
        "citrulline": "citrulline", "caffeine": "caffeine", "l_leucine": "leucine", "leucine": "leucine",
        "betaine": "betaine", "taurine": "taurine", "theanine": "theanine", "l_theanine": "theanine",
        "nitrate": "nitrate", "beetroot": "nitrate", "glycerol": "glycerol",
        "ashwagandha": "ashwagandha", "vitamin_d": "vitamin_d3", "epa": "epa_dha", "dha": "epa_dha",
    }
    for key, val in table.items():
        if key in t:
            return val
    return None


def _active_amount(f: SupplementFeatures, active_key: str) -> Optional[Tuple[float, str]]:
    """Return (value, unit) for an active from active_ingredients keys like 'caffeine mg'."""
    for raw_name, val in f.active_ingredients.items():
        if _match_active_key(raw_name) == active_key:
            unit = "mg" if " mg" in raw_name or raw_name.endswith("mg") else (
                "g" if " g" in raw_name or raw_name.endswith("g") else "mg")
            return val, unit
    return None


def _to_threshold_unit(value: float, unit: str, threshold_unit: str) -> float:
    if unit == threshold_unit:
        return value
    if unit == "mg" and threshold_unit == "g":
        return value / 1000.0
    if unit == "g" and threshold_unit == "mg":
        return value * 1000.0
    return value


def score_clinical_dose(f: SupplementFeatures) -> CardResult:
    name = "Clinical Dose"
    thresholds = _clinical_thresholds()
    if not f.has_actives:
        # Absence is scored; keep a single disclosure tag (no Poor tier chip).
        return CardResult("clinical_dose", name, 25.0, True,
                          ["undisclosed_dosages"], {"reason": "no_actives"})
    primary = _PRIMARY_ACTIVE_BY_COLUMN.get(f.weight_column or "")
    sub_scores: List[Tuple[str, float, float]] = []  # (key, score, weight)
    dose_ratios: Dict[str, float] = {}
    primary_ratio = None
    for raw_name in f.active_ingredients:
        key = _match_active_key(raw_name)
        if key is None or key not in thresholds:
            continue
        threshold, tunit, weight = thresholds[key]
        amt = _active_amount(f, key)
        if amt is None:
            continue
        val = _to_threshold_unit(amt[0], amt[1], tunit)
        if key == "citrulline" and "malate" in _norm_token(raw_name):
            val *= 0.5  # Citrulline Malate 2:1 -> pure citrulline (sheet 03 row 4)
        ratio = val / threshold if threshold else 0
        dose_ratios[key] = ratio
        s = DOSE_RATIO_LADDER.score(ratio)
        sub_scores.append((key, s if s is not None else 15.0, weight))
        if key == primary:
            primary_ratio = ratio
    if not sub_scores:
        return CardResult("clinical_dose", name, 25.0, True, ["undisclosed_dosages"])
    wsum = sum(w for _k, _s, w in sub_scores)
    score = sum(s * w for _k, s, w in sub_scores) / wsum
    if primary_ratio is not None and primary_ratio < 0.50:
        score = min(score, 40.0)  # hard cap (sheet 03 row 21)
    score = round(_clamp(score))
    return CardResult("clinical_dose", name, score, True, _tier_tags(name, score),
                      {"primary": primary, "primary_ratio": primary_ratio, "dose_ratios": dose_ratios})


def score_stimulant_balance(f: SupplementFeatures) -> CardResult:
    name = "Stimulant Balance"
    caffeine = _active_amount(f, "caffeine")
    stims = 0
    stim_names = ("caffeine", "yohimbine", "synephrine", "dmaa", "dmha", "higenamine", "theobromine")
    for raw_name in f.active_ingredients:
        if any(s in _norm_token(raw_name) for s in stim_names):
            stims += 1
    if caffeine is None and stims == 0:
        # stim-free (or undisclosed). If labelled stim-free treat as 100.
        return CardResult("stimulant_balance", name, 100.0, True, _tier_tags(name, 100.0))
    caff_mg = _to_threshold_unit(caffeine[0], caffeine[1], "mg") if caffeine else 0.0
    # Caffeine base (70%).
    if caff_mg == 0:
        base = 100.0
    elif caff_mg < 150:
        base = 85.0
    elif caff_mg <= 250:
        base = 100.0
    elif caff_mg <= 300:
        base = 85.0
    elif caff_mg <= 350:
        base = 68.0
    elif caff_mg <= 400:
        base = 48.0
    else:
        base = 25.0
    # Synergy / stacking (30%).
    theanine = _active_amount(f, "theanine")
    if stims <= 1 and theanine is not None and caff_mg > 0:
        synergy = 100.0
    elif stims <= 1:
        synergy = 100.0
    elif stims == 2:
        synergy = 80.0
    elif stims == 3:
        synergy = 55.0
    else:
        synergy = 10.0
    score = base * 0.70 + synergy * 0.30
    tags = []
    if caff_mg > 400:
        score = min(score, 25.0)
        tags.append("exceeds_safe_caffeine_dose")
    score = round(_clamp(score))
    return CardResult("stimulant_balance", name, score, True, _tier_tags(name, score) + tags,
                      {"caffeine_mg": caff_mg, "stims": stims})


def score_pump_formula(f: SupplementFeatures) -> CardResult:
    name = "Pump Formula"
    cit = _active_amount(f, "citrulline")
    ba = _active_amount(f, "beta_alanine")
    nitrate = _active_amount(f, "nitrate")
    glycerol = _active_amount(f, "glycerol")
    if not any([cit, ba, nitrate, glycerol]) and not f.has_actives:
        return CardResult("pump_formula", name, None, False)
    cit_g = _to_threshold_unit(cit[0], cit[1], "g") if cit else 0.0
    # Citrulline Malate handling handled at clinical dose; here assume pure unless name says malate.
    for raw in f.active_ingredients:
        if "citrulline" in _norm_token(raw) and "malate" in _norm_token(raw):
            cit_g *= 0.5
    ba_g = _to_threshold_unit(ba[0], ba[1], "g") if ba else 0.0
    nitrate_mg = _to_threshold_unit(nitrate[0], nitrate[1], "mg") if nitrate else 0.0
    glycerol_g = _to_threshold_unit(glycerol[0], glycerol[1], "g") if glycerol else 0.0
    cit_s = PUMP_CITRULLINE_LADDER.score(cit_g) if cit else 0.0
    ba_s = PUMP_BETA_ALANINE_LADDER.score(ba_g) if ba else 0.0
    nitrate_s = 100.0 if nitrate_mg >= 500 else (nitrate_mg / 500.0 * 100.0 if nitrate_mg else 0.0)
    glycerol_s = 100.0 if glycerol_g >= 2 else (glycerol_g / 2.0 * 100.0 if glycerol_g else 0.0)
    score = round(_clamp(0.45 * (cit_s or 0) + 0.25 * (ba_s or 0) + 0.20 * nitrate_s + 0.10 * glycerol_s))
    return CardResult("pump_formula", name, score, True, _tier_tags(name, score))


_FORMULA_STIM_WEIGHT = 20.0
_FORMULA_PUMP_WEIGHT = 18.0
_FORMULA_RUNTIME_TAGS = frozenset({
    "exceeds_safe_caffeine_dose",
    "exceeds_fssai_caffeine_limit",
})


def score_formula(f: SupplementFeatures) -> CardResult:
    """Pre-workout Formula: stim 20/38 + pump 18/38 blend (legacy weight sum)."""
    name = "Formula"
    if f.weight_column != "Pre-Workout":
        return CardResult("formula", name, None, False)

    stim = score_stimulant_balance(f)
    pump = score_pump_formula(f)
    parts: List[Tuple[float, float]] = []
    runtime: List[str] = []
    if stim.scorable and stim.score is not None:
        parts.append((float(stim.score), _FORMULA_STIM_WEIGHT))
        for t in stim.tags or []:
            if t in _FORMULA_RUNTIME_TAGS and t not in runtime:
                runtime.append(t)
    if pump.scorable and pump.score is not None:
        parts.append((float(pump.score), _FORMULA_PUMP_WEIGHT))

    if not parts:
        return CardResult("formula", name, None, False)

    wsum = sum(w for _s, w in parts)
    score = round(_clamp(sum(s * w for s, w in parts) / wsum))
    return CardResult(
        "formula",
        name,
        score,
        True,
        _tier_tags(name, score) + runtime,
        {
            "stim_score": stim.score if stim.scorable else None,
            "pump_score": pump.score if pump.scorable else None,
            "stim_weight": _FORMULA_STIM_WEIGHT,
            "pump_weight": _FORMULA_PUMP_WEIGHT,
        },
    )


def score_recovery_formula(f: SupplementFeatures) -> CardResult:
    name = "Recovery Formula"
    if not f.has_amino_profile and f.leucine_g_per_serving is None:
        return CardResult("recovery_formula", name, None, False, ["no_amino_profile_published"])
    leu = RECOVERY_LEUCINE_LADDER.score(f.leucine_g_per_serving)
    eaa = RECOVERY_EAA_LADDER.score(f.eaa_g_per_serving)
    # Completeness: all 9 EAA present 100; BCAA-only 45; leucine-only 25.
    if f.eaa_count >= 9:
        completeness = 100.0
    elif f.eaa_count >= 3:
        completeness = 45.0  # BCAA-only
    else:
        completeness = 25.0
    subs = {"leucine": (leu, 45.0), "eaa": (eaa, 35.0), "completeness": (completeness, 20.0)}
    present = {k: (s, w) for k, (s, w) in subs.items() if s is not None}
    if not present:
        return CardResult("recovery_formula", name, None, False)
    wsum = sum(w for _s, w in present.values())
    score = sum(s * w for s, w in present.values()) / wsum
    bcaa_only = bool(f.eaa_count and f.eaa_count < 9 and f.eaa_count <= 3)
    if bcaa_only:
        score = min(score, 55.0)  # BCAA-only cap (sheet 01 row 15)
    score = round(_clamp(score))
    return CardResult(
        "recovery_formula", name, score, True, _tier_tags(name, score),
        {
            "eaa_count": f.eaa_count,
            "leucine_g_per_serving": f.leucine_g_per_serving,
            "eaa_g_per_serving": f.eaa_g_per_serving,
            "bcaa_only": bcaa_only,
        },
    )


def _servings_disclosure_score(f: SupplementFeatures) -> float:
    scoop = 100.0 if f.scoop_stated else 30.0
    count = 100.0 if f.servings_per_container else 40.0
    return (scoop + count) / 2.0


def _servings_pack_math_score(f: SupplementFeatures) -> Optional[float]:
    if not (f.serving_qty_g and f.servings_per_container and f.pack_weight_g):
        return None
    implied = f.serving_qty_g * f.servings_per_container
    if f.pack_weight_g <= 0:
        return None
    if abs(implied - f.pack_weight_g) / f.pack_weight_g > 0.03:
        return 20.0
    return 100.0


def _servings_scoop_fit(f: SupplementFeatures) -> Tuple[Optional[float], List[str]]:
    """Category-aware scoop appropriateness. Returns (score, runtime_tags)."""
    tags: List[str] = []
    col = f.weight_column or ""
    g = f.serving_qty_g

    if col == "Mass Gainer":
        if g is None:
            return None, tags
        if 50 <= g <= 100:
            return 100.0, tags
        if 40 <= g < 50 or 100 < g <= 120:
            return 80.0, tags
        if 30 <= g < 40 or 120 < g <= 150:
            return 55.0, tags
        return 35.0, tags

    if col in PROTEIN_LEAVES or f.is_protein_category:
        if g is None:
            return None, tags
        protein = f.protein_g
        if g > 40:
            tags.append("inflated_serving")
            if protein is not None and protein < 20:
                return 25.0, tags
            return 40.0, tags
        if 25 <= g <= 35:
            size_s = 100.0
        elif 20 <= g < 25 or 35 < g <= 40:
            size_s = 80.0
        elif 15 <= g < 20:
            size_s = 55.0
        else:
            size_s = 35.0
        if protein is None:
            return size_s, tags
        if protein >= 24:
            dens = 100.0
        elif protein >= 20:
            dens = 80.0
        elif protein >= 15:
            dens = 55.0
        else:
            dens = 30.0
        return 0.6 * size_s + 0.4 * dens, tags

    if col == "Creatine":
        if g is None:
            return None, tags
        if 3 <= g <= 5:
            return 100.0, tags
        if 2.5 <= g < 3 or 5 < g <= 6:
            return 85.0, tags
        if 2 <= g < 2.5 or 6 < g <= 8:
            return 60.0, tags
        return 35.0, tags

    if col == "Pre-Workout":
        if g is None:
            return None, tags
        if g > 20:
            tags.append("inflated_serving")
            return 40.0, tags
        if 8 <= g <= 16:
            return 100.0, tags
        if 6 <= g < 8 or 16 < g <= 20:
            return 75.0, tags
        return 50.0, tags

    if col in ("BCAA", "EAA"):
        if g is None:
            return None, tags
        if g > 40:
            tags.append("inflated_serving")
            return 35.0, tags
        if 8 <= g <= 15:
            return 100.0, tags
        if 5 <= g < 8 or 15 < g <= 18:
            return 75.0, tags
        return 45.0, tags

    # Multivitamin / Omega / unknown: no scoop ladder.
    if g is not None and g > 40 and col != "Mass Gainer":
        tags.append("inflated_serving")
    return None, tags


def score_serving_honesty(f: SupplementFeatures) -> CardResult:
    """Servings card (internal key serving_honesty).

    Blend of disclosure (30%), category scoop fit (50%), and pack math (20%).
    Missing scoop-fit or pack-math components renormalise over the rest.
    Floor 35. Always scorable.
    """
    name = "Servings"
    tags: List[str] = []
    disclosure = _servings_disclosure_score(f)
    scoop_fit, scoop_tags = _servings_scoop_fit(f)
    tags.extend(scoop_tags)
    pack = _servings_pack_math_score(f)

    # Non-gainer oversized scoop flag even when scoop ladder unused.
    if (
        f.serving_qty_g is not None
        and f.serving_qty_g > 40
        and f.weight_column != "Mass Gainer"
        and "inflated_serving" not in tags
    ):
        tags.append("inflated_serving")

    weights: Dict[str, float] = {"disclosure": 30.0}
    subs: Dict[str, float] = {"disclosure": disclosure}
    if scoop_fit is not None:
        weights["scoop"] = 50.0
        subs["scoop"] = scoop_fit
    if pack is not None:
        weights["pack"] = 20.0
        subs["pack"] = pack

    wsum = sum(weights[k] for k in subs)
    score = sum(subs[k] * weights[k] for k in subs) / wsum
    score = round(_clamp(score, lo=35.0))
    return CardResult(
        "serving_honesty",
        name,
        score,
        True,
        _tier_tags(name, score) + tags,
        {
            "disclosure": disclosure,
            "scoop_fit": scoop_fit,
            "pack_math": pack,
            "serving_qty_g": f.serving_qty_g,
            "servings_per_container": f.servings_per_container,
        },
    )


CARD_SCORERS = {
    "protein_quality": score_protein_quality,
    "amino_acid_profile": score_amino_acid_profile,
    "protein_efficiency": score_protein_efficiency,
    "bioavailability": score_bioavailability,
    "digestibility": score_digestibility,
    "label_trust": score_label_trust,
    "heavy_metals": score_heavy_metals,
    "sweeteners": score_sweeteners,
    "serving_honesty": score_serving_honesty,
    "clinical_dose": score_clinical_dose,
    "formula": score_formula,
    "recovery_formula": score_recovery_formula,
}


# ══════════════════════════════════════════════════════════════════════════════
# Audit presenters (buyer-facing value/subtitle/tags; scores unchanged)
# ══════════════════════════════════════════════════════════════════════════════
_MUTED_TIER = {"status": "average", "status_label": "Average", "color": "#9CA3AF", "theme": "average"}

# Match es_products highlight subtitle_new palette so Flutter renders identically.
_SUBTITLE_POSITIVE = "#2E7D32"
_SUBTITLE_NEUTRAL = "#B49A61"
_SUBTITLE_NEGATIVE = "#C62828"


def _subtitle_entry(label: str, sentiment: str = "positive") -> Dict[str, str]:
    color = {
        "positive": _SUBTITLE_POSITIVE,
        "neutral": _SUBTITLE_NEUTRAL,
        "negative": _SUBTITLE_NEGATIVE,
    }.get(sentiment, _SUBTITLE_NEUTRAL)
    return {"tag_label": label, "color_code": color}


def _fmt_rupees(v: float) -> str:
    if abs(v - round(v)) < 1e-9:
        return f"₹{v:.0f}"
    if v >= 100:
        return f"₹{v:.0f}"
    if v >= 10:
        return f"₹{v:.1f}"
    return f"₹{v:.2f}"


def _amino_integrity_ok(f: SupplementFeatures) -> bool:
    gates = get_settings().get("amino_integrity") or {}
    leu_min = float(gates.get("leucine_pct_of_protein_min", 10.0))
    eaa_min = float(gates.get("eaa_pct_of_protein_min", 40.0))
    if f.leucine_pct_of_protein is None or f.eaa_pct_of_protein is None:
        return False
    return f.leucine_pct_of_protein >= leu_min and f.eaa_pct_of_protein >= eaa_min


def _doses_undisclosed(f: SupplementFeatures, card: Dict[str, Any]) -> bool:
    tags = card.get("tags") or []
    return (
        f.has_proprietary_blend
        or not f.has_actives
        or "undisclosed_dosages" in tags
        or (card.get("detail") or {}).get("reason") == "no_actives"
    )


def present_protein_quality(f: SupplementFeatures, card: Dict[str, Any]) -> Dict[str, Any]:
    """Keep Protein Quality tier value; emit facts as ``subtitle_new`` array.

    Same shape as non-supplement cards::
        [{"tag_label": "Complete amino profile", "color_code": "#2E7D32"}, ...]
    Legacy ``subtitle`` stays a short fallback (Score: N).
    """
    out = dict(card)
    out["title"] = "Protein Quality"
    tags = list(card.get("tags") or [])
    detail = card.get("detail") or {}

    # Keep scorer tier label in value (Best / Top / Average / …).
    if not out.get("value") and card.get("score") is not None:
        out["value"] = tier_for_score(card["score"])["label"]

    subtitle_new: List[Dict[str, str]] = []
    completeness = detail.get("completeness")
    if completeness is not None and completeness >= 100:
        subtitle_new.append(_subtitle_entry("Complete amino profile", "positive"))
        if "complete_protein" not in tags:
            tags.append("complete_protein")
    elif completeness is not None:
        subtitle_new.append(_subtitle_entry("Incomplete amino profile", "negative"))
        if "incomplete_amino_profile" not in tags:
            tags.append("incomplete_amino_profile")
    elif not f.has_amino_profile:
        subtitle_new.append(_subtitle_entry("Amino profile unknown", "neutral"))

    pct = f.protein_pct_by_weight
    if pct is not None:
        sentiment = "positive" if pct >= 75 else ("neutral" if pct >= 60 else "negative")
        subtitle_new.append(_subtitle_entry(f"{pct:.0f}% protein", sentiment))

    if len(f.protein_ingredients) == 1:
        subtitle_new.append(_subtitle_entry("Single source", "positive"))
    elif len(f.protein_ingredients) > 1:
        subtitle_new.append(_subtitle_entry("Multi-source blend", "neutral"))

    if subtitle_new:
        out["subtitle_new"] = subtitle_new
    out["subtitle"] = f"Score: {card.get('score')}" if card.get("score") is not None else ""
    out["tags"] = tags
    return out


def present_protein_audit(f: SupplementFeatures, card: Dict[str, Any]) -> Dict[str, Any]:
    """Overwrite Protein Efficiency slot with Protein audit presentation.

    ``subtitle_new`` only carries cost-per-gram protein when price, protein_g,
    and servings_per_container are all available.
    """
    out = dict(card)
    out["title"] = "Protein"
    tags = list(card.get("tags") or [])

    if f.protein_g is not None:
        out["value"] = f"{f.protein_g:.0f} g / Scoop"
    else:
        out["value"] = card.get("value") or "—"

    if f.protein_percentile is not None:
        out["percentile"] = round(f.protein_percentile, 1)

    subtitle_new: List[Dict[str, str]] = []
    if (
        f.price is not None
        and f.protein_g
        and f.servings_per_container
        and f.protein_g * f.servings_per_container > 0
    ):
        cost = f.price / (f.protein_g * f.servings_per_container)
        subtitle_new.append(_subtitle_entry(f"{_fmt_rupees(cost)}/g", "neutral"))

    if subtitle_new:
        out["subtitle_new"] = subtitle_new
    out["subtitle"] = f"Score: {card.get('score')}" if card.get("score") is not None else ""
    out["tags"] = tags
    return out


def _humanize_tag(tag: str) -> str:
    return str(tag).replace("_", " ").strip().capitalize()


def _two_word_label(label: str) -> str:
    """Clamp a display label to at most two words."""
    words = [w for w in str(label).split() if w]
    return " ".join(words[:2]) if words else str(label)


# Short buyer-facing labels for Sweeteners subtitle_new (max 2 words).
_SWEETENER_TAG_LABELS: Dict[str, str] = {
    "unsweetened": "Unsweetened",
    "stevia_sweetened": "Stevia sweetened",
    "monk_fruit_sweetened": "Monk fruit",
    "naturally_sweetened": "Naturally sweetened",
    "low_artificial_sweetener_load": "Low artificial",
    "single_natural_sweetener": "Single natural",
    "mixed_sweetener_system": "Mixed sweeteners",
    "contains_artificial_sweeteners": "Artificial sweeteners",
    "multiple_artificial_sweeteners": "Multiple artificial",
    "high_artificial_sweetener_load": "High artificial",
    "high_added_sugar": "Added sugar",
}


def _sweetener_tag_label(tag: str) -> str:
    if tag in _SWEETENER_TAG_LABELS:
        return _SWEETENER_TAG_LABELS[tag]
    return _two_word_label(_humanize_tag(tag))


def present_sweeteners(f: SupplementFeatures, card: Dict[str, Any]) -> Dict[str, Any]:
    """Sweeteners: value = worst single sweetener; subtitle_new = ≤2-word tags."""
    out = dict(card)
    out["title"] = "Sweeteners"
    tags = list(card.get("tags") or [])
    detail = card.get("detail") or {}

    names = list(detail.get("detected_names") or _detect_sweetener_display_names(f))
    primary = detail.get("primary_name") or _worst_sweetener_name(names)
    out["value"] = primary

    # Prefer score-derived status_label for color coding; keep status from scorer.
    if card.get("score") is not None and not out.get("status_label"):
        out["status_label"] = tier_for_score(card["score"])["label"]

    subtitle_new: List[Dict[str, str]] = []
    for tag in tags:
        if tag in _SWEETENER_NEGATIVE_TAGS:
            sentiment = "negative"
        elif tag in _SWEETENER_POSITIVE_TAGS:
            sentiment = "positive"
        else:
            sentiment = "neutral"
        subtitle_new.append(_subtitle_entry(_sweetener_tag_label(tag), sentiment))

    if subtitle_new:
        out["subtitle_new"] = subtitle_new
    out["subtitle"] = f"Score: {card.get('score')}" if card.get("score") is not None else ""
    out["tags"] = tags
    return out


_DIGESTIBILITY_POSITIVE_TAGS = frozenset({
    "easy_to_digest",
    "added_digestive_enzymes",
    "lactose_free",
    "gut_friendly",
    "low_lactose",
})
_DIGESTIBILITY_NEGATIVE_TAGS = frozenset({
    "heavy_formula",
    "high_thickener_load",
    "hard_to_digest",
    "high_lactose_load",
})


def present_digestibility(f: SupplementFeatures, card: Dict[str, Any]) -> Dict[str, Any]:
    """Digestibility: keep tier value; emit tier tags as ``subtitle_new``."""
    out = dict(card)
    out["title"] = "Digestibility"
    tags = list(card.get("tags") or [])

    if card.get("score") is not None:
        tier = tier_for_score(card["score"])
        out["value"] = tier["label"]
        if not out.get("status_label"):
            out["status_label"] = tier["label"]

    subtitle_new: List[Dict[str, str]] = []
    for tag in tags:
        if tag in _DIGESTIBILITY_NEGATIVE_TAGS:
            sentiment = "negative"
        elif tag in _DIGESTIBILITY_POSITIVE_TAGS:
            sentiment = "positive"
        else:
            sentiment = "neutral"
        subtitle_new.append(_subtitle_entry(_humanize_tag(tag), sentiment))

    if subtitle_new:
        out["subtitle_new"] = subtitle_new
    out["subtitle"] = f"Score: {card.get('score')}" if card.get("score") is not None else ""
    out["tags"] = tags
    return out


_BIOAVAILABILITY_POSITIVE_TAGS = frozenset({
    "highly_bioavailable",
    "fast_absorbing",
    "diaas_above_100",
    "easily_absorbed",
    "sustained_release",
})
_BIOAVAILABILITY_NEGATIVE_TAGS = frozenset({
    "lower_bioavailability",
    "poor_protein_source",
    "negligible_diaas",
})
_BIOAVAILABILITY_TAG_LABELS: Dict[str, str] = {
    "highly_bioavailable": "Highly bioavailable",
    "fast_absorbing": "Fast absorbing",
    "diaas_above_100": "DIAAS 100+",
    "easily_absorbed": "Easily absorbed",
    "sustained_release": "Sustained release",
    "moderate_absorption": "Moderate absorption",
    "lower_bioavailability": "Lower absorption",
    "poor_protein_source": "Poor source",
    "negligible_diaas": "Negligible DIAAS",
}


def present_bioavailability(f: SupplementFeatures, card: Dict[str, Any]) -> Dict[str, Any]:
    """Bioavailability: keep tier value; emit tier tags as ``subtitle_new``."""
    return _present_tier_tag_card(
        card, "Bioavailability",
        _BIOAVAILABILITY_NEGATIVE_TAGS, _BIOAVAILABILITY_POSITIVE_TAGS, _BIOAVAILABILITY_TAG_LABELS,
    )


_LABEL_TRUST_NEGATIVE_TAGS = frozenset({
    "proprietary_blend",
    "undisclosed_dosages",
    "suspected_amino_spiking",
    "maltodextrin_bulked",
    "label_mismatch",
    "label_claim_unverified",
    "no_third_party_testing",
    "no_amino_profile_published",
    "contains_fillers",
    "heavy_fillers",
    "partial_disclosure",
    "hidden_quantities",
    "unverified_testing_claim",
    "no_quality_assurance",
})
_LABEL_TRUST_POSITIVE_TAGS = frozenset({
    "no_fillers",
    "label_verified",
    "pharmaceutical_grade",
    "high_purity",
    "minimal_fillers",
    "fully_transparent",
    "exact_ingredient_amounts",
    "open_formula",
    "transparent_formula",
    "full_nutrition_panel",
    "nsf_certified_for_sport",
    "informed_choice",
    "banned_substance_certified",
    "third_party_lab_tested",
    "trustified_certified",
    "batch_coa_available",
})
_LABEL_TRUST_TAG_LABELS: Dict[str, str] = {
    "proprietary_blend": "Proprietary blend",
    "undisclosed_dosages": "Hidden doses",
    "suspected_amino_spiking": "Amino spiking",
    "maltodextrin_bulked": "Maltodextrin bulk",
    "label_mismatch": "Label mismatch",
    "label_claim_unverified": "Unverified claim",
    "no_third_party_testing": "No lab testing",
    "no_amino_profile_published": "No amino profile",
    "spiking_check_unavailable": "Spiking unchecked",
    "open_formula": "Open formula",
    "fully_transparent": "Fully transparent",
    "label_verified": "Label verified",
    "nsf_certified_for_sport": "NSF Sport",
    "informed_choice": "Informed Choice",
    "third_party_lab_tested": "Lab tested",
    "batch_coa_available": "Batch COA",
    "no_fillers": "No fillers",
    "high_purity": "High purity",
}

_HEAVY_METALS_NEGATIVE_TAGS = frozenset({
    "no_contaminant_testing",
    "untested_botanical_blend",
    "high_risk_formulation",
})
_HEAVY_METALS_POSITIVE_TAGS = frozenset({
    "heavy_metal_tested",
    "below_prop65_limits",
    "pesticide_screened",
    "contaminant_panel_published",
})
_HEAVY_METALS_TAG_LABELS: Dict[str, str] = {
    "no_contaminant_testing": "Untested metals",
    "untested_botanical_blend": "Untested botanicals",
    "high_risk_formulation": "High risk",
    "heavy_metal_tested": "Metals tested",
    "below_prop65_limits": "Prop65 clear",
    "pesticide_screened": "Pesticide screened",
    "contaminant_panel_published": "Panel published",
    "gmp_facility_only": "GMP only",
}


def _pick_subtitle_tags(tags: List[str], negative: frozenset, positive: frozenset, limit: int = 3) -> List[str]:
    """Prefer actionable failures, then proofs; fall back to remaining tier tags."""
    neg = [t for t in tags if t in negative]
    pos = [t for t in tags if t in positive]
    other = [t for t in tags if t not in negative and t not in positive]
    picked: List[str] = []
    for group in (neg, pos, other):
        for t in group:
            if t not in picked:
                picked.append(t)
            if len(picked) >= limit:
                return picked
    return picked


def _present_tier_tag_card(
    card: Dict[str, Any],
    title: str,
    negative: frozenset,
    positive: frozenset,
    label_map: Dict[str, str],
    chip_limit: int = 3,
) -> Dict[str, Any]:
    out = dict(card)
    out["title"] = title
    tags = list(card.get("tags") or [])

    if card.get("score") is not None:
        tier = tier_for_score(card["score"])
        out["value"] = tier["label"]
        if not out.get("status_label"):
            out["status_label"] = tier["label"]

    subtitle_new: List[Dict[str, str]] = []
    for tag in _pick_subtitle_tags(tags, negative, positive, limit=chip_limit):
        if tag in negative:
            sentiment = "negative"
        elif tag in positive:
            sentiment = "positive"
        else:
            sentiment = "neutral"
        label = label_map.get(tag) or _two_word_label(_humanize_tag(tag))
        subtitle_new.append(_subtitle_entry(label, sentiment))

    if subtitle_new:
        out["subtitle_new"] = subtitle_new
    out["subtitle"] = f"Score: {card.get('score')}" if card.get("score") is not None else ""
    out["tags"] = tags
    return out


def present_label_trust(f: SupplementFeatures, card: Dict[str, Any]) -> Dict[str, Any]:
    return _present_tier_tag_card(
        card, "Label Trust",
        _LABEL_TRUST_NEGATIVE_TAGS, _LABEL_TRUST_POSITIVE_TAGS, _LABEL_TRUST_TAG_LABELS,
    )


def present_heavy_metals(f: SupplementFeatures, card: Dict[str, Any]) -> Dict[str, Any]:
    return _present_tier_tag_card(
        card, "Heavy metals",
        _HEAVY_METALS_NEGATIVE_TAGS, _HEAVY_METALS_POSITIVE_TAGS, _HEAVY_METALS_TAG_LABELS,
    )


_FORMULA_POSITIVE_TAGS = frozenset({
    "optimal_caffeine",
    "max_pump",
    "solid_energy",
    "strong_pump",
})
_FORMULA_NEGATIVE_TAGS = frozenset({
    "high_stim",
    "weak_pump",
    "unsafe_stim",
    "no_pump",
    "exceeds_safe_caffeine_dose",
    "exceeds_fssai_caffeine_limit",
})
_FORMULA_TAG_LABELS: Dict[str, str] = {
    "optimal_caffeine": "Optimal caffeine",
    "max_pump": "Max pump",
    "solid_energy": "Solid energy",
    "strong_pump": "Strong pump",
    "moderate_formula": "Moderate formula",
    "mixed_stack": "Mixed stack",
    "high_stim": "High stim",
    "weak_pump": "Weak pump",
    "unsafe_stim": "Unsafe stim",
    "no_pump": "No pump",
    "exceeds_safe_caffeine_dose": "Exceeds safe dose",
    "exceeds_fssai_caffeine_limit": "Exceeds FSSAI limit",
}


def present_formula(f: SupplementFeatures, card: Dict[str, Any]) -> Dict[str, Any]:
    """Formula: value = tier label; subtitle_new = tier tags (max 2)."""
    return _present_tier_tag_card(
        card,
        "Formula",
        _FORMULA_NEGATIVE_TAGS,
        _FORMULA_POSITIVE_TAGS,
        _FORMULA_TAG_LABELS,
        chip_limit=2,
    )


_RECOVERY_FORMULA_POSITIVE_TAGS = frozenset({
    "complete_eaa_blend",
    "leucine_full",
    "all_9_essentials",
})
_RECOVERY_FORMULA_NEGATIVE_TAGS = frozenset({
    "bcaa_only",
    "weak_recovery",
    "ineffective_recovery_dose",
    "no_amino_profile_published",
    "amino_profile_not_disclosed",
})
_RECOVERY_FORMULA_TAG_LABELS: Dict[str, str] = {
    "complete_eaa_blend": "Complete EAA blend",
    "leucine_full": "Leucine full",
    "all_9_essentials": "All 9 essentials",
    "bcaa_only": "BCAA only",
    "weak_recovery": "Weak recovery",
    "ineffective_recovery_dose": "Ineffective dose",
    "no_amino_profile_published": "Amino profile hidden",
    "amino_profile_not_disclosed": "Amino profile hidden",
}


def _attach_recovery_tier_subtitle(out: Dict[str, Any], tags: List[str]) -> None:
    presented = _present_tier_tag_card(
        {**out, "tags": tags},
        out.get("title") or "Recovery Formula",
        _RECOVERY_FORMULA_NEGATIVE_TAGS,
        _RECOVERY_FORMULA_POSITIVE_TAGS,
        _RECOVERY_FORMULA_TAG_LABELS,
        chip_limit=2,
    )
    if presented.get("subtitle_new"):
        out["subtitle_new"] = presented["subtitle_new"]


def present_recovery_formula(f: SupplementFeatures, card: Dict[str, Any]) -> Dict[str, Any]:
    """Recovery Formula: value = tier label; subtitle_new = tier tags."""
    return _present_tier_tag_card(
        card,
        "Recovery Formula",
        _RECOVERY_FORMULA_NEGATIVE_TAGS,
        _RECOVERY_FORMULA_POSITIVE_TAGS,
        _RECOVERY_FORMULA_TAG_LABELS,
        chip_limit=2,
    )


_SERVING_HONESTY_POSITIVE_TAGS = frozenset({
    "ideal_scoop",
    "cost_per_serving",
    "solid_scoop",
    "servings_clear",
})
_SERVING_HONESTY_NEGATIVE_TAGS = frozenset({
    "inflated_serving",
    "inflated_serving_size",
    "unclear_scoop",
    "misleading_servings",
    "pack_math_mismatch",
})
_SERVING_HONESTY_TAG_LABELS: Dict[str, str] = {
    "ideal_scoop": "Ideal scoop",
    "cost_per_serving": "Per serving",
    "full_tub_math": "Full tub math",  # legacy
    "solid_scoop": "Solid scoop",
    "servings_clear": "Servings clear",
    "typical_scoop": "Typical scoop",
    "inflated_serving": "Inflated serving",
    "inflated_serving_size": "Inflated serving",
    "unclear_scoop": "Unclear scoop",
    "misleading_servings": "Misleading servings",
    "pack_math_mismatch": "Pack mismatch",
}


def _fmt_serving_qty(g: float) -> str:
    if abs(g - round(g)) < 1e-9:
        return f"{g:.0f} g"
    return f"{g:.1f} g"


_SCOOP_SIZE_TAGS = frozenset({"ideal_scoop", "solid_scoop", "typical_scoop"})


def _fmt_scoop_tag_label(g: float) -> str:
    if abs(g - round(g)) < 1e-9:
        return f"{g:.0f}g Scoop"
    return f"{g:.1f}g Scoop"


def _cost_per_serving(f: SupplementFeatures) -> Optional[float]:
    if f.price is None or not f.servings_per_container or f.servings_per_container <= 0:
        return None
    return float(f.price) / float(f.servings_per_container)


def _serving_honesty_tag_labels(f: SupplementFeatures) -> Dict[str, str]:
    labels = dict(_SERVING_HONESTY_TAG_LABELS)
    if f.serving_qty_g is not None:
        scoop_label = _fmt_scoop_tag_label(f.serving_qty_g)
        for tag in _SCOOP_SIZE_TAGS:
            labels[tag] = scoop_label
    cost = _cost_per_serving(f)
    if cost is not None:
        labels["cost_per_serving"] = f"{_fmt_rupees(cost)}/serving"
    return labels


def present_serving_honesty(f: SupplementFeatures, card: Dict[str, Any]) -> Dict[str, Any]:
    """Servings: value = size · count; subtitle_new = scoop size + per-serving cost."""
    out = dict(card)
    out["title"] = "Servings"
    tags = list(card.get("tags") or [])

    # Prefer cost_per_serving over legacy full_tub_math in subtitle chips.
    tags = ["cost_per_serving" if t == "full_tub_math" else t for t in tags]
    if _cost_per_serving(f) is not None and "cost_per_serving" not in tags:
        tags.append("cost_per_serving")
    # Scoop size chip before cost so subtitle reads "35g Scoop" · "₹X/serving".
    scoop = [t for t in tags if t in _SCOOP_SIZE_TAGS]
    cost_tags = [t for t in tags if t == "cost_per_serving"]
    rest = [t for t in tags if t not in _SCOOP_SIZE_TAGS and t != "cost_per_serving"]
    tags = scoop + cost_tags + rest

    if not f.scoop_stated and not f.servings_per_container:
        out["value"] = "Serving size not disclosed"
        out.update(_MUTED_TIER)
    else:
        parts: List[str] = []
        if f.serving_qty_g is not None:
            parts.append(_fmt_serving_qty(f.serving_qty_g))
        if f.servings_per_container is not None:
            n = f.servings_per_container
            n_txt = f"{n:.0f}" if abs(n - round(n)) < 1e-9 else f"{n:g}"
            parts.append(f"{n_txt} servings")
        out["value"] = " · ".join(parts) if parts else "Serving size not disclosed"
        if card.get("score") is not None:
            tier = tier_for_score(card["score"])
            if not out.get("status_label"):
                out["status_label"] = tier["label"]
            out["status"] = out.get("status") or tier["status"]
            out["color"] = out.get("color") or tier["color"]
            out["theme"] = out.get("theme") or tier["theme"]

    presented = _present_tier_tag_card(
        {**out, "tags": tags},
        "Servings",
        _SERVING_HONESTY_NEGATIVE_TAGS,
        _SERVING_HONESTY_POSITIVE_TAGS,
        _serving_honesty_tag_labels(f),
        chip_limit=2,
    )
    if presented.get("subtitle_new"):
        out["subtitle_new"] = presented["subtitle_new"]

    detail = card.get("detail") or {}
    facts: List[str] = []
    pack = detail.get("pack_math")
    if pack is not None:
        facts.append("Pack math checks" if float(pack) >= 90 else "Pack math off")
    if "inflated_serving" in tags or "inflated_serving_size" in tags:
        facts.append("Inflated scoop")
    out["subtitle"] = " · ".join(facts) if facts else (
        f"Score: {card.get('score')}" if card.get("score") is not None else ""
    )
    out["tags"] = tags
    return out


_CLINICAL_DOSE_POSITIVE_TAGS = frozenset({
    "fully_dosed",
    "well_dosed",
})
_CLINICAL_DOSE_NEGATIVE_TAGS = frozenset({
    "underdosed",
    "severely_underdosed",
    "undisclosed_dosages",
    "doses_not_disclosed",
})
_CLINICAL_DOSE_TAG_LABELS: Dict[str, str] = {
    "fully_dosed": "Fully Dosed",
    "well_dosed": "Well Dosed",
    "partially_dosed": "Partially Dosed",
    "underdosed": "Underdosed",
    "severely_underdosed": "Severely Underdosed",
    "undisclosed_dosages": "Doses hidden",
    "doses_not_disclosed": "Doses hidden",
}


def _attach_clinical_tier_subtitle(out: Dict[str, Any], tags: List[str]) -> None:
    presented = _present_tier_tag_card(
        {**out, "tags": tags},
        out.get("title") or "Clinical Dose",
        _CLINICAL_DOSE_NEGATIVE_TAGS,
        _CLINICAL_DOSE_POSITIVE_TAGS,
        _CLINICAL_DOSE_TAG_LABELS,
        chip_limit=1,
    )
    if presented.get("subtitle_new"):
        out["subtitle_new"] = presented["subtitle_new"]


def present_clinical_dose(f: SupplementFeatures, card: Dict[str, Any]) -> Dict[str, Any]:
    """Clinical Dose: value = tier label; subtitle_new = tier tags."""
    out = dict(card)
    out["title"] = "Clinical Dose"
    tags = list(card.get("tags") or [])

    if "undisclosed_dosages" in tags or not f.has_actives:
        out["value"] = "Doses not disclosed by brand"
        out.update(_MUTED_TIER)
    elif card.get("score") is not None:
        tier = tier_for_score(card["score"])
        out["value"] = tier["label"]
        if not out.get("status_label"):
            out["status_label"] = tier["label"]

    _attach_clinical_tier_subtitle(out, tags)
    out["subtitle"] = f"Score: {card.get('score')}" if card.get("score") is not None else ""
    out["tags"] = tags
    return out


def present_creatine_audit(f: SupplementFeatures, card: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(card)
    out["title"] = "Creatine"
    tags = list(card.get("tags") or [])
    dose_g = float(get_settings().get("creatine_effective_dose_g", 5.0))

    # Proprietary blend: honest disclosure message (still emit the card).
    if f.has_proprietary_blend:
        out["value"] = "Doses not disclosed by brand"
        out.update(_MUTED_TIER)
        if "doses_not_disclosed" not in tags:
            tags.append("doses_not_disclosed")
        out["tags"] = tags
        out["subtitle"] = "Proprietary blend — amounts hidden"
        _attach_clinical_tier_subtitle(out, tags)
        return out

    if card.get("score") is not None:
        tier = tier_for_score(card["score"])
        out["value"] = tier["label"]
        if not out.get("status_label"):
            out["status_label"] = tier["label"]

    parts: List[str] = []
    if f.price is not None and f.pack_weight_g and f.pack_weight_g > 0 and dose_g > 0:
        doses_in_tub = f.pack_weight_g / dose_g
        cost = f.price / doses_in_tub
        parts.append(f"{_fmt_rupees(cost)}/{dose_g:.0f} g dose")
        parts.append(f"Tub lasts {doses_in_tub:.0f} days")
    if f.creatine_purity_verified and f.creatine_purity_pct is not None:
        parts.append(f"{f.creatine_purity_pct:.1f}% assayed")
    elif f.creatine_purity_verified:
        parts.append("Assayed")
    else:
        parts.append("Not assayed.")
    out["subtitle"] = " · ".join(parts) if parts else f"Score: {card.get('score')}"

    if f.creatine_purity_verified and "creatine_purity_verified" not in tags:
        tags.append("creatine_purity_verified")
    out["tags"] = tags
    _attach_clinical_tier_subtitle(out, tags)
    return out


def present_preworkout_audit(f: SupplementFeatures, card: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(card)
    out["title"] = "Pre-Workout"
    tags = list(card.get("tags") or [])
    espresso_mg = float(get_settings().get("espresso_caffeine_mg", 63.0))

    if f.has_proprietary_blend or not f.has_actives or "undisclosed_dosages" in tags:
        out["value"] = "Doses not disclosed by brand"
        out.update(_MUTED_TIER)
        if "doses_not_disclosed" not in tags:
            tags.append("doses_not_disclosed")
        out["subtitle"] = "Doses not disclosed by brand"
        out["tags"] = tags
        _attach_clinical_tier_subtitle(out, tags)
        return out

    if card.get("score") is not None:
        tier = tier_for_score(card["score"])
        out["value"] = tier["label"]
        if not out.get("status_label"):
            out["status_label"] = tier["label"]

    thresholds = _clinical_thresholds()
    ratios = (card.get("detail") or {}).get("dose_ratios") or {}
    if not ratios:
        for raw_name in f.active_ingredients:
            key = _match_active_key(raw_name)
            if key is None or key not in thresholds:
                continue
            threshold, tunit, _w = thresholds[key]
            amt = _active_amount(f, key)
            if amt is None:
                continue
            val = _to_threshold_unit(amt[0], amt[1], tunit)
            if key == "citrulline" and "malate" in _norm_token(raw_name):
                val *= 0.5
            ratios[key] = val / threshold if threshold else 0.0

    bar_parts: List[str] = []
    caff = f.total_caffeine_mg
    if caff is None:
        amt = _active_amount(f, "caffeine")
        if amt:
            caff = _to_threshold_unit(amt[0], amt[1], "mg")
    if caff is not None and espresso_mg > 0:
        bar_parts.append(f"{caff / espresso_mg:.1f} espressos")
    for key, ratio in ratios.items():
        pct = min(100.0, max(0.0, float(ratio) * 100.0))
        bar_parts.append(f"{get_clinical_label(key)} {pct:.0f}%")
    out["subtitle"] = " · ".join(bar_parts) if bar_parts else f"Score: {card.get('score')}"
    out["tags"] = tags
    _attach_clinical_tier_subtitle(out, tags)
    return out


def present_eaa_audit(f: SupplementFeatures, card: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(card)
    out["title"] = "EAA"
    tags = list(card.get("tags") or [])
    mps = float(get_settings().get("mps_leucine_g", 2.5))
    detail = card.get("detail") or {}
    bcaa_only = bool(detail.get("bcaa_only")) or (f.eaa_count > 0 and f.eaa_count <= 3)

    if not card.get("scorable") or card.get("score") is None:
        out["scorable"] = True
        out["score"] = card.get("score")
        out["value"] = "Amino profile not disclosed"
        out.update(_MUTED_TIER)
        out["subtitle"] = "Amino profile not disclosed"
        if "amino_profile_not_disclosed" not in tags:
            tags.append("amino_profile_not_disclosed")
        out["tags"] = tags
        # Ensure status fields exist for PDP
        if out.get("score") is None:
            out["score"] = 0
        _attach_recovery_tier_subtitle(out, tags)
        return out

    if bcaa_only:
        out["value"] = "BCAA-only"
    else:
        count = f.eaa_count or int(detail.get("eaa_count") or 0)
        out["value"] = f"{count}/9 complete"

    parts: List[str] = []
    leu = f.leucine_g_per_serving if f.leucine_g_per_serving is not None else detail.get("leucine_g_per_serving")
    if leu is not None:
        parts.append(f"Leucine {float(leu):.1f} g (MPS {mps:g} g)")
    eaa_g = f.eaa_g_per_serving if f.eaa_g_per_serving is not None else detail.get("eaa_g_per_serving")
    if (
        f.price is not None
        and eaa_g
        and f.servings_per_container
        and float(eaa_g) * f.servings_per_container > 0
    ):
        cost = f.price / (float(eaa_g) * f.servings_per_container)
        parts.append(f"{_fmt_rupees(cost)}/g EAA")
    out["subtitle"] = " · ".join(parts) if parts else f"Score: {card.get('score')}"
    out["tags"] = tags
    _attach_recovery_tier_subtitle(out, tags)
    return out


def audit_slot_for(weight_column: Optional[str], card_key: str) -> Optional[str]:
    """Return audit kind if this card key should get audit presentation for the leaf."""
    if card_key == "protein_efficiency" and weight_column in PROTEIN_LEAVES:
        return "protein"
    if card_key == "clinical_dose" and weight_column == "Creatine":
        return "creatine"
    if card_key == "clinical_dose" and weight_column == "Pre-Workout":
        return "pre_workout"
    if card_key == "recovery_formula" and weight_column in ("EAA", "BCAA"):
        return "eaa"
    return None


def apply_audit_presentation(
    cards: Dict[str, Dict[str, Any]],
    features: SupplementFeatures,
) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for key, card in cards.items():
        kind = audit_slot_for(features.weight_column, key)
        if key == "protein_quality" and features.weight_column in PROTEIN_LEAVES and card.get("scorable"):
            out[key] = present_protein_quality(features, card)
        elif key == "sweeteners" and card.get("scorable"):
            out[key] = present_sweeteners(features, card)
        elif key == "digestibility" and card.get("scorable"):
            out[key] = present_digestibility(features, card)
        elif key == "bioavailability" and card.get("scorable"):
            out[key] = present_bioavailability(features, card)
        elif key == "label_trust" and card.get("scorable"):
            out[key] = present_label_trust(features, card)
        elif key == "heavy_metals" and card.get("scorable"):
            out[key] = present_heavy_metals(features, card)
        elif key == "serving_honesty" and card.get("scorable"):
            out[key] = present_serving_honesty(features, card)
        elif key == "formula" and card.get("scorable"):
            out[key] = present_formula(features, card)
        elif kind == "protein":
            out[key] = present_protein_audit(features, card)
        elif kind == "creatine":
            out[key] = present_creatine_audit(features, card)
        elif kind == "pre_workout":
            out[key] = present_preworkout_audit(features, card)
        elif key == "clinical_dose" and card.get("scorable"):
            out[key] = present_clinical_dose(features, card)
        elif kind == "eaa":
            out[key] = present_eaa_audit(features, card)
        elif key == "recovery_formula" and card.get("scorable"):
            out[key] = present_recovery_formula(features, card)
        else:
            out[key] = card
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Composite engine
# ══════════════════════════════════════════════════════════════════════════════
def is_supplement(src: Dict[str, Any]) -> bool:
    paths = src.get("category_paths") or []
    return any("f_and_b/supplements" in str(p) for p in paths)


def _weight_vector(weight_column: str) -> Dict[str, float]:
    weights = load_config()["weights"]
    out: Dict[str, float] = {}
    for card_name, cols in weights.items():
        key = NAME_TO_CARD_KEY.get(card_name)
        if key is None:
            continue
        out[key] = float(cols.get(weight_column, 0.0) or 0.0)
    return out


def compute_supplement_scorecards(
    src: Dict[str, Any],
    allowed_keys: Optional[Collection[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Compute applicable supplement cards + composite Flean Score.

    When ``allowed_keys`` is provided (from flean_card_config), only those cards
    are calculated and only allowed cards with weight>0 enter the composite.
    When omitted, all scorers run (legacy weight-based PDP path).

    Returns None when the product is not a supplement or its leaf category has no
    weight column defined (caller should then fall back to percentile cards).
    """
    if not is_supplement(src):
        return None
    features = extract_features(src)
    if not features.weight_column:
        log.info("SUPPLEMENT_SCORE_SKIP | unmapped_leaf=%s", features.leaf)
        return None

    weights = _weight_vector(features.weight_column)
    allow = frozenset(allowed_keys) if allowed_keys is not None else None
    scorers = (
        {k: CARD_SCORERS[k] for k in allow if k in CARD_SCORERS}
        if allow is not None
        else CARD_SCORERS
    )
    cards: Dict[str, CardResult] = {}
    for key, scorer in scorers.items():
        try:
            cards[key] = scorer(features)
        except Exception as exc:  # defensive: one bad card must not sink the PDP
            log.warning("SUPPLEMENT_CARD_ERROR | card=%s | error=%s", key, exc)
            cards[key] = CardResult(key, CARD_KEY_TO_NAME[key], None, False)

    # Composite: weighted mean of applicable (weight>0) & scorable cards, with
    # renormalisation + confidence deduction for NOT SCORABLE cards (sheet 02 A24).
    applicable = {
        k: w for k, w in weights.items()
        if w > 0 and k in cards and (allow is None or k in allow)
    }
    scorable = {k: cards[k].score for k in applicable if cards[k].scorable and cards[k].score is not None}
    dropped = [k for k in applicable if k not in scorable]
    total_weight = sum(applicable[k] for k in scorable)
    if total_weight > 0:
        composite = sum(scorable[k] * applicable[k] for k in scorable) / total_weight
    else:
        composite = 0.0
    confidence_deduction = min(CONFIDENCE_DEDUCTION_CAP, CONFIDENCE_DEDUCTION_PER_DROP * len(dropped))
    flean_score = round(_clamp(composite - confidence_deduction), 2)

    # Aggregate tags from applicable (weight>0) cards only, so weight-0 display
    # cards (e.g. Recovery/Stimulant on a protein product) do not leak tier tags.
    all_tags = sorted({t for k, c in cards.items() if applicable.get(k, 0.0) > 0 for t in c.tags})
    if dropped:
        all_tags.append("data_incomplete")

    cards_dict = {k: _card_to_dict(c, weights.get(k, 0.0)) for k, c in cards.items()}
    cards_dict = apply_audit_presentation(cards_dict, features)

    es_flean = src.get("flean_score") if isinstance(src.get("flean_score"), dict) else {}
    hide_score = (
        bool(es_flean.get("hide_score"))
        if es_flean.get("hide_score") is not None
        else False
    )

    return {
        "leaf_category": features.leaf,
        "weight_column": features.weight_column,
        "cards": cards_dict,
        "flean_score": flean_score,
        "hide_score": hide_score,
        "composite_before_deduction": round(composite, 2),
        "confidence_deduction": confidence_deduction,
        "cards_dropped": len(dropped),
        "dropped_cards": dropped,
        "tags": all_tags,
        "features": _features_summary(features),
        "_features": features,  # for PDP adapter; stripped before API if needed
    }


def _card_to_dict(c: CardResult, weight: float) -> Dict[str, Any]:
    if not c.scorable or c.score is None:
        return {
            "title": c.name, "scorable": False, "weight": weight, "score": None,
            "tags": c.tags, "detail": c.detail,
        }
    tier = tier_for_score(c.score)
    return {
        "title": c.name, "scorable": True, "weight": weight, "score": c.score,
        "value": tier["label"], "status": tier["status"], "status_label": tier["label"],
        "color": tier["color"], "theme": tier["theme"], "tags": c.tags, "detail": c.detail,
    }


# Icons reuse the existing PDP icon set where a sensible analogue exists.
SUPPLEMENT_CARD_ICONS: Dict[str, str] = {
    "protein_quality": "https://img.flean.ai/assets/Pdp-Icons/02.svg",
    "amino_acid_profile": "https://img.flean.ai/assets/Pdp-Icons/02.svg",
    "protein_efficiency": "https://img.flean.ai/assets/Pdp-Icons/02.svg",
    "sweeteners": "https://img.flean.ai/assets/Pdp-Icons/03.svg",
    "digestibility": "https://img.flean.ai/assets/Pdp-Icons/digestability.svg",
    "additives": "https://img.flean.ai/assets/Pdp-Icons/additives1.svg",
    "label_trust": "https://img.flean.ai/assets/Pdp-Icons/01.svg",
    "heavy_metals": "https://img.flean.ai/assets/Pdp-Icons/preservatives1.svg",
    "serving_honesty": "https://img.flean.ai/assets/Pdp-Icons/serving.svg",
    "clinical_dose": "https://img.flean.ai/assets/Pdp-Icons/02.svg",
    "formula": "https://img.flean.ai/assets/Pdp-Icons/formula1.svg",
    "recovery_formula": "https://img.flean.ai/assets/Pdp-Icons/wellness.svg",
    "bioavailability": "https://img.flean.ai/assets/Pdp-Icons/bioavailable.svg",
}


def to_pdp_score_cards(
    result: Dict[str, Any],
    allowed_keys: Optional[Collection[str]] = None,
) -> Dict[str, Any]:
    """Adapt a compute_supplement_scorecards() result into the PDP ``score_cards``
    shape consumed by Flutter, plus a computed ``flean_badge`` and a
    ``supplement_scoring`` detail block.

    When ``allowed_keys`` is provided (config-driven path), emit only those keys
    and skip weight/bioavailability gates — caller applies config order.
    When omitted, keep legacy audit-first / weight>0 / bioavailability rules.
    """
    weight_column = result.get("weight_column")
    cards_src = result["cards"]
    allow = frozenset(allowed_keys) if allowed_keys is not None else None
    if allow is not None:
        cards_src = {k: v for k, v in cards_src.items() if k in allow}

    # Identify the audit card key for this leaf (pinned to order 1 in legacy path).
    audit_key: Optional[str] = None
    for key in cards_src:
        if audit_slot_for(weight_column, key):
            audit_key = key
            break

    # Build ordered entry list: audit first, then weight-desc, then display-only.
    def _sort_key(item: Tuple[str, Dict[str, Any]]) -> Tuple[int, float]:
        key, c = item
        if key == audit_key:
            return (0, 0.0)
        weight = c.get("weight", 0.0) or 0.0
        return (1 if weight > 0 else 2, -weight)

    entries = (
        list(cards_src.items())
        if allow is not None
        else sorted(cards_src.items(), key=_sort_key)
    )
    cards_out: Dict[str, Any] = {}
    order = 0
    for key, c in entries:
        weight = c.get("weight", 0.0) or 0.0
        is_audit = key == audit_key
        # Skip unscorable unless it's an EAA audit that we force-emit.
        if not c.get("scorable"):
            if not (is_audit and audit_slot_for(weight_column, key) == "eaa"):
                continue
        # Legacy visibility: weight>0, bioavailability display-only, or audit.
        if allow is None and weight <= 0 and key != "bioavailability" and not is_audit:
            continue
        order += 1
        # Ensure muted EAA unscorable still has display fields
        value = c.get("value")
        status = c.get("status")
        if value is None and c.get("score") is not None:
            tier = tier_for_score(c["score"])
            value = tier["label"]
            status = tier["status"]
        card: Dict[str, Any] = {
            "title": c.get("title") or CARD_KEY_TO_NAME.get(key, key),
            "value": value if value is not None else "—",
            "subtitle": c.get("subtitle") or (f"Score: {c['score']}" if c.get("score") is not None else ""),
            "score": c.get("score"),
            "percentile": c.get("percentile"),
            "status": status or c.get("status") or "average",
            "status_label": c.get("status_label") or c.get("value") or status or "Average",
            "color": c.get("color") or "#F2E9BB80",
            "theme": c.get("theme") or c.get("status") or "average",
            "weight": weight,
            "display_only": weight <= 0 and not is_audit,
            "tags": c.get("tags", []),
            "visible": True,
            "order": order,
        }
        if c.get("subtitle_new"):
            card["subtitle_new"] = c["subtitle_new"]
        icon = SUPPLEMENT_CARD_ICONS.get(key)
        if icon:
            card["icon_url"] = icon
        cards_out[key] = card

    flean = result["flean_score"]
    tier = tier_for_score(flean)
    flean_badge = {
        "score": round(flean / 10.0, 1),
        "score_display": str(round(flean)),
        "level": tier["status"],
        "level_text": tier["label"],
        "color": tier["color"],
        "hide_score": bool(result.get("hide_score", False)),
    }
    supplement_scoring = {
        "framework_version": "v2.0",
        "flean_score": flean,
        "composite_before_deduction": result["composite_before_deduction"],
        "confidence_deduction": result["confidence_deduction"],
        "cards_dropped": result["cards_dropped"],
        "dropped_cards": result["dropped_cards"],
        "leaf_category": result["leaf_category"],
        "weight_column": result["weight_column"],
        "tags": result["tags"],
        "features": result["features"],
    }
    return {"score_cards": cards_out, "flean_badge": flean_badge, "supplement_scoring": supplement_scoring}


def _features_summary(f: SupplementFeatures) -> Dict[str, Any]:
    return {
        "protein_pct_by_weight": f.protein_pct_by_weight,
        "protein_per_100kcal": f.protein_per_100kcal,
        "leucine_per_25g_protein": f.leucine_per_25g_protein,
        "eaa_per_25g_protein": f.eaa_per_25g_protein,
        "bcaa_per_25g_protein": f.bcaa_per_25g_protein,
        "amino_acid_recovery": f.amino_acid_recovery,
        "leucine_pct_of_protein": f.leucine_pct_of_protein,
        "eaa_pct_of_protein": f.eaa_pct_of_protein,
        "total_caffeine_mg": f.total_caffeine_mg,
        "has_proprietary_blend": f.has_proprietary_blend,
        "creatine_purity_verified": f.creatine_purity_verified,
        "protein_percentile": f.protein_percentile,
    }
