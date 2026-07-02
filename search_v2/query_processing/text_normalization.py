"""
search_v2/query_processing/text_normalization.py
─────────────────────────────────────────────────────
Canonical text normalization for Search V2. This is intentionally the ONE place
this logic lives — synonyms/synonym_builder.py imports it to normalize terms
before building equivalence groups, and the live query pipeline (this same
package) imports it to normalize incoming queries. If these two ever used
different normalization, a synonym file entry could silently stop matching
real queries (e.g. the synonym file has "café" but the query analyzer folds
accents and the query becomes "cafe" — a classic, hard-to-notice production bug).

Stages, in order (each individually disable-able for debugging/benchmarking):
    1. Unicode normalization (NFKC) — collapses visually-identical characters
       that are different code points (full-width punctuation, compatibility
       characters) to one canonical form.
    2. Lowercasing
    3. Punctuation cleanup — strips punctuation that doesn't carry search
       meaning, but preserves a few characters that do (e.g. "-" in
       "sugar-free", "%" in "2% milk") — see PRESERVED_PUNCTUATION.
    4. Whitespace cleanup — collapse runs of whitespace, strip ends.

No LLM, no network call, no external service — pure Python + stdlib `unicodedata`.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Punctuation that changes search meaning and should NOT be stripped outright.
# These are handled by adding a space around them (so "sugar-free" tokenizes as
# "sugar" "free" via the tokenizer, AND the literal "-" survives for analyzers
# that care) rather than deleting them, which would merge "sugar-free" into
# "sugarfree" and break matching against text that has the hyphen.
PRESERVED_PUNCTUATION = "-%+"

_PUNCT_STRIP_RE = re.compile(r"[^\w\s" + re.escape(PRESERVED_PUNCTUATION) + r"]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class NormalizationOptions:
    unicode_normalize: bool = True
    lowercase: bool = True
    strip_punctuation: bool = True
    collapse_whitespace: bool = True


DEFAULT_OPTIONS = NormalizationOptions()


def normalize_text(text: str, options: NormalizationOptions = DEFAULT_OPTIONS) -> str:
    """The single normalization function everything else in Search V2 calls."""
    if not text:
        return ""
    result = text

    if options.unicode_normalize:
        result = unicodedata.normalize("NFKC", result)

    if options.lowercase:
        result = result.lower()

    if options.strip_punctuation:
        result = _PUNCT_STRIP_RE.sub(" ", result)

    if options.collapse_whitespace:
        result = _WS_RE.sub(" ", result).strip()

    return result


def normalize_term_for_synonyms(text: str) -> str:
    """
    Slightly stricter than normalize_text(): synonym file entries should be
    plain lowercase tokens/phrases with no preserved punctuation at all, since
    OpenSearch/Elasticsearch synonym files match against ANALYZED tokens, and
    most analyzers strip punctuation anyway. Keeping synonym-file terms in this
    stricter form avoids a synonym rule silently never firing because the
    indexed token stream never contains a "-" the synonym file expects.
    """
    strict_options = NormalizationOptions(
        unicode_normalize=True, lowercase=True, strip_punctuation=True, collapse_whitespace=True
    )
    result = normalize_text(text, strict_options)
    # Also strip the preserved punctuation characters here specifically —
    # normalize_text() keeps them by design for query/index text, but synonym
    # terms need the fully-stripped form to match analyzed tokens reliably.
    for ch in PRESERVED_PUNCTUATION:
        result = result.replace(ch, " ")
    return _WS_RE.sub(" ", result).strip()
