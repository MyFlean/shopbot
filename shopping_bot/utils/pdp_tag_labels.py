from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

import boto3

log = logging.getLogger(__name__)

DEFAULT_BUCKET = "flean-app-json"
DEFAULT_KEY = "pdp_grid_tag_labels.json"
LOCAL_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "config" / "pdp_grid_tag_labels.json"
)

_labels_cache: Optional[Tuple[float, Dict[str, str]]] = None


def _humanize_tag_id(tag_id: str) -> str:
    s = str(tag_id or "").strip().replace("_", " ")
    if not s:
        return ""
    return s.title()


def _cache_ttl_seconds() -> int:
    raw = os.getenv("PDP_TAG_LABELS_CACHE_TTL_SECONDS", "300")
    try:
        return max(0, int(raw))
    except ValueError:
        return 300


def _use_local_only() -> bool:
    env = (os.getenv("APP_ENV") or os.getenv("FLASK_ENV") or "").strip().lower()
    if env in ("development", "dev", "local"):
        return os.getenv("PDP_TAG_LABELS_USE_S3", "").strip().lower() not in (
            "1",
            "true",
            "yes",
        )
    return False


def _load_local_labels() -> Dict[str, str]:
    if not LOCAL_CONFIG_PATH.is_file():
        log.warning("PDP_TAG_LABELS_LOCAL_MISSING | path=%s", LOCAL_CONFIG_PATH)
        return {}
    with LOCAL_CONFIG_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("pdp_grid_tag_labels.json must be a JSON object")
    return {str(k).strip(): str(v).strip() for k, v in data.items() if str(k).strip() and str(v).strip()}


def _load_labels_from_s3() -> Dict[str, str]:
    bucket = os.getenv("PDP_TAG_LABELS_BUCKET", DEFAULT_BUCKET).strip() or DEFAULT_BUCKET
    key = os.getenv("PDP_TAG_LABELS_KEY", DEFAULT_KEY).strip() or DEFAULT_KEY
    region = (
        os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or "ap-south-1"
    )
    client = boto3.client("s3", region_name=region)
    response = client.get_object(Bucket=bucket, Key=key)
    data = json.loads(response["Body"].read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{key} must be a JSON object")
    log.info("PDP_TAG_LABELS_LOADED | source=s3 | bucket=%s | key=%s", bucket, key)
    return {str(k).strip(): str(v).strip() for k, v in data.items() if str(k).strip() and str(v).strip()}


def get_tag_label_map(*, force_refresh: bool = False) -> Dict[str, str]:
    """Return tag_id -> display label map (S3 with local fallback, TTL-cached)."""
    global _labels_cache

    ttl = _cache_ttl_seconds()
    now = time.time()
    if not force_refresh and _labels_cache is not None:
        cached_at, cached_map = _labels_cache
        if ttl == 0 or (now - cached_at) < ttl:
            return cached_map

    labels: Dict[str, str] = {}
    if _use_local_only():
        try:
            labels = _load_local_labels()
        except Exception as exc:
            log.error("PDP_TAG_LABELS_LOCAL_ERROR | error=%s", exc)
        _labels_cache = (now, labels)
        return labels

    try:
        labels = _load_labels_from_s3()
    except Exception as exc:
        log.warning("PDP_TAG_LABELS_S3_FALLBACK | error=%s", exc)
        try:
            labels = _load_local_labels()
        except Exception as local_exc:
            log.error("PDP_TAG_LABELS_FALLBACK_FAILED | error=%s", local_exc)
            labels = {}

    _labels_cache = (now, labels)
    return labels


def label_for_tag_id(tag_id: str) -> str:
    """Map ES tag_id to PDP subtitle label; humanize if unmapped."""
    key = str(tag_id or "").strip()
    if not key:
        return ""
    mapped = get_tag_label_map().get(key)
    if mapped:
        return mapped
    return _humanize_tag_id(key)


def clear_tag_label_cache() -> None:
    """Invalidate cached labels (e.g. after S3 upload in tests)."""
    global _labels_cache
    _labels_cache = None
