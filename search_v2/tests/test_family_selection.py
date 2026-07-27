"""Tests for search_v2/retrieval/family_selection.py"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from search_v2.retrieval.family_selection import (
    family_key,
    family_key_from_card,
    select_one_per_family,
)


def test_family_key_prefers_parent_id():
    assert family_key({"id": "v200", "parent_id": "fam-paneer"}) == "fam-paneer"
    assert family_key({"id": "solo"}) == "solo"


def test_select_one_per_family_paneer_variants():
    docs = [
        {"id": "v200", "parent_id": "fam-paneer", "flean_score": {"adjusted_score": 7.0}, "size": "200 g"},
        {"id": "v400", "parent_id": "fam-paneer", "flean_score": {"adjusted_score": 8.5}, "size": "400 g"},
        {"id": "v1kg", "parent_id": "fam-paneer", "flean_score": {"adjusted_score": 6.0}, "size": "1 kg"},
        {"id": "other", "parent_id": "fam-other", "flean_score": {"adjusted_score": 5.0}},
    ]
    out = select_one_per_family(docs)
    assert len(out) == 2
    assert out[0]["id"] == "v400"
    assert out[1]["id"] == "other"


def test_select_one_per_family_respects_limit():
    docs = [
        {"id": "a", "parent_id": "f1", "flean_score": {"adjusted_score": 9.0}},
        {"id": "b", "parent_id": "f2", "flean_score": {"adjusted_score": 8.0}},
        {"id": "c", "parent_id": "f3", "flean_score": {"adjusted_score": 7.0}},
    ]
    out = select_one_per_family(docs, limit=2)
    assert [d["id"] for d in out] == ["a", "b"]


def test_family_key_from_card():
    assert family_key_from_card({"id": "x", "parent_id": "p"}) == "p"


def _run_all():
    fns = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS: {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL: {fn.__name__}: {exc}")
    print(f"\n{len(fns) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    _run_all()
