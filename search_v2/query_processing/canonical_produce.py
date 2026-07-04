"""
search_v2/query_processing/canonical_produce.py
─────────────────────────────────────────────────
Fresh Produce Identification — curated vernacular fruit/vegetable data
treated as AUTHORITATIVE knowledge, not just a text-alias lookup.

produce_synonyms.json (alongside this file) is a curated, vernacular
fruit/vegetable synonym list maintained OUTSIDE this codebase and copied in
verbatim — it is the ONLY source of truth for this feature. This module does
not transform it into another file; it is parsed into in-memory structures
once and never written back to disk.

Schema of produce_synonyms.json: a JSON array of catalog SKU entries
    {"id": "01K...", "name": "Potato (Aloo)", "synonyms": ["Aloo", "Aalu", ...]}

Each entry's "id" is a real catalog product id (verified against the live
index — not every curated id is currently stocked, but every id that IS
stocked matches a real document's own "id" field exactly).

── Why "family", not "one canonical string per alias" ──────────────────────
A single alias frequently spans MANY distinct catalog SKUs that are all
genuinely the same produce concept — e.g. "Aam" is listed on ~20 different
mango-variety entries (Kesar Mango, Alphonso Mango, Langra Mango, Raw
Mango, ...). The business requirement is that querying "aam" retrieves
*all* of them and nothing else — not one arbitrarily-chosen variety, and
not any processed food that merely contains the word "mango".

That means the unit this module needs to produce isn't a canonical STRING
to text-match against — it's a FAMILY: the authoritative set of catalog ids
belonging to that produce concept. Retrieval then hard-restricts to exactly
that id set (see product_intent_extractor.py / retrieval/filters.py's
SearchFilters.product_ids), which cannot admit a processed food no matter
what words its name contains, because a processed food's id is simply never
a member of the family's id set.

── How families are formed — deterministic, order-independent ─────────────
Two catalog entries belong to the same family if they share at least one
alias string (case/punctuation-insensitive). This is a graph connectivity
problem: entries are nodes, a shared alias is an edge, and each connected
component is one family. Connected-component membership is a pure function
of the "shares an alias with" relation — it does NOT depend on which order
entries appear in the JSON file, unlike the previous per-alias
shared-word-intersection heuristic (see git history / prior design), which
could silently pick a different "shortest candidate" if the file were
reordered. Adding a new SKU to the curated JSON with the right synonyms
automatically slots it into the right family (or forms a new one) with zero
code changes.

── Canonical display name — majority vote, alphabetical tie-break ─────────
Each family still needs a single human-readable label for reporting/UX
(product_intent.primary_product). This is derived by counting how many of
the family's member names (parenthetical vernacular hints stripped) contain
each stemmed word, and picking the word with the highest count — ties
broken alphabetically by the word itself, never by JSON position. For any
family with more than a couple of members this converges on the obvious
shared noun ("mango", "potato", "cucumber", "tomato", "muskmelon", ...).
This label is NOT used for retrieval (member_ids is) — it is purely
informational, so even in a small family with no clear majority (e.g. a
2-member family sharing no common word), the alphabetical tie-break just
needs to be *deterministic*, not perfect.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

# The curated JSON lives here, copied in verbatim from the source vernacular
# list. This is the single physical location of the source of truth for
# this feature — see module docstring.
PRODUCE_SYNONYMS_PATH: Path = Path(__file__).resolve().parent / "produce_synonyms.json"

_WORD_RE = re.compile(r"[a-zA-Z]+")
_PAREN_RE = re.compile(r"\([^)]*\)")


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — the same tokenize
    + rejoin approach product_intent_extractor.py uses, so a query and an
    alias normalize identically regardless of surface punctuation/casing."""
    return " ".join(_WORD_RE.findall(text.lower()))


def _strip_name(raw_name: str) -> str:
    """'Potato (Aloo)' -> 'potato'; 'Kutch Kesar Mango' -> 'kutch kesar mango'."""
    return _normalize(_PAREN_RE.sub("", raw_name))


def _stem(word: str) -> str:
    """Light plural fold so e.g. 'Cherry Tomatoes' is recognized as sharing
    'tomato' with 'Hybrid Tomato' — mirrors the mild singularization
    product_intent_extractor.py's resolve_head_term() already applies."""
    if word.endswith("es") and len(word) > 4:
        return word[:-2]
    if word.endswith("s") and len(word) > 3:
        return word[:-1]
    return word


@dataclass(frozen=True)
class FreshProduceFamily:
    """One connected group of curated catalog SKUs the vernacular data
    considers the same produce concept.

    canonical_name — deterministic, human-readable label (reporting/UX only).
    member_ids     — the authoritative catalog product ids belonging to this
                      family. Retrieval hard-restricts to exactly this set
                      (see SearchFilters.product_ids) — this, not
                      canonical_name, is what makes retrieval precise.
    """
    canonical_name: str
    member_ids: Tuple[str, ...]


class _UnionFind:
    """Standard disjoint-set over integer indices 0..n-1. The resulting
    PARTITION (which indices end up grouped together) is a pure function of
    the union operations performed — independent of the order they're
    performed in — so the family membership derived from it never depends
    on JSON array order."""

    def __init__(self, n: int):
        self._parent = list(range(n))

    def find(self, x: int) -> int:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # Lower index always becomes the root — an arbitrary but fixed
            # rule, used only internally; final family membership does not
            # depend on it (see class docstring).
            if ra < rb:
                self._parent[rb] = ra
            else:
                self._parent[ra] = rb


def _majority_word(names: List[str]) -> str:
    """Deterministically derive one representative label for a family.

    Step 1 finds the single STEMMED word with the highest coverage across
    member names (present anywhere in the name, not just trailing — some
    curated names put the head noun first, e.g. "Potato Carisma"), ties
    broken alphabetically by the word itself (never by input order).

    Step 2 greedily extends that single word into a longer phrase ONLY when
    doing so is unanimous: if every member name that contains the winning
    word also has the SAME adjacent word in the SAME direction immediately
    next to it, the phrase grows to include it (repeated in both
    directions until no further unanimous extension exists). This is what
    turns a bare "finger" (which wins step 1 over "lady" only because it's
    alphabetically first) into "lady finger" — every member containing
    "finger" has "lady" immediately before it — while leaving a case like
    "potato" alone, since "Potato Carisma"/"Red Potato"/"Baby Potato"/etc.
    disagree on what's adjacent.
    """
    word_lists = [name.split() for name in names]

    stem_counts: Counter = Counter()
    surface_for_stem: Dict[str, Counter] = {}
    for words in word_lists:
        seen_stems = set()
        for word in words:
            stem = _stem(word)
            if stem in seen_stems:
                continue  # count each distinct word at most once per name
            seen_stems.add(stem)
            stem_counts[stem] += 1
            surface_for_stem.setdefault(stem, Counter())[word] += 1

    if not stem_counts:
        return ""

    max_count = max(stem_counts.values())
    winning_stem = sorted(s for s, c in stem_counts.items() if c == max_count)[0]
    surfaces = surface_for_stem[winning_stem]
    max_surface_count = max(surfaces.values())
    winning_surface = sorted(w for w, c in surfaces.items() if c == max_surface_count)[0]

    # Supporting set: the word-index-lists of every member name containing
    # the winning stem (first occurrence per name).
    supporting: List[Tuple[List[str], int]] = []
    for words in word_lists:
        for idx, word in enumerate(words):
            if _stem(word) == winning_stem:
                supporting.append((words, idx))
                break

    phrase = [winning_surface]
    start_offset = 0  # index of phrase[0] within each supporting words-list, relative to the matched word
    for direction in ("before", "after"):
        while True:
            candidates = set()
            ok = True
            for words, idx in supporting:
                pos = idx - start_offset if direction == "before" else idx + (len(phrase) - 1 - start_offset)
                neighbor_pos = pos - 1 if direction == "before" else pos + 1
                if neighbor_pos < 0 or neighbor_pos >= len(words):
                    ok = False
                    break
                candidates.add(words[neighbor_pos])
            if not ok or len(candidates) != 1:
                break
            neighbor_word = next(iter(candidates))
            if direction == "before":
                phrase.insert(0, neighbor_word)
                start_offset += 1
            else:
                phrase.append(neighbor_word)

    return " ".join(phrase)


def load_produce_alias_map(path: Path = PRODUCE_SYNONYMS_PATH) -> Dict[str, FreshProduceFamily]:
    """
    Parse produce_synonyms.json ONCE into a flat, in-memory
    {alias(normalized): FreshProduceFamily} dictionary. Every alias and
    every member name belonging to a family maps to that SAME
    FreshProduceFamily instance.

    Returns an empty dict (feature no-ops) if the file is missing or
    unparsable, exactly like load_product_type_lexicon() does for a missing
    lexicon — never raises.
    """
    if not path.exists():
        return {}
    try:
        raw_entries = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

    entries: List[Dict[str, object]] = []
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        entry_id = entry.get("id")
        raw_name = str(entry.get("name") or "")
        name = _strip_name(raw_name)
        if not entry_id or not name:
            continue
        # Both the raw (unstripped) name and the parenthetical-stripped form
        # are registered as aliases — e.g. "Lady Finger (Bhindi)" contributes
        # both "lady finger bhindi" and "lady finger", so a bare "lady
        # finger" query resolves via a direct member alias rather than
        # depending on the derived canonical label happening to match it.
        aliases = {_normalize(str(a)) for a in (list(entry.get("synonyms") or []) + [raw_name])}
        aliases.add(name)
        aliases.discard("")
        if not aliases:
            continue
        entries.append({"id": str(entry_id), "name": name, "aliases": aliases})

    n = len(entries)
    uf = _UnionFind(n)

    # Union every pair of entries that share an alias. Processing aliases in
    # a fixed (sorted) order doesn't change the resulting partition — union
    # of a relation is associative/commutative — it's just here for
    # reproducible iteration, not correctness.
    alias_to_first_index: Dict[str, int] = {}
    for i, e in enumerate(entries):
        for alias in e["aliases"]:  # type: ignore[union-attr]
            if alias in alias_to_first_index:
                uf.union(alias_to_first_index[alias], i)
            else:
                alias_to_first_index[alias] = i

    components: Dict[int, List[int]] = {}
    for i in range(n):
        components.setdefault(uf.find(i), []).append(i)

    alias_map: Dict[str, FreshProduceFamily] = {}
    for indices in components.values():
        members = [entries[i] for i in indices]
        member_ids = tuple(sorted({m["id"] for m in members}))  # type: ignore[arg-type]
        member_names = [m["name"] for m in members]  # type: ignore[misc]
        canonical = _majority_word(member_names) or sorted(set(member_names))[0]
        family = FreshProduceFamily(canonical_name=canonical, member_ids=member_ids)

        family_aliases: set = {canonical}
        for m in members:
            family_aliases |= m["aliases"]  # type: ignore[operator]
        for alias in family_aliases:
            alias_map[alias] = family

    return alias_map


def fuzzy_match_produce_alias(text: str, alias_map: Dict[str, "FreshProduceFamily"]):
    """
    Fallback for produce queries generic typo correction only partially
    resolves. Root cause: VocabularyCorrector (typo_correction.py) does a
    single nearest-vocabulary-word hop per token. A double-typo query like
    "pyaaj" can land one edit away from a DIFFERENT, unrelated word that
    happens to also be in the catalog vocabulary (e.g. "pyaaz" — a real but
    obscure catalog token) while the intended curated alias ("pyaz") is two
    edits away — the corrector stops at the first valid vocabulary word it
    finds and has no reason to keep going. This is a generic property of
    single-pass nearest-neighbor correction, not specific to any one word.

    Rather than teaching the general corrector about produce specifically,
    this tries a second, independent match directly against the curated
    alias set — using the SAME length-scaled edit-distance budget as
    OpenSearch's own `fuzziness: AUTO` and this codebase's
    _auto_fuzziness_budget() (0 edits below length 3, 1 edit for 3-5, 2 for
    6+), so produce queries get exactly as much typo tolerance as everything
    else in the pipeline — no more, no less. Only called on an EXACT-match
    miss (see ProductIntentExtractor.extract()), so it costs nothing for the
    overwhelming majority of queries that either match exactly or aren't
    produce at all.

    Returns None when nothing in the curated alias set is within that
    small, length-proportional distance — this bound is what keeps
    unrelated queries ("granola bar", "greek yogurt") from ever matching by
    coincidence; ties broken alphabetically by alias for determinism.
    """
    if not text or not alias_map:
        return None

    from search_v2.query_processing.typo_correction import _auto_fuzziness_budget, damerau_levenshtein

    budget = _auto_fuzziness_budget(len(text))
    if budget == 0:
        return None

    best_family = None
    best_alias = None
    best_distance = budget + 1
    for alias, family in alias_map.items():
        if abs(len(alias) - len(text)) > budget:
            continue
        dist = damerau_levenshtein(text, alias, max_distance=budget)
        if dist > budget:
            continue
        if dist < best_distance or (dist == best_distance and (best_alias is None or alias < best_alias)):
            best_distance = dist
            best_family = family
            best_alias = alias

    return best_family
