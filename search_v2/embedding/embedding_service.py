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
# Lets the playground hold multiple models loaded simultaneously for
# side-by-side comparison (memory permitting — see playground docs) without
# reloading on every request.
_service_cache: Dict[str, EmbeddingService] = {}
_cache_lock = threading.Lock()


def get_embedding_service(model_key: Optional[str] = None) -> EmbeddingService:
    from search_v2.config.settings import SETTINGS

    key = model_key or SETTINGS.EMBEDDING_MODEL_KEY
    if key not in _service_cache:
        with _cache_lock:
            if key not in _service_cache:
                _service_cache[key] = EmbeddingService(key)
    return _service_cache[key]
