#!/usr/bin/env python3
"""
Load flean_card_config.json into Redis scorecard/* keys.

Usage:
  python scripts/load_cards_config_to_redis.py
  python scripts/load_cards_config_to_redis.py --force
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from shopping_bot.redis_manager import RedisContextManager
from shopping_bot.utils.cards_config import ensure_cards_config_in_redis, load_cards_config_source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing scorecard/* keys",
    )
    args = parser.parse_args()

    source = load_cards_config_source(force_refresh=True)
    if not source:
        print("error: no card config source found", file=sys.stderr)
        return 1

    ctx_mgr = RedisContextManager()
    written = ensure_cards_config_in_redis(ctx_mgr.redis, force=args.force)
    host = os.getenv("REDIS_HOST", "localhost")
    print(f"Seeded {written} scorecard/* keys to Redis at {host} ({len(source)} subcategories in source)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
