"""
search_v2/embedding/bedrock_embedding_service.py
──────────────────────────────────────────────────
Runtime QUERY embedding generation via Amazon Bedrock Titan Text Embeddings V2.

This module belongs entirely to shopbot-main's runtime-search responsibility.
It is NOT shared with, imported from, or kept in sync with the Search repo's
own (independent) indexing-time embedding implementation — the two repos
each own their half of the embedding pipeline and only agree on the model
ID, dimension, and normalization convention, so that a query vector produced
here and a passage vector produced by the Search repo land in the same
vector space. See settings.py's BEDROCK_* fields for the shared contract.

Auth: bearer-token (the SAME AWS_BEARER_TOKEN_BEDROCK already used by
shopping_bot/bedrock_client.py for Claude) over plain HTTPS — no boto3, no
IAM role, no new secret. This reuses the existing auth *mechanism* already
proven in production for Bedrock calls from this repo; it is a new,
independent implementation of that same pattern for a different model, not
a shared client class (bedrock_client.py's Converse-API-specific classes
don't fit an embedding InvokeModel call's shape, and duplicating the ~15
lines of bearer-token HTTP plumbing here is cheaper than forcing an
unrelated abstraction over both).

Failure philosophy: this is the runtime query path. On any failure (timeout,
throttle, bad response), embed_query() raises BedrockEmbeddingError rather
than returning None, so callers surface the error instead of silently
degrading to lexical-only retrieval.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import List, Optional

import requests

logger = logging.getLogger("search_v2.bedrock_embedding_service")

_WS_RE = re.compile(r"\s+")
MAX_QUERY_CHARS = 500

# Short timeout/retry budget, deliberately — this sits in the live request
# Bedrock call timeout (seconds). Failures raise BedrockEmbeddingError.
# needs to avoid needlessly inflating request latency before falling back.
_MAX_ATTEMPTS = 2
_RETRY_DELAY_SEC = 0.5
_REQUEST_TIMEOUT_SEC = 8


def _clean(text: Optional[str], max_chars: int) -> str:
    if not text:
        return ""
    return _WS_RE.sub(" ", str(text)).strip()[:max_chars]


class BedrockEmbeddingError(RuntimeError):
    """Raised on any Titan query-embedding failure."""


class BedrockTitanEmbeddingService:
    """Same public shape as EmbeddingService (dim, is_available, embed_query)
    so get_embedding_service()'s factory can return either one
    interchangeably — semantic_query_builder.py and hybrid_query_builder.py
    never need to know which backend they got.

    embed_passages() is deliberately NOT implemented here — bulk passage
    embedding is the Search repo's responsibility (indexing), not
    shopbot-main's (runtime search). Calling it here is a sign something is
    routing indexing work through the runtime service by mistake.
    """

    def __init__(self, bearer_token: str, region: str, model_id: str, dim: int):
        if not bearer_token:
            raise RuntimeError(
                "Missing AWS_BEARER_TOKEN_BEDROCK. Set it in the environment — "
                "semantic search cannot generate query embeddings without it."
            )
        self.bearer_token = bearer_token
        self.region = region
        self.model_id = model_id
        self._dim = dim
        self.endpoint = f"https://bedrock-runtime.{region}.amazonaws.com/model/{model_id}/invoke"
        self._session = requests.Session()
        logger.info(
            "BEDROCK_QUERY_EMBEDDING_INIT | region=%s | model=%s | dim=%d", region, model_id, dim,
        )

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def model_label(self) -> str:
        return self.model_id

    def is_available(self) -> bool:
        try:
            self._invoke("availability check")
            return True
        except Exception:
            logger.exception("Bedrock Titan query-embedding endpoint unavailable")
            return False

    def preload(self) -> None:
        """No-op: there are no local model weights to warm for a remote
        Bedrock call. Exists only for interface parity with EmbeddingService
        so callers (dev_search_cli.py, shopping_bot/__init__.py's ECS/non-Lambda
        init path) can call .preload() on whatever get_embedding_service()
        returns without needing to know which backend it is."""
        return None

    def _invoke(self, text: str) -> List[float]:
        body = json.dumps({"inputText": text, "dimensions": self._dim, "normalize": True})
        headers = {
            "Authorization": f"Bearer {self.bearer_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        last_exc: Optional[Exception] = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                resp = self._session.post(
                    self.endpoint, data=body, headers=headers, timeout=_REQUEST_TIMEOUT_SEC,
                )
            except requests.RequestException as exc:
                last_exc = exc
                logger.warning(
                    "BEDROCK_QUERY_EMBEDDING_NETWORK_ERROR | attempt=%d/%d | error=%s",
                    attempt, _MAX_ATTEMPTS, exc,
                )
                if attempt < _MAX_ATTEMPTS:
                    time.sleep(_RETRY_DELAY_SEC)
                continue

            if resp.status_code == 200:
                payload = resp.json()
                embedding = payload.get("embedding")
                if not isinstance(embedding, list) or not embedding:
                    raise BedrockEmbeddingError(
                        f"Bedrock returned 200 but no usable 'embedding' field: {resp.text[:300]}"
                    )
                return embedding

            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                logger.warning(
                    "BEDROCK_QUERY_EMBEDDING_RETRYABLE_ERROR | attempt=%d/%d | status=%d",
                    attempt, _MAX_ATTEMPTS, resp.status_code,
                )
                if attempt < _MAX_ATTEMPTS:
                    time.sleep(_RETRY_DELAY_SEC)
                continue

            # Non-retryable (400/401/403/404/...) — fail immediately, don't
            # burn the retry budget on a request that will never succeed.
            raise BedrockEmbeddingError(
                f"Bedrock query embedding request failed: HTTP {resp.status_code}: {resp.text[:500]}"
            )

        raise BedrockEmbeddingError(
            f"Bedrock query embedding failed after {_MAX_ATTEMPTS} attempts: {last_exc}"
        )

    def embed_query(self, text: str) -> Optional[List[float]]:
        """Raises BedrockEmbeddingError on failure — does not return None."""
        cleaned = _clean(text, MAX_QUERY_CHARS)
        if not cleaned:
            return None
        return self._invoke(cleaned)

    def embed_passages(self, texts: List[str], batch_size: int = 64) -> List[List[float]]:
        raise NotImplementedError(
            "Bulk passage embedding is the Search repo's responsibility (indexing), "
            "not shopbot-main's (runtime search) — see module docstring."
        )
