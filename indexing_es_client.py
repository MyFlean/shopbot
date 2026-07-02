"""
Elasticsearch (Elastic Cloud / API key) vs Amazon OpenSearch (SigV4 / IAM).

Supports two AWS SigV4 flavors:
  - Amazon OpenSearch Serverless (AOSS)       → service = "aoss", host *.aoss.amazonaws.com
  - Amazon OpenSearch provisioned domain      → service = "es",   host *.es.amazonaws.com

Used by index.products-v2.py and index.products-v3.py.
"""
from __future__ import annotations

import os
from typing import Any, Optional
from urllib.parse import urlparse


def aoss_url_indicates_serverless(es_url: str) -> bool:
    return "aoss.amazonaws.com" in (es_url or "").lower()


def es_url_indicates_managed(es_url: str) -> bool:
    """Amazon OpenSearch provisioned domain URL (*.<region>.es.amazonaws.com)."""
    s = (es_url or "").lower()
    return ".es.amazonaws.com" in s and ".aoss.amazonaws.com" not in s


def sigv4_service_for_url(es_url: str) -> Optional[str]:
    """Return 'aoss', 'es', or None depending on the AWS OpenSearch URL flavor."""
    if aoss_url_indicates_serverless(es_url):
        return "aoss"
    if es_url_indicates_managed(es_url):
        return "es"
    return None


def use_aoss_from_env(es_url_explicit: Optional[str]) -> bool:
    """Legacy: True only for OpenSearch Serverless. Kept for index.products-v2.py."""
    if os.getenv("AOSS_ENABLED", "").lower() in ("1", "true", "yes"):
        return True
    if es_url_explicit and aoss_url_indicates_serverless(es_url_explicit):
        return True
    return False


def use_iam_from_env(es_url_explicit: Optional[str]) -> bool:
    """Generalized: True for either AOSS or a provisioned Amazon OpenSearch domain."""
    if os.getenv("AOSS_ENABLED", "").lower() in ("1", "true", "yes"):
        return True
    if os.getenv("ES_USE_IAM", "").lower() in ("1", "true", "yes"):
        return True
    if es_url_explicit and (
        aoss_url_indicates_serverless(es_url_explicit)
        or es_url_indicates_managed(es_url_explicit)
    ):
        return True
    return False


def build_search_client(
    es_url: str,
    es_api_key: str,
    *,
    use_aoss: bool,
    aws_region: Optional[str] = None,
    force_opensearch_client: bool = False,
) -> Any:
    """
    Returns elasticsearch.Elasticsearch or opensearchpy.OpenSearch.

    `use_aoss` is interpreted as "use AWS SigV4 auth". The actual signing service
    is derived from the URL: 'aoss' for *.aoss.amazonaws.com, 'es' for
    *.es.amazonaws.com. Defaults to 'aoss' for back-compat if detection fails.

    `force_opensearch_client` (new, default False — fully backward compatible):
    use the opensearch-py client WITHOUT AWS SigV4 auth. This is for local or
    self-hosted OpenSearch instances — `use_aoss=False` alone is not enough to
    express that case, since it previously meant "use elasticsearch.Elasticsearch",
    and the elasticsearch-py 8.x client sends an
    `Accept`/`Content-Type: application/vnd.elasticsearch+json; compatible-with=8`
    header that OpenSearch's server rejects with a 406 — that header is not a
    bug to work around by stripping it; it means the wrong client library is
    talking to the wrong engine. Auth for this path: HTTP Basic if `es_api_key`
    is supplied as "user:pass", otherwise no auth at all (the common local-dev
    case — a local OpenSearch instance with its security plugin disabled).
    """
    aws_region = (
        aws_region
        or os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or "ap-south-1"
    )
    if use_aoss:
        try:
            import boto3
            from opensearchpy import OpenSearch, RequestsHttpConnection
            from requests_aws4auth import AWS4Auth
        except ImportError as e:
            raise RuntimeError(
                "AWS OpenSearch needs opensearch-py, requests-aws4auth, and boto3. "
                "From the search repo: python3 -m pip install -r requirements.txt"
            ) from e

        raw = es_url.strip()
        parsed = urlparse(raw if raw.startswith("http") else f"https://{raw}")
        host = parsed.hostname
        if not host:
            raise ValueError(f"Invalid AWS OpenSearch ES_URL: {es_url!r}")
        port = parsed.port or 443
        service = sigv4_service_for_url(es_url) or "aoss"
        creds = boto3.Session(region_name=aws_region).get_credentials()
        if creds is None:
            raise RuntimeError(
                "AWS credentials are required for Amazon OpenSearch IAM auth; "
                "configure the instance/task profile or AWS_ACCESS_KEY_ID."
            )
        auth = AWS4Auth(
            creds.access_key,
            creds.secret_key,
            aws_region,
            service,
            session_token=creds.token,
        )
        return OpenSearch(
            hosts=[{"host": host, "port": port}],
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            timeout=300,
        )

    if force_opensearch_client:
        try:
            from opensearchpy import OpenSearch, RequestsHttpConnection
        except ImportError as e:
            raise RuntimeError(
                "OpenSearch needs opensearch-py. From the search repo: "
                "python3 -m pip install -r requirements.txt"
            ) from e

        raw = es_url.strip()
        parsed = urlparse(raw if raw.startswith("http") else f"https://{raw}")
        host = parsed.hostname
        if not host:
            raise ValueError(f"Invalid OpenSearch ES_URL: {es_url!r}")
        use_ssl = parsed.scheme == "https"
        port = parsed.port or (443 if use_ssl else 9200)

        http_auth = None
        if es_api_key and ":" in es_api_key:
            user, _, password = es_api_key.partition(":")
            http_auth = (user, password)
        # else: no auth — the common case for local-dev OpenSearch with its
        # security plugin disabled (confirmed: this is what's actually being
        # run against right now).

        return OpenSearch(
            hosts=[{"host": host, "port": port}],
            http_auth=http_auth,
            use_ssl=use_ssl,
            verify_certs=use_ssl,  # plain http (local dev) has no certs to verify
            connection_class=RequestsHttpConnection,
            timeout=300,
        )

    from elasticsearch import Elasticsearch

    return Elasticsearch(es_url, api_key=es_api_key).options(request_timeout=300)


def create_index_with_mapping(
    client: Any,
    index: str,
    mapping_config: dict,
    *,
    use_aoss: bool,
    force_opensearch_client: bool = False,
) -> None:
    """Create index if it does not exist (idempotent).

    The body= vs settings=/mappings= kwarg choice below depends on which
    CLIENT LIBRARY built `client` (opensearchpy vs elasticsearch-py), not on
    AWS SigV4 specifically — `use_aoss or force_opensearch_client` together
    cover both ways `client` can end up being an opensearchpy.OpenSearch
    instance (see build_search_client above)."""
    if client.indices.exists(index=index):
        return
    settings = mapping_config.get("settings")
    mappings = mapping_config.get("mappings")
    if use_aoss or force_opensearch_client:
        client.indices.create(
            index=index,
            body={"settings": settings, "mappings": mappings},
        )
    else:
        client.indices.create(
            index=index,
            settings=settings,
            mappings=mappings,
        )
