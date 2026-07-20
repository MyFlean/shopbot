from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from search_v2.query_processing.typo_correction import VocabularyCorrector

VOCAB = {
    "apple": 168,
    "tomato": 273,
    "banana": 170,
    "protein": 1628,
    "protien": 1,
    "amul": 1011,
    "milk": 910,
    "yogurt": 222,
    "paneer": 138,
    "cheese": 678,
    "granola": 61,
    "grain": 37,
    "gain": 1,
    "weight": 12,
    "fit": 64,
    "fat": 195,
    "jain": 40,
    "baking": 42,
    "ritebite": 205,
    "rite": 3,
    "bite": 66,
}


@pytest.fixture
def corrector() -> VocabularyCorrector:
    return VocabularyCorrector(dict(VOCAB))


@pytest.mark.parametrize(
    "token,expected",
    [
        ("aple", "apple"),
        ("appl", "apple"),
        ("appel", "apple"),
        ("applle", "apple"),
        ("tomoto", "tomato"),
        ("bananna", "banana"),
        ("protien", "protein"),
        ("amull", "amul"),
        ("milkk", "milk"),
        ("yoghrt", "yogurt"),
        ("paneeer", "paneer"),
        ("yougrt", "yogurt"),
        ("tomatoo", "tomato"),
    ],
)
def test_realistic_typos_are_corrected(corrector: VocabularyCorrector, token: str, expected: str) -> None:
    candidate = corrector.correct_token(token)
    assert candidate is not None
    assert candidate.corrected == expected


@pytest.mark.parametrize(
    "token,expected",
    [
        ("mlik", "milk"),
        ("pnaeer", "paneer"),
        ("bnaana", "banana"),
        ("ganola", "granola"),
    ],
)
def test_non_leading_transpositions_and_insertions_are_corrected(
    corrector: VocabularyCorrector, token: str, expected: str
) -> None:
    candidate = corrector.correct_token(token)
    assert candidate is not None
    assert candidate.corrected == expected


@pytest.mark.parametrize(
    "token,expected",
    [
        ("pple", "apple"),
        ("ilk", "milk"),
        ("anana", "banana"),
        ("aneer", "paneer"),
        ("heese", "cheese"),
    ],
)
def test_leading_character_deletion_is_corrected(
    corrector: VocabularyCorrector, token: str, expected: str
) -> None:
    candidate = corrector.correct_token(token)
    assert candidate is not None
    assert candidate.corrected == expected


@pytest.mark.parametrize(
    "token",
    [
        "gain",
        "bulking",
        "weight",
        "protein",
        "apple",
        "milk",
        "grain",
        "fit",
        "fat",
    ],
)
def test_real_words_are_never_corrected_away(corrector: VocabularyCorrector, token: str) -> None:
    assert corrector.correct_token(token) is None


def test_first_position_substitution_collisions_stay_blocked(corrector: VocabularyCorrector) -> None:
    assert corrector.correct_token("gain") is None
    assert corrector.correct_token("bulking") is None


def test_correct_query_fixes_ganola_bar(corrector: VocabularyCorrector) -> None:
    result = corrector.correct_query("ganola bar")
    assert result.corrected_tokens() == ["granola", "bar"]


@pytest.mark.parametrize("query", ["muscle gain", "weight gain", "gain weight", "bulking"])
def test_correct_query_never_touches_gain_or_bulking(corrector: VocabularyCorrector, query: str) -> None:
    result = corrector.correct_query(query)
    assert "jain" not in result.corrected_tokens()
    assert "baking" not in result.corrected_tokens()
