#!/usr/bin/env python3
"""
Run a _search against AWS OpenSearch (managed *.es.amazonaws.com) or
Serverless (*.aoss.amazonaws.com) for a single product by `id`.

SigV4 service is chosen from ES_URL unless overridden by ES_AWS_SERVICE (es|aoss).

Usage (from repo root, venv activated):
  python scripts/opensearch_product_by_id.py 01K1B1BQWSP6S4JK39R5G33YRE

Requires in .env / .env.local:
  ES_URL, ELASTIC_INDEX (default: products_master),
  AWS_REGION or SEARCH_AWS_REGION,
  and for SigV4: AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY (optional AWS_SESSION_TOKEN).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        print("Install python-dotenv: pip install python-dotenv", file=sys.stderr)
        sys.exit(1)
    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT / ".env.local", override=True)


def _sigv4_service_for_url(base_url: str) -> str:
    env = (os.getenv("ES_AWS_SERVICE") or "").strip().lower()
    if env in ("aoss", "es"):
        return env
    b = base_url.lower()
    if "aoss.amazonaws.com" in b:
        return "aoss"
    if ".es.amazonaws.com" in b:
        return "es"
    return "es"


def main() -> None:
    _load_env()

    p = argparse.ArgumentParser(description="OpenSearch _search by product id")
    p.add_argument("product_id", help="Product id (term query on field `id`)")
    p.add_argument("--index", default=os.getenv("ELASTIC_INDEX", "products_master"))
    p.add_argument("--size", type=int, default=1)
    args = p.parse_args()

    base = (os.getenv("ES_URL") or os.getenv("ELASTIC_BASE") or "").strip().rstrip("/")
    if not base:
        print("ES_URL (or ELASTIC_BASE) is not set.", file=sys.stderr)
        sys.exit(1)

    region = (
        os.getenv("SEARCH_AWS_REGION")
        or os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or "ap-south-1"
    )

    ak = os.getenv("AWS_ACCESS_KEY_ID", "").strip()
    sk = os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
    tok = os.getenv("AWS_SESSION_TOKEN", "").strip() or None
    if not ak or not sk:
        print(
            "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be set for SigV4.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        import requests
        from requests_aws4auth import AWS4Auth
    except ImportError:
        print("Install deps: pip install requests requests-aws4auth", file=sys.stderr)
        sys.exit(1)

    service = _sigv4_service_for_url(base)
    auth = AWS4Auth(ak, sk, region, service, session_token=tok)
    url = f"{base}/{args.index}/_search"
    body = {"size": args.size, "query": {"term": {"id": args.product_id}}}

    r = requests.post(url, auth=auth, json=body, headers={"Content-Type": "application/json"}, timeout=60)
    try:
        out = r.json()
    except Exception:
        print(r.text, file=sys.stderr)
        sys.exit(1)

    if r.status_code >= 400:
        print(json.dumps(out, indent=2))
        sys.exit(1)

    print(json.dumps(out, indent=2, ensure_ascii=False))

    hits = (out.get("hits") or {}).get("hits") or []
    if not hits:
        sys.exit(2)


if __name__ == "__main__":
    main()
