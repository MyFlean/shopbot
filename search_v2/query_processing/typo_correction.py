"""
search_v2/query_processing/typo_correction.py
─────────────────────────────────────────────────
Non-LLM typo/spelling correction, COMPLEMENTARY to (not a replacement for)
OpenSearch's own `fuzziness: AUTO` on the query side — see
retrieval/lexical_query_builder.py, which uses both. Why both:

  - `fuzziness: AUTO` is query-time, per-field, and only catches the specific
    typo against whatever's actually in the index for that field. It can't
    fix the query text itself, can't report "did you mean", and doesn't help
    phrase/phrase_prefix queries (which generally don't support fuzziness).
  - This module corrects against a CATALOG-DERIVED vocabulary (real product
    vocabulary, not a dictionary of English words), so "muscel" corrects
    toward "muscle" specifically because "muscle" is a real, frequent token
    in your catalog/category vocabulary — not because it's an English word.
  - It also catches a typo CLASS plain character-edit-distance fuzziness
    cannot: word-SEGMENTATION errors ("pine ape" -> "pineapple") — see
    `repair_segmentation()`.

Algorithm: frequency-weighted Damerau-Levenshtein lookup against a vocabulary
built from the real catalog (see vocabulary_builder.py). Deliberately NOT a
precomputed-deletes SymSpell index — at this catalog's scale (~8K products,
a vocabulary of a few thousand distinct meaningful tokens) and traffic
(<1 QPS), direct edit-distance comparison against the vocabulary is fast
enough and far simpler to read/maintain/test. If the vocabulary grows by an
order of magnitude or more, swap VocabularyCorrector's lookup for a
precomputed-deletes SymSpell index — the public interface
(`correct_token`/`correct_query`) wouldn't need to change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from search_v2.query_processing.text_normalization import normalize_text


def damerau_levenshtein(a: str, b: str, max_distance: Optional[int] = None) -> int:
    """Edit distance with transpositions counted as a single edit (so "muscel"
    -> "muscle" is distance 1, not 2 — plain Levenshtein would say 2)."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if max_distance is not None and abs(la - lb) > max_distance:
        return max_distance + 1

    d: Dict[Tuple[int, int], int] = {}
    for i in range(-1, la + 1):
        d[(i, -1)] = i + 1
    for j in range(-1, lb + 1):
        d[(-1, j)] = j + 1

    for i in range(la):
        for j in range(lb):
            cost = 0 if a[i] == b[j] else 1
            d[(i, j)] = min(
                d[(i - 1, j)] + 1,        # deletion
                d[(i, j - 1)] + 1,        # insertion
                d[(i - 1, j - 1)] + cost,  # substitution
            )
            if i > 0 and j > 0 and a[i] == b[j - 1] and a[i - 1] == b[j]:
                d[(i, j)] = min(d[(i, j)], d[(i - 2, j - 2)] + cost)  # transposition

    return d[(la - 1, lb - 1)]


# Minimum catalog frequency a merged-compound candidate must have to be
# trusted as a genuine segmentation error. Roughly 70% of the generated
# vocabulary (see vocabulary_builder.py's per-product brand+name compound
# entries) consists of frequency-1 tokens — full product names concatenated
# into one string, e.g. "aahadesihandmadeplainmathri" — that are essentially
# noise for this purpose. Without this floor, repair_segmentation() would
# happily "repair" any two-word query into whichever random freq-1 compound
# token happens to be within edit distance, silently overriding a more
# reliable per-token correction in the process (e.g. "glutten free" being
# pulled toward the freq-1 artifact "glutenfree" instead of the much more
# common, higher-confidence per-token fix "glutten" -> "gluten", freq 84).
# Genuine compound/brand terms (e.g. "pineapple", "muscleblaze", "ritebite")
# occur far more than once across a ~6-8K product catalog, so this floor
# costs essentially nothing for the cases the mechanism is meant to catch.
MIN_SEGMENTATION_FREQUENCY = 3


def _auto_fuzziness_budget(length: int) -> int:
    """Mirrors OpenSearch/Elasticsearch's own `fuzziness: AUTO` thresholds, so
    this corrector and the ES-level fuzzy clause agree on how much typo a given
    word length should tolerate: 0 edits below length 3, 1 edit for 3-5, 2 for 6+."""
    if length < 3:
        return 0
    if length < 6:
        return 1
    return 2


@dataclass
class CorrectionCandidate:
    original: str
    corrected: str
    distance: int
    frequency: int
    # Token span [start, end) this candidate covers — only set for
    # segmentation repairs (see repair_segmentation()), where the candidate
    # replaces MULTIPLE original tokens rather than one. None for ordinary
    # per-token corrections, which always cover exactly their own token.
    start: Optional[int] = None
    end: Optional[int] = None


@dataclass
class QueryCorrectionResult:
    original_query: str
    tokens: List[str] = field(default_factory=list)
    corrections: Dict[str, CorrectionCandidate] = field(default_factory=dict)  # original token -> candidate
    segmentation_repairs: List[CorrectionCandidate] = field(default_factory=list)  # "pine ape" -> "pineapple"

    def corrected_tokens(self) -> List[str]:
        """The fully corrected token list — folds in BOTH per-token
        corrections AND segmentation repairs (merged-span replacements).
        Segmentation repairs take priority over per-token corrections for the
        tokens they span (correct_query() never populates both for the same
        index — see its covered_indices logic)."""
        repair_by_start = {
            r.start: r for r in self.segmentation_repairs if r.start is not None and r.end is not None
        }
        result: List[str] = []
        i = 0
        n = len(self.tokens)
        while i < n:
            repair = repair_by_start.get(i)
            if repair is not None:
                result.append(repair.corrected)
                i = repair.end
                continue
            token = self.tokens[i]
            result.append(self.corrections[token].corrected if token in self.corrections else token)
            i += 1
        return result

    def has_any_correction(self) -> bool:
        return bool(self.corrections) or bool(self.segmentation_repairs)


class VocabularyCorrector:
    def __init__(self, vocabulary: Dict[str, int]):
        """`vocabulary`: {normalized_token: frequency_count}. See
        vocabulary_builder.py for how this gets built from the real catalog."""
        self.vocabulary = vocabulary

    def correct_token(self, token: str, max_candidates: int = 3) -> Optional[CorrectionCandidate]:
        token = normalize_text(token)
        if not token:
            return None

        # A token already present in the vocabulary is normally left alone —
        # but the vocabulary is built from raw catalog text (see
        # vocabulary_builder.py) and inherits whatever misspellings exist in
        # real product listings, e.g. "protien" itself shows up as a
        # frequency-1 token because some product's name/description contains
        # that exact typo. Treating vocabulary membership alone as "correct"
        # means a user typing that same typo never gets fixed. So: only
        # short-circuit here when the existing entry is well-attested
        # (frequency >= MIN_SEGMENTATION_FREQUENCY); otherwise fall through
        # and see if a meaningfully more common neighbor exists.
        existing_freq = self.vocabulary.get(token)
        if existing_freq is not None and existing_freq >= MIN_SEGMENTATION_FREQUENCY:
            return None  # a well-attested real word — nothing to correct

        budget = _auto_fuzziness_budget(len(token))
        if budget == 0:
            return None  # too short to safely guess at

        best: List[CorrectionCandidate] = []
        for word, freq in self.vocabulary.items():
            if word == token:
                continue  # don't "correct" a token to itself
            if abs(len(word) - len(token)) > budget:
                continue
            dist = damerau_levenshtein(token, word, max_distance=budget)
            if dist <= budget:
                best.append(CorrectionCandidate(original=token, corrected=word, distance=dist, frequency=freq))

        if not best:
            return None

        # Prefer the smallest edit distance, then the most frequent catalog term
        # (so "aple" prefers "apple" over some rarer 1-edit-away word).
        best.sort(key=lambda c: (c.distance, -c.frequency))
        top = best[0]

        # If the token itself is already a legitimate (if rare) vocabulary
        # entry, only "correct" it when a candidate is convincingly more
        # common — otherwise we'd be rewriting one real word into another.
        if existing_freq is not None and top.frequency <= existing_freq:
            return None

        return top

    def repair_segmentation(self, tokens: List[str], window: int = 2) -> List[Tuple[int, int, CorrectionCandidate]]:
        """Tries merging adjacent tokens (e.g. "pine" + "ape" -> "pineape") and
        checks whether the merged form is itself a near-miss for a real
        vocabulary word ("pineapple") — catches typos that are a SPACING error
        rather than a character error, which edit-distance-per-token alone
        cannot, since neither "pine" nor "ape" individually looks like a typo
        of anything in particular.

        Returns (start_index, end_index_exclusive, candidate) tuples so callers
        can identify which token SPAN each repair covers — see correct_query(),
        which uses this to suppress individual per-token corrections for tokens
        already explained by a (more specific, more confident) segmentation
        repair."""
        repairs: List[Tuple[int, int, CorrectionCandidate]] = []
        for i in range(len(tokens) - 1):
            for span in range(2, min(window, len(tokens) - i) + 1):
                if all(
                    (self.vocabulary.get(t) or 0) >= MIN_SEGMENTATION_FREQUENCY
                    for t in tokens[i : i + span]
                ):
                    continue
                merged = "".join(tokens[i : i + span])
                merged_freq = self.vocabulary.get(merged)
                if merged_freq is not None and merged_freq >= MIN_SEGMENTATION_FREQUENCY:
                    # The merged form is a real, non-noise vocabulary term —
                    # this IS a segmentation error (user inserted a space into
                    # a brand/compound word, e.g. "rite bite" → "ritebite").
                    # Emit a distance-0 repair so the tokens are marked
                    # covered and individual-token correction (which would
                    # otherwise fire and corrupt "rite" → "rice") is
                    # suppressed.
                    candidate = CorrectionCandidate(
                        original=merged, corrected=merged, distance=0,
                        frequency=merged_freq, start=i, end=i + span,
                    )
                    repairs.append((i, i + span, candidate))
                    continue
                budget = _auto_fuzziness_budget(len(merged))
                if budget == 0:
                    continue
                best: Optional[CorrectionCandidate] = None
                for word, freq in self.vocabulary.items():
                    if freq < MIN_SEGMENTATION_FREQUENCY:
                        continue
                    if abs(len(word) - len(merged)) > budget:
                        continue
                    dist = damerau_levenshtein(merged, word, max_distance=budget)
                    if dist <= budget and (best is None or dist < best.distance):
                        best = CorrectionCandidate(original=merged, corrected=word, distance=dist, frequency=freq)
                if best:
                    best.start, best.end = i, i + span
                    repairs.append((i, i + span, best))
        return repairs

    def correct_query(self, query_text: str) -> QueryCorrectionResult:
        normalized = normalize_text(query_text)
        tokens = normalized.split()
        result = QueryCorrectionResult(original_query=query_text, tokens=tokens)

        covered_indices = set()
        if len(tokens) >= 2:
            for start, end, candidate in self.repair_segmentation(tokens):
                result.segmentation_repairs.append(candidate)
                covered_indices.update(range(start, end))

        for idx, token in enumerate(tokens):
            if idx in covered_indices:
                continue  # already explained by a segmentation repair — see above
            candidate = self.correct_token(token)
            if candidate:
                result.corrections[token] = candidate

        return result
