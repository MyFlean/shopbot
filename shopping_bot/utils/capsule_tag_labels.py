from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import boto3

log = logging.getLogger(__name__)

DEFAULT_BUCKET = "flean-app-json"
DEFAULT_KEY = "nutra_capsule_tag_labels.json"
LOCAL_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "config"
    / "nutra_capsule_tag_labels.json"
)

_labels_cache: Optional[Tuple[float, Dict[str, str]]] = None


def _normalize_label_map(data: Any, source: str) -> Dict[str, str]:
    if not isinstance(data, dict):
        raise ValueError(f"{source} must be a JSON object")

    labels: Dict[str, str] = {}
    for tag_id, label in data.items():
        if not isinstance(tag_id, str) or not isinstance(label, str):
            continue
        normalized_id = tag_id.strip()
        normalized_label = label.strip()
        if normalized_id and normalized_label:
            labels[normalized_id] = normalized_label
    return labels


def _cache_ttl_seconds() -> int:
    raw = os.getenv("CAPSULE_TAG_LABELS_CACHE_TTL_SECONDS", "300")
    try:
        return max(0, int(raw))
    except ValueError:
        return 300


def _use_local_only() -> bool:
    env = (os.getenv("APP_ENV") or os.getenv("FLASK_ENV") or "").strip().lower()
    if env in {"development", "dev", "local"}:
        return os.getenv("CAPSULE_TAG_LABELS_USE_S3", "").strip().lower() not in {
            "1",
            "true",
            "yes",
        }
    return False


def _load_local_labels() -> Dict[str, str]:
    if not LOCAL_CONFIG_PATH.is_file():
        log.warning("CAPSULE_TAG_LABELS_LOCAL_MISSING | path=%s", LOCAL_CONFIG_PATH)
        return {}
    with LOCAL_CONFIG_PATH.open(encoding="utf-8") as file:
        data = json.load(file)
    return _normalize_label_map(data, LOCAL_CONFIG_PATH.name)


def _load_labels_from_s3() -> Dict[str, str]:
    bucket = (
        os.getenv("CAPSULE_TAG_LABELS_BUCKET", DEFAULT_BUCKET).strip()
        or DEFAULT_BUCKET
    )
    key = os.getenv("CAPSULE_TAG_LABELS_KEY", DEFAULT_KEY).strip() or DEFAULT_KEY
    region = (
        os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or "ap-south-1"
    )
    client = boto3.client("s3", region_name=region)
    response = client.get_object(Bucket=bucket, Key=key)
    data = json.loads(response["Body"].read().decode("utf-8"))
    labels = _normalize_label_map(data, key)
    log.info(
        "CAPSULE_TAG_LABELS_LOADED | source=s3 | bucket=%s | key=%s",
        bucket,
        key,
    )
    return labels


def get_capsule_tag_label_map(
    *, force_refresh: bool = False
) -> Dict[str, str]:
    """Return the cached capsule tag ID-to-label mapping."""
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
            log.error("CAPSULE_TAG_LABELS_LOCAL_ERROR | error=%s", exc)
    else:
        try:
            labels = _load_labels_from_s3()
        except Exception as exc:
            # Production fails closed: never expose raw or generated labels.
            log.warning("CAPSULE_TAG_LABELS_S3_ERROR | error=%s", exc)

    _labels_cache = (now, labels)
    return labels


def resolve_capsule_tags(source: Any) -> List[str]:
    """Resolve category_data.tags.raw_tags IDs, dropping unmapped values."""
    if not isinstance(source, dict):
        return []

    category_data = source.get("category_data")
    if not isinstance(category_data, dict):
        return []
    tags = category_data.get("tags")
    if not isinstance(tags, dict):
        return []
    raw_tags = tags.get("raw_tags")
    if not isinstance(raw_tags, list):
        return []

    labels = get_capsule_tag_label_map()
    resolved: List[str] = []
    seen = set()
    for tag_id in raw_tags:
        if not isinstance(tag_id, str):
            continue
        label = labels.get(tag_id.strip())
        if label and label not in seen:
            resolved.append(label)
            seen.add(label)
    return resolved


def clear_capsule_tag_label_cache() -> None:
    """Clear the in-process mapping cache."""
    global _labels_cache
    _labels_cache = None
