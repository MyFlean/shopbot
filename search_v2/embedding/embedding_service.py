"""
search_v2/embedding/embedding_service.py
────────────────────────────────────────────
Generic embedding wrapper for Search V2. Unlike V1's embedding_model.py (which
hardcoded e5-small-v2), this loads whichever model search_v2.config.SETTINGS
points at, via the registry in model_registry.py — so the playground and the
benchmarking suite can swap models at runtime without touching code.

One process can only sensibly hold one loaded model at a time for the live
query path (memory), but the benchmarking suite needs to compare several —
see benchmarking/benchmark_embedding_models.py, which loads/unloads models
one at a time rather than holding all of them in memory simultaneously.
"""
from __future__ import annotations

import logging
import re
import threading
from typing import Dict, List, Optional

from search_v2.embedding.model_registry import EmbeddingModelSpec, get_model_spec

logger = logging.getLogger("search_v2.embedding_service")

_WS_RE = re.compile(r"\s+")
MAX_PASSAGE_CHARS = 2000
MAX_QUERY_CHARS = 500


def _clean(text: Optional[str], max_chars: int) -> str:
    if not text:
        return ""
    return _WS_RE.sub(" ", str(text)).strip()[:max_chars]


class EmbeddingService:
    """One instance = one loaded model. Thread-safe lazy load."""

    def __init__(self, model_key: str):
        self.spec: EmbeddingModelSpec = get_model_spec(model_key)
        self._model = None
        self._lock = threading.Lock()

    @property
    def dim(self) -> int:
        return self.spec.dim

    @property
    def model_label(self) -> str:
        return self.spec.key

    def _get_model(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                from sentence_transformers import SentenceTransformer  # deferred import

                logger.info("Loading embedding model %s (%s) ...", self.spec.key, self.spec.hf_name)
                self._model = SentenceTransformer(self.spec.hf_name)
                logger.info("Loaded %s (dim=%d).", self.spec.key, self.spec.dim)
        return self._model

    def preload(self) -> None:
        try:
            self._get_model()
        except Exception:
            logger.exception("Failed to preload embedding model %s", self.spec.key)

    def is_available(self) -> bool:
        try:
            self._get_model()
            return True
        except Exception:
            return False

    def embed_passages(self, texts: List[str], batch_size: int = 64) -> List[List[float]]:
        model = self._get_model()
        prefixed = [f"{self.spec.passage_prefix}{_clean(t, MAX_PASSAGE_CHARS)}" for t in texts]
        vectors = model.encode(prefixed, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False)
        return [v.tolist() for v in vectors]

    def embed_query(self, text: str) -> Optional[List[float]]:
        cleaned = _clean(text, MAX_QUERY_CHARS)
        if not cleaned:
            return None
        try:
            model = self._get_model()
        except Exception:
            logger.exception("Embedding model unavailable; query will fall back to lexical-only.")
            return None
        vec = model.encode([f"{self.spec.query_prefix}{cleaned}"], normalize_embeddings=True, show_progress_bar=False)[0]
        return vec.tolist()


# ── Process-wide cache of loaded services, keyed by model_key ──────────────
_service_cache: Dict[str, object] = {}
_cache_lock = threading.Lock()


def get_embedding_service(model_key: Optional[str] = None):
    """Production runtime default is SETTINGS.EMBEDDING_BACKEND == "bedrock"
    (Amazon Titan Text Embeddings V2, via bedrock_embedding_service.py) —
    returns a BedrockTitanEmbeddingService.

    Passing an explicit model_key always uses the LOCAL sentence-transformers
    path regardless of EMBEDDING_BACKEND — the only caller that does this is
    shopping_bot/__init__.py's gunicorn-preload step, itself gated behind
    EMBEDDING_BACKEND == "local". Every other caller (search_v2/extension/search/core.py,
    dev_search_cli.py) must call this with NO argument to get the configured
    backend — passing SETTINGS.EMBEDDING_MODEL_KEY explicitly would silently
    bypass Bedrock entirely.
    """
    from search_v2.config.settings import SETTINGS

    if model_key is None and SETTINGS.EMBEDDING_BACKEND == "bedrock":
        cache_key = f"bedrock:{SETTINGS.BEDROCK_EMBEDDING_MODEL_ID}:{SETTINGS.EMBEDDING_DIM}"
        if cache_key not in _service_cache:
            with _cache_lock:
                if cache_key not in _service_cache:
                    from search_v2.embedding.bedrock_embedding_service import BedrockTitanEmbeddingService

                    _service_cache[cache_key] = BedrockTitanEmbeddingService(
                        bearer_token=SETTINGS.AWS_BEARER_TOKEN_BEDROCK,
                        region=SETTINGS.BEDROCK_REGION,
                        model_id=SETTINGS.BEDROCK_EMBEDDING_MODEL_ID,
                        dim=SETTINGS.EMBEDDING_DIM,
                    )
        return _service_cache[cache_key]

    key = model_key or SETTINGS.EMBEDDING_MODEL_KEY
    if key not in _service_cache:
        with _cache_lock:
            if key not in _service_cache:
                _service_cache[key] = EmbeddingService(key)
    return _service_cache[key]
