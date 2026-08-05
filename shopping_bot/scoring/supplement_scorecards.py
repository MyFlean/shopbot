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
from typing import Any, Dict, List, Optional, Tuple

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
    "lean_formula": "Lean Formula",
    "purity": "Purity & Label Integrity",
    "transparency": "Transparency",
    "testing_trust": "Testing & Trust",
    "contaminant_safety": "Contaminant Safety",
    "sweeteners": "Sweeteners",
    "serving_honesty": "Serving Honesty",
    "clinical_dose": "Clinical Dose",
    "stimulant_balance": "Stimulant Balance",
    "pump_formula": "Pump Formula",
    "recovery_formula": "Recovery Formula",
}
NAME_TO_CARD_KEY = {v: k for k, v in CARD_KEY_TO_NAME.items()}


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


def _step(value: Optional[float], bands: Tuple[Tuple[float, float], ...], default: float) -> Optional[float]:
    """Step ladder with `value <= threshold` semantics (Lean Formula sub-scores)."""
    if value is None:
        return None
    for threshold, score in bands:
        if value <= threshold:
            return score
    return default


# Lean Formula sub-score bands (sheet 01 row 7 / sheet 03 row 42 for sodium).
SUGAR_BANDS = ((0, 100), (1, 95), (2, 88), (3, 78), (5, 62), (8, 42))       # else 20
FAT_BANDS = ((1.5, 100), (2, 95), (3, 88), (4, 78), (6, 62), (8, 45))        # >=8 -> 28
CARB_BANDS = ((2, 100), (3, 95), (5, 85), (8, 70), (12, 52), (18, 35))       # >=18 -> 20
SODIUM_BANDS = ((100, 100), (150, 92), (200, 84), (300, 70), (400, 52), (500, 35))  # >=500 -> 20


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
# Non-nutritive sweeteners that count toward the stacking penalty / caution tags.
ARTIFICIAL_SWEETENER_INS = frozenset({"ins950", "ins951", "ins954", "ins955", "ins961"})

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

    # Amino acids: prefer the detailed found_amino_acid_profile, else the summary.
    found = cd.get("found_amino_acid_profile") or {}
    summary = cd.get("amino_acid_profile") or {}
    aa_qty = _parse_qty_grams(found.get("qty")) or _parse_qty_grams(summary.get("qty")) or qty_basis
    ess = found.get("essential_amino_acids") or {}
    neaa_dict = found.get("non_essential_amino_acids") or {}
    protein_100 = (protein_per_basis / qty_basis * 100.0) if (protein_per_basis and qty_basis) else None

    def per100_aa(v: Any) -> Optional[float]:
        n = _num(v)
        return (n / aa_qty * 100.0) if (n is not None and aa_qty) else None

    leucine_100 = per100_aa(ess.get("leucine g"))
    eaa_100 = per100_aa(found.get("total_eaa_g")) or per100_aa(summary.get("essential_amino_acids_g"))
    bcaa_100 = per100_aa(found.get("total_bcaa_g")) or per100_aa(summary.get("total_bcaa_g"))
    neaa_100 = None
    if neaa_dict:
        s = sum(_num(v) or 0 for v in neaa_dict.values())
        neaa_100 = s / aa_qty * 100.0 if aa_qty else None
    elif summary.get("non_essential_amino_acids_g") is not None:
        neaa_100 = per100_aa(summary.get("non_essential_amino_acids_g"))
    cond_100 = per100_aa(summary.get("conditionally_essential_amino_acids_g"))

    f.eaa_count = sum(1 for v in ess.values() if (_num(v) or 0) > 0)
    f.has_amino_profile = bool(ess) or eaa_100 is not None

    if protein_100:
        if leucine_100 is not None:
            f.leucine_per_25g_protein = leucine_100 / protein_100 * 25.0
        if eaa_100 is not None:
            f.eaa_per_25g_protein = eaa_100 / protein_100 * 25.0
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

    # Store detailed AA per-100 for completeness scoring.
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
    return f


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


def score_lean_formula(f: SupplementFeatures) -> CardResult:
    name = "Lean Formula"
    is_gainer = f.weight_column == "Mass Gainer"
    subs: Dict[str, Optional[float]] = {
        "added_sugar": _step(f.added_sugar_g if f.added_sugar_g is not None else f.total_sugar_g, SUGAR_BANDS, 20),
        "fat": _step(f.total_fat_g, FAT_BANDS, 28),
        "sodium": _step(f.sodium_mg, SODIUM_BANDS, 20),
    }
    if is_gainer:
        # Carbohydrate quantity is the product's purpose; score carb *source* instead.
        malto = any("malto" in a or "dextrose" in a for a in f.additives) or "maltodextrin" in f.raw_text.lower()
        subs["carb_source"] = 45.0 if malto else 90.0
    else:
        subs["carb"] = _step(f.carb_g, CARB_BANDS, 20)
    present = {k: v for k, v in subs.items() if v is not None}
    if len(present) < 2:
        return CardResult("lean_formula", name, None, False)
    score = round(_clamp(sum(present.values()) / len(present)))
    return CardResult("lean_formula", name, score, True, _tier_tags(name, score), {"subs": present})


def score_purity(f: SupplementFeatures) -> CardResult:
    name = "Purity & Label Integrity"
    if not f.has_raw_text and f.amino_acid_recovery is None:
        return CardResult("purity", name, None, False)
    score = 100.0
    tags: List[str] = []
    text = f.raw_text.lower()
    # Filler penalties.
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
    # Adulteration: free amino acids as separate ingredients in a protein product.
    if f.is_protein_category:
        for amino in SPIKING_FREE_AMINOS:
            if re.search(rf"\b{amino}\b", text):
                score -= 25
                tags.append("suspected_amino_spiking")
                break
    # Amino recovery ladder (sheet 03 row 39).
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
    # Label integrity: protein % below category floor.
    floor = {"Whey Isolate": 75, "Whey Concentrate": 70, "Whey Blend": 65}.get(f.weight_column or "")
    if floor and f.protein_pct_by_weight is not None and f.protein_pct_by_weight < floor:
        score -= 15
        tags.append("label_mismatch")
    if "proprietary" in text and "blend" in text:
        score -= 20
        tags.append("proprietary_blend")
    elif len(f.protein_ingredients) >= 2:
        score -= 10
    score = round(_clamp(score))
    return CardResult("purity", name, score, True, _tier_tags(name, score) + tags, {"recovery": rec})


def score_transparency(f: SupplementFeatures) -> CardResult:
    name = "Transparency"  # always scorable (sheet N-rule row 9)
    score = 92.0
    tags: List[str] = []
    text = f.raw_text.lower()
    if "proprietary" in text and "blend" in text:
        score -= 40
        tags.append("proprietary_blend")
    # Active doses undisclosed (only meaningful where actives are expected).
    expects_actives = f.weight_column in {"Creatine", "Pre-Workout", "BCAA", "EAA", "Multivitamin", "Omega-3 / Fish Oil"}
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
    score = round(_clamp(score))
    return CardResult("transparency", name, score, True, _tier_tags(name, score) + tags)


def _cert_tokens(f: SupplementFeatures) -> List[Tuple[str, bool]]:
    """Return [(normalised_name, verified)] for declared certifications."""
    out = []
    for c in f.certifications:
        nm = _norm_token(str(c.get("name", "")))
        out.append((nm, bool(c.get("verified", True))))
    # Also scan raw_text/tags for common testing claims.
    return out


def score_testing_trust(f: SupplementFeatures) -> CardResult:
    name = "Testing & Trust"  # always scorable (sheet N-rule row 10)
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
            pts *= 0.5  # expired / unlisted -> 50% (sheet 01 row 10 hard-cap clause)
        score += pts
        has_third_party = has_third_party or tp
    if not has_third_party:
        score = min(score, 30.0)  # hard cap when no third-party evidence
    score = round(_clamp(score))
    tags = _tier_tags(name, score)
    if not has_third_party:
        tags.append("no_third_party_testing")
    return CardResult("testing_trust", name, score, True, tags, {"third_party": has_third_party})


def score_contaminant_safety(f: SupplementFeatures) -> CardResult:
    name = "Contaminant Safety"  # always scorable (sheet N-rule row 16)
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
    # Risk penalties.
    is_plant = f.weight_column == "Plant Protein" or any(w in " ".join(f.protein_ingredients) for w in ("rice", "pea", "hemp"))
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
    return CardResult("contaminant_safety", name, score, True, _tier_tags(name, score) + tags)


def score_sweeteners(f: SupplementFeatures) -> CardResult:
    name = "Sweeteners"
    sweeteners = list(f.sweeteners)
    added_sugar = f.added_sugar_g
    if not sweeteners and not f.has_raw_text:
        return CardResult("sweeteners", name, None, False)
    if not sweeteners:
        # No sweetener system detected.
        base = 55.0 if (added_sugar and added_sugar > 5) else 100.0
        score = round(base)
        return CardResult("sweeteners", name, score, True, _tier_tags(name, score))
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
    return CardResult("sweeteners", name, score, True, tags, {"anchor": anchor, "artificial": artificial})


# ── Actives-based cards (Clinical Dose / Stimulant / Pump / Recovery) ─────────
# Clinical-dose reference thresholds (sheet 03), converted to (grams-or-mg, unit).
CLINICAL_THRESHOLDS: Dict[str, Tuple[float, str, float]] = {
    # active_key: (threshold, unit, importance_weight)
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
    if not f.has_actives:
        return CardResult("clinical_dose", name, 25.0, True,
                          _tier_tags(name, 25.0) + ["undisclosed_dosages"], {"reason": "no_actives"})
    primary = _PRIMARY_ACTIVE_BY_COLUMN.get(f.weight_column or "")
    sub_scores: List[Tuple[str, float, float]] = []  # (key, score, weight)
    primary_ratio = None
    for raw_name in f.active_ingredients:
        key = _match_active_key(raw_name)
        if key is None or key not in CLINICAL_THRESHOLDS:
            continue
        threshold, tunit, weight = CLINICAL_THRESHOLDS[key]
        amt = _active_amount(f, key)
        if amt is None:
            continue
        val = _to_threshold_unit(amt[0], amt[1], tunit)
        if key == "citrulline" and "malate" in _norm_token(raw_name):
            val *= 0.5  # Citrulline Malate 2:1 -> pure citrulline (sheet 03 row 4)
        ratio = val / threshold if threshold else 0
        s = DOSE_RATIO_LADDER.score(ratio)
        sub_scores.append((key, s if s is not None else 15.0, weight))
        if key == primary:
            primary_ratio = ratio
    if not sub_scores:
        return CardResult("clinical_dose", name, 25.0, True, _tier_tags(name, 25.0) + ["undisclosed_dosages"])
    wsum = sum(w for _k, _s, w in sub_scores)
    score = sum(s * w for _k, s, w in sub_scores) / wsum
    tags = []
    if primary_ratio is not None and primary_ratio < 0.50:
        score = min(score, 40.0)  # hard cap (sheet 03 row 21)
        tags.append("underdosed")
    score = round(_clamp(score))
    return CardResult("clinical_dose", name, score, True, _tier_tags(name, score) + tags,
                      {"primary": primary, "primary_ratio": primary_ratio})


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
    bcaa_only = f.eaa_count and f.eaa_count < 9 and f.eaa_count <= 3
    if bcaa_only:
        score = min(score, 55.0)  # BCAA-only cap (sheet 01 row 15)
    score = round(_clamp(score))
    return CardResult("recovery_formula", name, score, True, _tier_tags(name, score))


def score_serving_honesty(f: SupplementFeatures) -> CardResult:
    """Serving Honesty (sheet 02 row 12 + worked example). Penalties constructed
    from the tag dictionary (inflated_serving_size) and derived-metric net-weight
    check; the workbook gives no explicit point table for this card, so the
    deductions below are documented assumptions consistent with the reachability
    floor of 35 and the worked example (standard serving -> 100)."""
    name = "Serving Honesty"
    score = 100.0
    tags: List[str] = []
    is_gainer = f.weight_column == "Mass Gainer"
    if f.serving_qty_g is not None and not is_gainer and f.serving_qty_g > 40:
        score -= 15
        tags.append("inflated_serving_size")
    if not f.scoop_stated:
        score -= 10
    if f.serving_qty_g and f.servings_per_container and f.pack_weight_g:
        implied = f.serving_qty_g * f.servings_per_container
        if abs(implied - f.pack_weight_g) / f.pack_weight_g > 0.03:
            score -= 20
    score = round(_clamp(score, lo=35.0))
    return CardResult("serving_honesty", name, score, True, _tier_tags(name, score) + tags)


CARD_SCORERS = {
    "protein_quality": score_protein_quality,
    "amino_acid_profile": score_amino_acid_profile,
    "protein_efficiency": score_protein_efficiency,
    "bioavailability": score_bioavailability,
    "digestibility": score_digestibility,
    "lean_formula": score_lean_formula,
    "purity": score_purity,
    "transparency": score_transparency,
    "testing_trust": score_testing_trust,
    "contaminant_safety": score_contaminant_safety,
    "sweeteners": score_sweeteners,
    "serving_honesty": score_serving_honesty,
    "clinical_dose": score_clinical_dose,
    "stimulant_balance": score_stimulant_balance,
    "pump_formula": score_pump_formula,
    "recovery_formula": score_recovery_formula,
}


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


def compute_supplement_scorecards(src: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Compute all applicable supplement cards + composite Flean Score.

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
    cards: Dict[str, CardResult] = {}
    for key, scorer in CARD_SCORERS.items():
        try:
            cards[key] = scorer(features)
        except Exception as exc:  # defensive: one bad card must not sink the PDP
            log.warning("SUPPLEMENT_CARD_ERROR | card=%s | error=%s", key, exc)
            cards[key] = CardResult(key, CARD_KEY_TO_NAME[key], None, False)

    # Composite: weighted mean of applicable (weight>0) & scorable cards, with
    # renormalisation + confidence deduction for NOT SCORABLE cards (sheet 02 A24).
    applicable = {k: w for k, w in weights.items() if w > 0}
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

    return {
        "leaf_category": features.leaf,
        "weight_column": features.weight_column,
        "cards": {k: _card_to_dict(c, applicable.get(k, 0.0)) for k, c in cards.items()},
        "flean_score": flean_score,
        "composite_before_deduction": round(composite, 2),
        "confidence_deduction": confidence_deduction,
        "cards_dropped": len(dropped),
        "dropped_cards": dropped,
        "tags": all_tags,
        "features": _features_summary(features),
    }


def _card_to_dict(c: CardResult, weight: float) -> Dict[str, Any]:
    if not c.scorable or c.score is None:
        return {"title": c.name, "scorable": False, "weight": weight, "score": None, "tags": c.tags}
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
    "lean_formula": "https://img.flean.ai/assets/Pdp-Icons/06.svg",
    "sweeteners": "https://img.flean.ai/assets/Pdp-Icons/03.svg",
    "digestibility": "https://img.flean.ai/assets/Pdp-Icons/gut1.svg",
    "additives": "https://img.flean.ai/assets/Pdp-Icons/additives1.svg",
}


def to_pdp_score_cards(result: Dict[str, Any]) -> Dict[str, Any]:
    """Adapt a compute_supplement_scorecards() result into the PDP ``score_cards``
    shape consumed by Flutter, plus a computed ``flean_badge`` and a
    ``supplement_scoring`` detail block.
    """
    cards_out: Dict[str, Any] = {}
    # Applicable (weight>0) cards ordered by descending weight; then display-only.
    entries = list(result["cards"].items())
    entries.sort(key=lambda kv: (kv[1].get("weight", 0.0) <= 0, -kv[1].get("weight", 0.0)))
    order = 0
    for key, c in entries:
        if not c.get("scorable"):
            continue
        weight = c.get("weight", 0.0)
        # Skip weight-0 cards except the display-only Bioavailability badge.
        if weight <= 0 and key != "bioavailability":
            continue
        order += 1
        card: Dict[str, Any] = {
            "title": c["title"],
            "value": c["value"],
            "subtitle": f"Score: {c['score']}",
            "score": c["score"],
            "percentile": None,
            "status": c["status"],
            "status_label": c["status_label"],
            "color": c["color"],
            "theme": c["theme"],
            "weight": weight,
            "display_only": weight <= 0,
            "tags": c.get("tags", []),
            "visible": True,
            "order": order,
        }
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
    }
