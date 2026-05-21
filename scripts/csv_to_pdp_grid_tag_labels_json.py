#!/usr/bin/env python3
"""
Generate pdp_grid_tag_labels.json from docs/Flean_PDP_Grid_Tags_Final.csv.

After editing the CSV:
  python scripts/csv_to_pdp_grid_tag_labels_json.py
  aws s3 cp shopping_bot/data/config/pdp_grid_tag_labels.json \\
    s3://flean-app-json/pdp_grid_tag_labels.json

No Lambda deploy required for label updates (shopbot loads from S3).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = REPO_ROOT / "docs" / "Flean_PDP_Grid_Tags_Final.csv"
DEFAULT_OUT = REPO_ROOT / "shopping_bot" / "data" / "config" / "pdp_grid_tag_labels.json"


def csv_to_labels(csv_path: Path) -> dict[str, str]:
    labels: dict[str, str] = {}
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tag_id = (row.get("tag_id") or "").strip()
            tag_name = (row.get("tag_name") or "").strip()
            if not tag_id or not tag_name:
                continue
            if tag_id in labels and labels[tag_id] != tag_name:
                print(
                    f"warning: duplicate tag_id {tag_id!r} "
                    f"({labels[tag_id]!r} vs {tag_name!r})",
                    file=sys.stderr,
                )
            labels[tag_id] = tag_name
    return labels


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    if not args.csv.is_file():
        print(f"error: CSV not found: {args.csv}", file=sys.stderr)
        return 1

    labels = csv_to_labels(args.csv)
    if not labels:
        print("error: no tag_id/tag_name rows parsed", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        json.dump(labels, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"wrote {len(labels)} labels to {args.out}")
    print(
        "upload: aws s3 cp "
        f"{args.out.relative_to(REPO_ROOT)} "
        "s3://flean-app-json/pdp_grid_tag_labels.json"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
