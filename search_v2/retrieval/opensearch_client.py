"""
search_v2/retrieval/opensearch_client.py
─────────────────────────────────────────────────
Thin wrapper for issuing search requests against the OpenSearch domain.
Deliberately reuses indexing_es_client.py (already in this repo, at the
search/ root, proven working for both Elastic-Cloud-ApiKey and AWS-IAM/SigV4
auth) rather than writing a third copy of that signing logic — V1 had its own
copy in shopbot/es_products.py; this is "reuse existing code where it
provides real value" applied directly.

indexing_es_client.py is a flat script at the search repo root (not inside a
package), so it's imported the same way index.products-v4.py and
setup_search_pipeline.py already do: `import indexing_es_client` works when
the working directory is the search repo root (or it's otherwise on
sys.path) — consistent with this repo's existing convention, not a new one.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class OpenSearchClient:
    def __init__(self, es_url: str = "", api_key: str = "", index: str = "", settings=None):
        if settings is not None:
            self.es_url = settings.ES_URL
            self.api_key = settings.ES_API_KEY
            self.index = settings.INDEX_NAME
        else:
            self.es_url = es_url
            self.api_key = api_key
            self.index = index
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        import indexing_es_client as iec  # see module docstring

        use_iam = iec.use_iam_from_env(self.es_url)
        self._client = iec.build_search_client(self.es_url, self.api_key, use_aoss=use_iam, force_opensearch_client=True)
        self._use_iam = use_iam
        return self._client

    def search(self, body: Dict[str, Any], query_params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """`query_params` (e.g. {"search_pipeline": "..."}) gets passed through
        as extra query-string params — both opensearch-py and elasticsearch-py
        clients accept arbitrary extra kwargs on .search() for this purpose."""
        client = self._get_client()
        query_params = query_params or {}
        return client.search(index=self.index, body=body, **query_params)

    def suggest(self, body: Dict[str, Any]) -> Dict[str, Any]:
        return self.search(body)

    def mget(self, ids: List[str], index: Optional[str] = None) -> Dict[str, Any]:
        """Fetch multiple documents by ID in a single request (ES/OS mget API)."""
        client = self._get_client()
        idx = index or self.index
        body = {"ids": ids}
        return client.mget(index=idx, body=body)


def extract_hits(response: Dict[str, Any]) -> list:
    """[(doc_id, score, source_dict), ...] in rank order, from a raw OpenSearch
    response. Shared by every retrieval mode so downstream code (fusion,
    business ranking, the playground) deals with one consistent shape
    regardless of which query produced it."""
    hits = response.get("hits", {}).get("hits", [])
    return [(hit.get("_id") or hit.get("_source", {}).get("id"), hit.get("_score", 0.0), hit.get("_source", {})) for hit in hits]
