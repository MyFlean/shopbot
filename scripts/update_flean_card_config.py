#!/usr/bin/env python3
"""
Maintain highlight_tag and Start Case card values in flean_card_config.json.

Usage:
  python scripts/update_flean_card_config.py
  python scripts/update_flean_card_config.py --path /path/to/flean_card_config.json
  python scripts/update_flean_card_config.py --validate-only
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_CONFIG = Path("/Users/anuj/flean/flean-app-json/flean_card_config.json")

CARD_TO_HIGHLIGHT: dict[str, str] = {
    "Protein": "protein_tags",
    "Fiber": "carbs_fiber_tags",
    "Flean Rank": "",
    "Preservatives": "ingredients_tags",
    "Additives": "ingredients_tags",
    "Sweeteners": "sweetners_sugar_tags",
    "Natural Sugar": "ns_tags",
    "Glycemic Index": "gi_tags",
    "Vitamins & Minerals": "vm_tags",
    "Antioxidants": "antioxidant_tags",
    "Calories": "energy_tags",
    "Fats": "oils_fats_tags",
    "Gut Health": "gh_tags",
    "Hydration": "hydration_tags",
    "Watch Outs": "",
}

ALIAS_TO_START_CASE: dict[str, str] = {
    "protein": "Protein",
    "Protein": "Protein",
    "fiber": "Fiber",
    "Fiber": "Fiber",
    "flean_rank": "Flean Rank",
    "fleanRank": "Flean Rank",
    "FleanRank": "Flean Rank",
    "Flean Rank": "Flean Rank",
    "preservatives": "Preservatives",
    "Preservatives": "Preservatives",
    "additives": "Additives",
    "Additives": "Additives",
    "sweeteners": "Sweeteners",
    "Sweeteners": "Sweeteners",
    "Natural Sugar": "Natural Sugar",
    "naturalSugar": "Natural Sugar",
    "NaturalSugar": "Natural Sugar",
    "Glycemic Index": "Glycemic Index",
    "glycemicIndex": "Glycemic Index",
    "GlycemicIndex": "Glycemic Index",
    "Vitamins & Minerals": "Vitamins & Minerals",
    "vitaminsAndMinerals": "Vitamins & Minerals",
    "VitaminsAndMinerals": "Vitamins & Minerals",
    "Antioxidants": "Antioxidants",
    "antioxidants": "Antioxidants",
    "calories": "Calories",
    "Calories": "Calories",
    "fats": "Fats",
    "Fats": "Fats",
    "Gut Health": "Gut Health",
    "gutHealth": "Gut Health",
    "GutHealth": "Gut Health",
    "Hydration": "Hydration",
    "hydration": "Hydration",
    "watch_outs": "Watch Outs",
    "watchOuts": "Watch Outs",
    "WatchOuts": "Watch Outs",
    "Watch Outs": "Watch Outs",
}

EXPECTED_KEYS = ("card", "highlight_tag", "visible", "optional", "order")
ALLOWED_CARDS = frozenset(CARD_TO_HIGHLIGHT)


def _read_entry_field(entry: dict, *names: str):
    for name in names:
        if name in entry:
            return entry[name]
    return None


def _resolve_start_case_card(entry: dict) -> str:
    raw = _read_entry_field(entry, "card", "Card")
    if raw is None:
        raise KeyError("missing card field")
    if raw not in ALIAS_TO_START_CASE:
        raise KeyError(f"Unknown card type: {raw!r}")
    return ALIAS_TO_START_CASE[raw]


def transform_card(entry: dict) -> dict:
    card = _resolve_start_case_card(entry)
    highlight = _read_entry_field(
        entry, "highlight_tag", "highlightTag", "HighlightTag"
    )
    if highlight is None:
        highlight = CARD_TO_HIGHLIGHT[card]

    return {
        "card": card,
        "highlight_tag": highlight,
        "visible": _read_entry_field(entry, "visible", "Visible"),
        "optional": _read_entry_field(entry, "optional", "Optional"),
        "order": _read_entry_field(entry, "order", "Order"),
    }


def transform_config(data: dict) -> dict:
    return {
        subcategory: [transform_card(entry) for entry in entries]
        for subcategory, entries in data.items()
    }


def validate_config(data: dict) -> list[str]:
    errors: list[str] = []
    for subcategory, entries in data.items():
        for index, entry in enumerate(entries):
            prefix = f"{subcategory}[{index}]"
            keys = tuple(entry.keys())
            if keys != EXPECTED_KEYS:
                errors.append(f"{prefix}: expected keys {EXPECTED_KEYS}, got {keys}")
            card = entry.get("card", "")
            if card not in ALLOWED_CARDS:
                errors.append(f"{prefix}: unknown card {card!r}")
            if "highlight_tag" not in entry:
                errors.append(f"{prefix}: missing highlight_tag")
            elif entry["highlight_tag"] != CARD_TO_HIGHLIGHT[card]:
                errors.append(
                    f"{prefix}: highlight_tag {entry['highlight_tag']!r} "
                    f"!= expected {CARD_TO_HIGHLIGHT[card]!r}"
                )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--path",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"Path to flean_card_config.json (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the file without writing changes",
    )
    args = parser.parse_args()

    if not args.path.exists():
        print(f"error: file not found: {args.path}", file=sys.stderr)
        return 1

    with args.path.open(encoding="utf-8") as f:
        original = json.load(f)

    subcategory_count = len(original)
    card_count = sum(len(entries) for entries in original.values())

    if args.validate_only:
        errors = validate_config(original)
        if errors:
            for err in errors:
                print(err, file=sys.stderr)
            return 1
        print(
            f"OK: {subcategory_count} subcategories, {card_count} card entries validated"
        )
        return 0

    updated = transform_config(original)
    errors = validate_config(updated)
    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        return 1

    with args.path.open("w", encoding="utf-8") as f:
        json.dump(updated, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(
        f"Updated {args.path}: {subcategory_count} subcategories, "
        f"{card_count} card entries"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
