"""Load and read PDP score-card grid config from Redis (scorecard/{subcategory_path})."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import boto3

log = logging.getLogger(__name__)

DEFAULT_SUBCATEGORY = "Default"
DEFAULT_BUCKET = "flean-app-json"
DEFAULT_S3_KEY = "flean_card_config.json"
REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_CONFIG_PATHS = (
    REPO_ROOT / "flean_card_config.json",
    Path(__file__).resolve().parent.parent / "data" / "config" / "flean_card_config.json",
)

_config_cache: Optional[Tuple[float, Dict[str, List[Dict[str, Any]]]]] = None
_seeded = False


def scorecard_redis_prefix() -> str:
    return os.getenv("SCORECARD_REDIS_PREFIX", "scorecard/").strip() or "scorecard/"


def scorecard_redis_key(subcategory_path: str) -> str:
    path = str(subcategory_path or "").strip() or DEFAULT_SUBCATEGORY
    return f"{scorecard_redis_prefix()}{path}"


def _cache_ttl_seconds() -> int:
    raw = os.getenv("CARDS_CONFIG_CACHE_TTL_SECONDS", "300")
    try:
        return max(0, int(raw))
    except ValueError:
        return 300


def _use_local_only() -> bool:
    env = (os.getenv("APP_ENV") or os.getenv("FLASK_ENV") or "").strip().lower()
    if env in ("development", "dev", "local"):
        return os.getenv("CARDS_CONFIG_USE_S3", "").strip().lower() not in (
            "1",
            "true",
            "yes",
        )
    return False


def _resolve_local_path() -> Optional[Path]:
    override = os.getenv("CARDS_CONFIG_LOCAL_PATH", "").strip()
    if override:
        path = Path(override)
        if path.is_file():
            return path
    for candidate in LOCAL_CONFIG_PATHS:
        if candidate.is_file():
            return candidate
    return None


def load_cards_config_source(*, force_refresh: bool = False) -> Dict[str, List[Dict[str, Any]]]:
    """Load full subcategory -> card entries map from local file or S3."""
    global _config_cache

    ttl = _cache_ttl_seconds()
    now = time.time()
    if not force_refresh and _config_cache is not None:
        cached_at, cached = _config_cache
        if ttl == 0 or (now - cached_at) < ttl:
            return cached

    if _use_local_only():
        path = _resolve_local_path()
        if path is None:
            log.warning("CARDS_CONFIG_LOCAL_MISSING")
            data: Dict[str, List[Dict[str, Any]]] = {}
        else:
            with path.open(encoding="utf-8") as f:
                raw = json.load(f)
            data = _normalize_source(raw)
            log.info("CARDS_CONFIG_LOADED | source=local | path=%s", path)
        _config_cache = (now, data)
        return data

    try:
        bucket = os.getenv("CARDS_CONFIG_BUCKET", DEFAULT_BUCKET).strip() or DEFAULT_BUCKET
        key = os.getenv("CARDS_CONFIG_KEY", DEFAULT_S3_KEY).strip() or DEFAULT_S3_KEY
        region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "ap-south-1"
        client = boto3.client("s3", region_name=region)
        response = client.get_object(Bucket=bucket, Key=key)
        raw = json.loads(response["Body"].read().decode("utf-8"))
        data = _normalize_source(raw)
        log.info("CARDS_CONFIG_LOADED | source=s3 | bucket=%s | key=%s", bucket, key)
    except Exception as exc:
        log.warning("CARDS_CONFIG_S3_FALLBACK | error=%s", exc)
        path = _resolve_local_path()
        if path is None:
            data = {}
        else:
            with path.open(encoding="utf-8") as f:
                raw = json.load(f)
            data = _normalize_source(raw)

    _config_cache = (now, data)
    return data


def _normalize_source(raw: Any) -> Dict[str, List[Dict[str, Any]]]:
    if not isinstance(raw, dict):
        raise ValueError("flean_card_config.json must be a JSON object")
    out: Dict[str, List[Dict[str, Any]]] = {}
    for subcategory, entries in raw.items():
        key = str(subcategory or "").strip()
        if not key or not isinstance(entries, list):
            continue
        out[key] = [entry for entry in entries if isinstance(entry, dict)]
    return out


def ensure_cards_config_in_redis(redis_client, *, force: bool = False) -> int:
    """Seed scorecard/* Redis keys from JSON source. Returns number of keys written."""
    global _seeded

    probe_key = scorecard_redis_key(DEFAULT_SUBCATEGORY)
    if not force and _seeded:
        return 0
    if not force and redis_client.exists(probe_key):
        _seeded = True
        return 0

    source = load_cards_config_source(force_refresh=force)
    if not source:
        log.warning("CARDS_CONFIG_SEED_SKIPPED | empty source")
        return 0

    written = 0
    for subcategory_path, entries in source.items():
        redis_key = scorecard_redis_key(subcategory_path)
        if not force and redis_client.exists(redis_key):
            continue
        redis_client.set(redis_key, json.dumps(entries, ensure_ascii=False))
        written += 1

    _seeded = True
    log.info("CARDS_CONFIG_SEEDED | keys_written=%s", written)
    return written


def _parse_config_payload(raw: Optional[str]) -> List[Dict[str, Any]]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("CARDS_CONFIG_PARSE_ERROR")
        return []
    if not isinstance(parsed, list):
        return []
    return [entry for entry in parsed if isinstance(entry, dict)]


def get_subcategory_cards_config(
    redis_client,
    subcategory_path: str,
    *,
    force_refresh: bool = False,
) -> List[Dict[str, Any]]:
    """Read card config for a subcategory; fallback to Default."""
    path = str(subcategory_path or "").strip()

    ensure_cards_config_in_redis(redis_client)

    raw = redis_client.get(scorecard_redis_key(path)) if path else None
    entries = _parse_config_payload(raw)
    if not entries:
        raw = redis_client.get(scorecard_redis_key(DEFAULT_SUBCATEGORY))
        entries = _parse_config_payload(raw)

    return entries


def get_subcategory_cards_config_for_path(subcategory_path: str) -> List[Dict[str, Any]]:
    """Resolve Redis client from Flask app and fetch config (empty list if unavailable)."""
    redis_client = _get_redis_client()
    if redis_client is None:
        return []
    try:
        return get_subcategory_cards_config(redis_client, subcategory_path)
    except Exception as exc:
        log.warning("CARDS_CONFIG_READ_ERROR | path=%s | error=%s", subcategory_path, exc)
        return []


def _get_redis_client():
    try:
        from flask import has_app_context, current_app

        if not has_app_context():
            return None
        ctx_mgr = current_app.extensions.get("ctx_mgr")
        if ctx_mgr is None:
            return None
        return ctx_mgr.redis
    except Exception:
        return None


def clear_cards_config_cache() -> None:
    """Invalidate in-process caches (tests)."""
    global _config_cache, _seeded
    _config_cache = None
    _seeded = False


CARD_DISPLAY_NAME_TO_SCORE_KEY: Dict[str, str] = {
    "Protein": "protein",
    "Fiber": "fiber",
    "Sweeteners": "sweeteners",
    "Fats": "oils",
    "Additives": "additives",
    "Preservatives": "preservatives",
    "Calories": "calories",
    "Flean Rank": "flean_rank",
    "Watch Outs": "watch_outs",
    "Natural Sugar": "natural_sugar",
    "Glycemic Index": "glycemic_index",
    "Vitamins & Minerals": "vitamins_minerals",
    "Antioxidants": "antioxidants",
    "Gut Health": "gut_health",
}

# Unified score-card build registry.
# build_type: percentile | highlight_only | additives | preservatives | watch_outs | flean_rank | glycemic_index | sentiment_highlight
# tier_mode (percentile): bonus → High/Good/Average/Poor/Sub-Par | penalty → Very Low/Low/Present/High/Very High
# ES highlight tag groups come from Redis config highlight_tag only (not registry).
CARD_STATS_REGISTRY: Dict[str, Dict[str, Any]] = {
    "watch_outs": {"build_type": "watch_outs", "default_title": "Watch-outs"},
    "flean_rank": {"build_type": "flean_rank", "default_title": "Flean Rank"},
    "protein": {
        "build_type": "percentile",
        "default_title": "Protein",
        "stats_fields": ("protein_percentiles",),
        "tier_mode": "bonus",
        "subtitle": "Efficiency",
    },
    "fiber": {
        "build_type": "percentile",
        "default_title": "Fiber",
        "stats_fields": ("fiber_percentiles",),
        "tier_mode": "bonus",
        "subtitle": "Efficiency",
    },
    "natural_sugar": {
        "build_type": "percentile",
        "default_title": "Natural Sugar",
        "stats_fields": ("total_sugar_percentiles",),
        "tier_mode": "penalty",
        "subtitle": "Percentile",
    },
    "glycemic_index": {
        "build_type": "glycemic_index",
        "default_title": "Glycemic Index",
    },
    "vitamins_minerals": {
        "build_type": "percentile",
        "default_title": "Vitamins & Minerals",
        "stats_fields": ("total_vitamin_mineral_percentiles",),
        "tier_mode": "bonus",
        "subtitle": "Efficiency",
    },
    "sweeteners": {
        "build_type": "percentile",
        "default_title": "Sweeteners",
        "stats_fields": ("sweetener_penalty_percentiles",),
        "tier_mode": "penalty",
        "subtitle": "Percentile",
    },
    "oils": {
        "build_type": "percentile",
        "default_title": "Fats",
        "stats_fields": ("total_fat_penalty_percentiles",),
        "tier_mode": "penalty",
        "subtitle": "Percentile",
    },
    "additives": {
        "build_type": "additives",
        "default_title": "Additives",
        "stats_fields": ("additives_penalty_percentiles",),
        "tier_mode": "penalty",
    },
    "preservatives": {
        "build_type": "preservatives", 
        "default_title": "Preservatives"
    },
    "antioxidants": {
        "build_type": "sentiment_highlight",
        "default_title": "Antioxidants",
    },
    "calories": {
        "build_type": "percentile",
        "default_title": "Calories",
        "stats_fields": ("calories_penalty_percentiles",),
        "tier_mode": "penalty",
        "subtitle": "Percentile",
    },
    "gut_health": {
        "build_type": "sentiment_highlight",
        "default_title": "Gut Health",
    },
}

SCORE_CARD_BUILD_ORDER: Tuple[str, ...] = (
    "watch_outs",
    "flean_rank",
    "protein",
    "fiber",
    "natural_sugar",
    "glycemic_index",
    "vitamins_minerals",
    "sweeteners",
    "oils",
    "additives",
    "preservatives",
    "antioxidants",
    "calories",
    "gut_health",
)


def score_key_meta_from_config(
    config_entries: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Map score_cards builder keys to Redis config metadata."""
    meta: Dict[str, Dict[str, Any]] = {}
    for entry in config_entries:
        display_name = str(entry.get("card") or "").strip()
        score_key = CARD_DISPLAY_NAME_TO_SCORE_KEY.get(display_name)
        if not score_key:
            continue
        meta[score_key] = {
            "title": display_name,
            "highlight_tag": str(entry.get("highlight_tag") or "").strip(),
            "optional": bool(entry.get("optional", True)),
        }
    return meta


def allowed_score_keys_from_config(
    config_entries: List[Dict[str, Any]],
) -> frozenset[str]:
    """Map visible config entries to score_cards builder keys."""
    keys: set[str] = set()
    for entry in config_entries:
        if not entry.get("visible", True):
            continue
        display_name = str(entry.get("card") or "").strip()
        score_key = CARD_DISPLAY_NAME_TO_SCORE_KEY.get(display_name)
        if score_key:
            keys.add(score_key)
    return frozenset(keys)


def apply_order_from_config(
    score_cards: Dict[str, Any],
    config_entries: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Attach config order and visible flag to built score cards."""
    order_by_key: Dict[str, Any] = {}
    for entry in config_entries:
        if not entry.get("visible", True):
            continue
        display_name = str(entry.get("card") or "").strip()
        score_key = CARD_DISPLAY_NAME_TO_SCORE_KEY.get(display_name)
        if not score_key or score_key not in score_cards:
            continue
        order = entry.get("order")
        if order is not None:
            order_by_key[score_key] = order

    updated: Dict[str, Any] = {}
    for key, card in score_cards.items():
        merged = dict(card)
        merged["visible"] = True
        if key in order_by_key:
            merged["order"] = order_by_key[key]
        updated[key] = merged
    return updated
