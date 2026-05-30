from __future__ import annotations

import json
import os
import logging
from typing import Dict, List, Optional

import boto3

SERVICEABLE_PINCODES_BUCKET = "flean-app-json"
SERVICEABLE_PINCODES_KEY = "serviceable_pincodes.json"
log = logging.getLogger(__name__)


class PincodeMappingError(RuntimeError):
    """Raised when request pincode cannot be canonicalized."""


PLACEHOLDER_PINCODES = frozenset({"000000"})


def is_placeholder_pincode(pincode: Optional[str]) -> bool:
    """True when pincode is missing or a client placeholder (no serviceability lookup)."""
    text = str(pincode or "").strip()
    return not text or text in PLACEHOLDER_PINCODES


def _load_serviceable_mapping() -> Dict[str, Dict[str, List[str]]]:
    try:
        region = (
            os.getenv("AWS_REGION")
            or os.getenv("AWS_DEFAULT_REGION")
            or "ap-south-1"
        )
        s3_client = boto3.client("s3", region_name=region)
        response = s3_client.get_object(
            Bucket=SERVICEABLE_PINCODES_BUCKET,
            Key=SERVICEABLE_PINCODES_KEY,
        )
        payload = json.loads(response["Body"].read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise PincodeMappingError("serviceable_pincodes.json must be a JSON object")
        return payload
    except PincodeMappingError:
        raise
    except Exception as exc:
        raise PincodeMappingError(
            f"Failed to fetch {SERVICEABLE_PINCODES_KEY} from {SERVICEABLE_PINCODES_BUCKET}: {exc}"
        ) from exc


def resolve_canonical_pincode(request_pincode: str) -> str:
    raw = str(request_pincode or "").strip()
    if not raw:
        raise PincodeMappingError("Request pincode is required")

    mapping = _load_serviceable_mapping()
    matches: List[str] = []
    for canonical_key, config in mapping.items():
        canonical = str(canonical_key or "").strip()
        if not canonical:
            continue
        if not isinstance(config, dict):
            continue
        serviceable = config.get("serviceable_pincodes")
        if not isinstance(serviceable, list):
            continue
        normalized_serviceable = {
            str(pin).strip() for pin in serviceable if str(pin).strip()
        }
        if raw in normalized_serviceable:
            matches.append(canonical)

    if matches:
        if len(matches) > 1:
            log.warning(
                "PINCODE_CANONICAL_DUPLICATE_MATCH | request_pincode=%s | matches=%s | selected=%s",
                raw,
                matches,
                matches[0],
            )
        return matches[0]

    raise PincodeMappingError(
        f"Pincode {raw} is not mapped in {SERVICEABLE_PINCODES_KEY}"
    )
