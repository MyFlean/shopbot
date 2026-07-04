"""
search_v2/query_processing/vocabulary_builder.py
───────────────────────────────────────────────────
Builds the {token: frequency} vocabulary that typo_correction.VocabularyCorrector
looks up against.

Two ways to get one:

  1. build_vocabulary_from_mongo() — the real one. Reuses the existing MongoDB
     connection/read pattern (same idea as index.products-v3.py's cursor over
     the product collection — no new indexing pipeline, just a read-only token
     count over the same fields already used for indexing: name, brand,
     description, descriptive_tags). Run this once you have DB access; it's
     intentionally NOT run automatically anywhere in this milestone, since
     this sandbox has no network/DB access to actually run it.

  2. seed_vocabulary() — a functional starter vocabulary so typo correction is
     testable today: every term already in the merged synonym system (high
     confidence — these are real catalog-relevant terms) plus a hand-curated
     list of common grocery/personal-care/nutrition vocabulary covering the
     brief's own example queries (apple, protein, watermelon, muscle,
     pineapple, ...). REPLACE with build_vocabulary_from_mongo()'s output once
     you have it — see merge_vocabularies() to combine both rather than
     picking one.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from search_v2.query_processing.text_normalization import normalize_text

_WORD_RE = re.compile(r"[a-zA-Z]+")

# Canonical on-disk path for the generated vocabulary — shared between the
# indexer (which writes it) and the runtime corrector (which reads it) so
# there is exactly one place to change if the file ever moves.
VOCABULARY_PATH: Path = Path(__file__).resolve().parent / "vocabulary.json"

# ── Noise controls (generic — length/count-based, no product/brand names) ───
# A single "word" longer than this is essentially never a real token typed by
# a user or found in a dictionary — it's almost always an indexing artifact
# (e.g. a run-on OCR'd ingredient string). Applies uniformly to every token
# from every field, not just the compound-generation path below.
MAX_TOKEN_LENGTH = 24

# Compound generation (joining adjacent words into one token, e.g. "rite" +
# "bite" -> "ritebite") exists ONLY to let typo_correction.py's segmentation
# repair catch a real 2-3 word brand/compound term that a user typed with an
# errant space (see repair_segmentation()'s docstring). It is NOT meant to
# concatenate an entire multi-word product name into one token — "Aaha Desi
# Handmade Plain Mathri" becoming a single 25-character token is noise, not a
# compound-word candidate (no realistic typo collapses 5 words into one, and
# such full-name compounds are near-universally unique — see MIN_KEEP_FREQUENCY
# below for why that matters). Bounding the word count catches the intended
# case while excluding this one.
MIN_COMPOUND_WORDS = 2
MAX_COMPOUND_WORDS = 3

# Tokens that appear only once anywhere in the ENTIRE catalog and aren't
# otherwise trusted (seed vocabulary / synonym-derived) are overwhelmingly
# noise — misspellings, OCR artifacts, or one-off compound names — rather
# than real vocabulary. See prune_noise(), applied once after merging all
# sources, not per-field here (a legitimate rare term can still accumulate
# frequency across MULTIPLE products/fields before pruning runs).
MIN_KEEP_FREQUENCY = 2


def load_vocabulary(path: Path = VOCABULARY_PATH) -> Dict[str, int]:
    """Load the generated vocabulary from disk.  Returns an empty dict when the
    file does not yet exist — callers should fall back to seed_vocabulary()."""
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_vocabulary(vocab: Dict[str, int], path: Path = VOCABULARY_PATH) -> None:
    """Write vocabulary to disk as a sorted JSON file (deterministic diffs)."""
    path.write_text(json.dumps(vocab, indent=2, sort_keys=True), encoding="utf-8")

# Curated starter vocabulary — common grocery / nutrition / personal-care
# terms, deliberately including every word from the brief's own example
# queries so typo correction for those examples works out of the box.
SEED_TERMS = [
    "apple", "apples", "banana", "mango", "watermelon", "pineapple", "orange",
    "grapes", "papaya", "guava", "pomegranate", "lemon", "lime", "strawberry",
    "kiwi", "pear", "peach", "plum", "cherry", "fig", "coconut", "avocado",
    "protein", "proteins", "carbohydrate", "carbohydrates", "fiber", "fibre",
    "vitamin", "vitamins", "mineral", "minerals", "calcium", "iron", "calorie",
    "calories", "nutrition", "nutritional", "supplement", "supplements",
    "muscle", "muscles", "gain", "weight", "loss", "energy", "immunity",
    "immune", "wellness", "fitness", "workout", "recovery", "endurance",
    "milk", "coffee", "tea", "chocolate", "biscuit", "biscuits", "cookie",
    "cookies", "chips", "crisps", "snack", "snacks", "namkeen", "noodles",
    "pasta", "rice", "wheat", "flour", "atta", "oats", "cereal", "cereals",
    "bread", "butter", "cheese", "yogurt", "yoghurt", "curd", "paneer",
    "ghee", "oil", "sugar", "salt", "spice", "spices", "masala", "pickle",
    "sauce", "ketchup", "jam", "honey", "juice", "beverage", "beverages",
    "drink", "drinks", "soda", "water", "vinegar", "cider", "syrup",
    "vegetable", "vegetables", "fruit", "fruits", "potato", "onion",
    "tomato", "carrot", "spinach", "cabbage", "cauliflower", "broccoli",
    "cucumber", "pumpkin", "garlic", "ginger", "chilli", "chili", "pepper",
    "coriander", "mint", "lentil", "lentils", "bean", "beans", "chickpea",
    "chickpeas", "soap", "shampoo", "conditioner", "lotion", "cream",
    "moisturizer", "moisturiser", "sunscreen", "serum", "facewash",
    "toothpaste", "deodorant", "perfume", "lipstick", "skincare", "haircare",
    "healthy", "organic", "fresh", "natural", "diet", "vegan", "vegetarian",
    "gluten", "lactose", "sugarfree", "wholegrain", "multigrain",
    # Price prepositions — absent from catalogue text, must not be "corrected"
    # (e.g. "under" → "sunder", "above" → anything else)
    "under", "above", "below", "over",
    # Fitness/intent words — absent from catalogue; "gym" corrects to "gum" without this
    "gym", "exercise",
]


def seed_vocabulary() -> Dict[str, int]:
    """A flat starter vocabulary, every term at frequency 1 (no real frequency
    signal exists yet without the real catalog) — sufficient for the corrector
    to function, but see merge_vocabularies() to fold in real frequencies once
    available."""
    return {normalize_text(t): 1 for t in SEED_TERMS if t}


def vocabulary_from_synonym_groups(synonym_debug_json_path: Path) -> Dict[str, int]:
    """Pulls every individual term out of the merged synonym groups (the
    output of synonyms/synonym_builder.py's write_debug_json()) into a flat
    vocabulary. These are real catalog-derived terms (from the vernacular JSON
    and the existing-repo sources), so they're high-confidence even before
    a real Mongo-derived frequency count exists."""
    if not synonym_debug_json_path.exists():
        return {}
    groups = json.loads(synonym_debug_json_path.read_text(encoding="utf-8"))
    vocab: Counter = Counter()
    for group in groups:
        for term in group.get("terms", []):
            for word in _WORD_RE.findall(term):
                vocab[word.lower()] += 1
    return dict(vocab)


def vocabulary_from_synonym_lines(lines: Iterable[str]) -> Dict[str, int]:
    """Extract vocabulary from in-memory synonym file lines (comma-separated
    term groups, one group per line).  In-process alternative to
    vocabulary_from_synonym_groups() that avoids writing synonyms_debug.json
    to disk just to read it back — used by the indexer which already has the
    synonym lines in memory."""
    vocab: Counter = Counter()
    for line in lines:
        if line.startswith("#"):
            continue
        for phrase in line.split(","):
            for word in _WORD_RE.findall(phrase.lower()):
                if len(word) >= 2:
                    vocab[word] += 1
    return dict(vocab)


def merge_vocabularies(*vocabs: Dict[str, int]) -> Dict[str, int]:
    merged: Counter = Counter()
    for vocab in vocabs:
        for term, freq in vocab.items():
            merged[term] += freq
    return dict(merged)


def prune_noise(
    vocab: Dict[str, int],
    trusted_terms: Optional[Iterable[str]] = None,
    min_frequency: int = MIN_KEEP_FREQUENCY,
) -> Dict[str, int]:
    """Drop tokens occurring fewer than `min_frequency` times across the
    WHOLE catalog, unless already trusted (present in the curated seed
    vocabulary or synonym-derived vocabulary — correct by construction
    regardless of how rarely they occur). Frequency-1 catalog-derived tokens
    are overwhelmingly noise: misspellings, OCR/data-entry artifacts, or
    one-off compound names — this is where the indexing pipeline removes the
    bulk of that noise AT THE SOURCE, rather than relying solely on runtime
    safeguards in typo_correction.py (MIN_SEGMENTATION_FREQUENCY there is a
    second, independent layer of defense — this is the first)."""
    trusted = set(trusted_terms or ())
    return {
        term: freq
        for term, freq in vocab.items()
        if freq >= min_frequency or term in trusted
    }


def build_and_write_vocabulary(
    mongo_uri: str,
    mongo_db: str,
    mongo_collection: str,
    synonym_lines: Optional[Iterable[str]] = None,
    path: Path = VOCABULARY_PATH,
) -> Dict[str, int]:
    """Build the merged vocabulary (seed + synonyms + Mongo catalog) and write
    it to *path*.  This is the single call the indexer makes for full ``all``
    runs.  Also directly callable by tests without going through main().
    Returns the merged vocabulary so the caller can log ``len(merged)``."""
    seed = seed_vocabulary()
    trusted = set(seed)
    vocabs: list = [seed]
    if synonym_lines is not None:
        synonym_vocab = vocabulary_from_synonym_lines(synonym_lines)
        trusted.update(synonym_vocab)
        vocabs.append(synonym_vocab)
    mongo_vocab = build_vocabulary_from_mongo(mongo_uri, mongo_db, mongo_collection)
    vocabs.append(prune_noise(mongo_vocab, trusted_terms=trusted))
    merged = merge_vocabularies(*vocabs)
    write_vocabulary(merged, path)
    return merged


def _add_tokens(vocab: "Counter[str]", text: str, allow_compound: bool) -> None:
    """Tokenize `text` and add its words (and, when allowed, a bounded
    2-3-word compound) to `vocab`. Shared by both the scalar-field and
    list-field branches below so the noise-control rules only live in ONE
    place."""
    words = [w for w in _WORD_RE.findall(text.lower()) if 2 <= len(w) <= MAX_TOKEN_LENGTH]
    for word in words:
        vocab[word] += 1
    if allow_compound and MIN_COMPOUND_WORDS <= len(words) <= MAX_COMPOUND_WORDS:
        compound = "".join(words)
        if len(compound) >= 4 and len(compound) <= MAX_TOKEN_LENGTH:
            vocab[compound] += 1


def build_vocabulary_from_mongo(
    mongo_uri: str,
    db_name: str,
    collection_name: str,
    fields: Iterable[str] = ("name", "brand", "description", "descriptive_tags"),
    mongo_filter: Optional[Dict[str, Any]] = None,
    limit: Optional[int] = None,
) -> Dict[str, int]:
    """
    The REAL vocabulary builder — reuses the existing read-only MongoDB access
    pattern (same connection style as index.products-v3.py), counting token
    frequency across the actual catalog. Not run anywhere in this milestone
    (no DB access in this sandbox) — run this yourself once you have
    connectivity, then pass its output into merge_vocabularies() alongside
    seed_vocabulary() and vocabulary_from_synonym_groups().

    Deliberately does NOT touch or duplicate the indexing pipeline itself —
    this is a read-only pass over the same collection, can run independently
    of (before, after, or never touching) an actual index build.
    """
    from pymongo import MongoClient  # deferred import — optional dependency for this one function

    client = MongoClient(mongo_uri)
    collection = client[db_name][collection_name]

    vocab: Counter = Counter()
    cursor = collection.find(mongo_filter or {}, {f: 1 for f in fields})
    if limit:
        cursor = cursor.limit(limit)

    # Brand and name fields get their concatenated 2-3-word form added as
    # well as individual tokens.  This lets the segmentation repair in
    # typo_correction.py catch queries like "muscle blaze" → "muscleblaze"
    # or "rite bite" → "ritebite" as exact-match (distance-0) repairs.
    # Single-word camel-case brands (e.g. "RiteBite") already land here as
    # one token after lowercasing, so they need no special treatment.
    _compound_fields = frozenset(("brand", "name"))

    for doc in cursor:
        for field_name in fields:
            value = doc.get(field_name)
            allow_compound = field_name in _compound_fields
            if isinstance(value, str):
                _add_tokens(vocab, value, allow_compound)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        _add_tokens(vocab, item, allow_compound)

    client.close()
    return dict(vocab)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synonym-debug-json", default=None,
                         help="Path to synonyms/output/synonyms_debug.json")
    parser.add_argument("--out", default=str(VOCABULARY_PATH))
    parser.add_argument("--mongo-uri", default=None, help="If set, also pulls real catalog vocabulary")
    parser.add_argument("--mongo-db", default="flean")
    parser.add_argument("--mongo-collection", default="products_master")
    args = parser.parse_args()

    seed = seed_vocabulary()
    trusted = set(seed)
    vocabs = [seed]
    if args.synonym_debug_json:
        synonym_vocab = vocabulary_from_synonym_groups(Path(args.synonym_debug_json))
        trusted.update(synonym_vocab)
        vocabs.append(synonym_vocab)
    if args.mongo_uri:
        mongo_vocab = build_vocabulary_from_mongo(args.mongo_uri, args.mongo_db, args.mongo_collection)
        vocabs.append(prune_noise(mongo_vocab, trusted_terms=trusted))

    merged = merge_vocabularies(*vocabs)
    out_path = Path(args.out)
    write_vocabulary(merged, out_path)
    print(f"Wrote {len(merged)} vocabulary terms -> {out_path}")
