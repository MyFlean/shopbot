"""
Unit tests for BedrockTitanEmbeddingService (shopbot-main's runtime query
embedder). Mocks requests.post — no live AWS credentials or network access
required. These tests validate the request/response contract and, most
importantly, the raise-not-None failure contract that unified_search.py's
existing V1 fallback depends on (see bedrock_embedding_service.py's module
docstring).
"""
from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from search_v2.embedding.bedrock_embedding_service import (
    BedrockEmbeddingError,
    BedrockTitanEmbeddingService,
)


def _service() -> BedrockTitanEmbeddingService:
    return BedrockTitanEmbeddingService(
        bearer_token="ABSK-fake-token-for-tests",
        region="ap-south-1",
        model_id="amazon.titan-embed-text-v2:0",
        dim=512,
    )


def _mock_response(status_code: int, json_body: dict | None = None, text: str = ""):
    resp = Mock()
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    resp.text = text or str(json_body or "")
    return resp


class TestConstruction:
    def test_missing_bearer_token_raises_immediately(self):
        with pytest.raises(RuntimeError, match="Missing AWS_BEARER_TOKEN_BEDROCK"):
            BedrockTitanEmbeddingService(
                bearer_token="", region="ap-south-1", model_id="amazon.titan-embed-text-v2:0", dim=512,
            )

    def test_endpoint_url_shape(self):
        svc = _service()
        assert svc.endpoint == (
            "https://bedrock-runtime.ap-south-1.amazonaws.com"
            "/model/amazon.titan-embed-text-v2:0/invoke"
        )

    def test_dim_and_model_label(self):
        svc = _service()
        assert svc.dim == 512
        assert svc.model_label == "amazon.titan-embed-text-v2:0"


class TestEmbedQuerySuccess:
    @patch("search_v2.embedding.bedrock_embedding_service.requests.post")
    def test_returns_embedding_on_200(self, mock_post):
        mock_post.return_value = _mock_response(200, {"embedding": [0.1, 0.2, 0.3], "inputTextTokenCount": 4})
        svc = _service()
        vector = svc.embed_query("chips under 300 calories")
        assert vector == [0.1, 0.2, 0.3]
        assert mock_post.call_count == 1

    @patch("search_v2.embedding.bedrock_embedding_service.requests.post")
    def test_request_body_shape(self, mock_post):
        mock_post.return_value = _mock_response(200, {"embedding": [0.0] * 512})
        svc = _service()
        svc.embed_query("greek yogurt")
        _, kwargs = mock_post.call_args
        import json
        body = json.loads(kwargs["data"])
        assert body == {"inputText": "greek yogurt", "dimensions": 512, "normalize": True}
        assert kwargs["headers"]["Authorization"] == "Bearer ABSK-fake-token-for-tests"

    @patch("search_v2.embedding.bedrock_embedding_service.requests.post")
    def test_empty_query_returns_none_without_calling_bedrock(self, mock_post):
        svc = _service()
        assert svc.embed_query("") is None
        assert svc.embed_query(None) is None
        mock_post.assert_not_called()


class TestEmbedQueryFailure_RaisesNotNone:
    """The critical contract: on failure, embed_query() RAISES rather than
    returning None — this is what lets unified_search.py's existing V1
    fallback catch it, instead of degrading to lexical-only Search V2."""

    @patch("search_v2.embedding.bedrock_embedding_service.requests.post")
    def test_non_retryable_4xx_raises_immediately(self, mock_post):
        mock_post.return_value = _mock_response(403, text='{"Message":"Invalid API Key format"}')
        svc = _service()
        with pytest.raises(BedrockEmbeddingError, match="HTTP 403"):
            svc.embed_query("test query")
        assert mock_post.call_count == 1  # no retry burned on a non-retryable error

    @patch("search_v2.embedding.bedrock_embedding_service.requests.post")
    def test_throttle_retries_then_raises(self, mock_post):
        mock_post.return_value = _mock_response(429, text="Too many requests")
        svc = _service()
        with patch("search_v2.embedding.bedrock_embedding_service.time.sleep"):
            with pytest.raises(BedrockEmbeddingError, match="failed after 2 attempts"):
                svc.embed_query("test query")
        assert mock_post.call_count == 2  # _MAX_ATTEMPTS

    @patch("search_v2.embedding.bedrock_embedding_service.requests.post")
    def test_throttle_then_success_recovers(self, mock_post):
        mock_post.side_effect = [
            _mock_response(429, text="Too many requests"),
            _mock_response(200, {"embedding": [0.5] * 512}),
        ]
        svc = _service()
        with patch("search_v2.embedding.bedrock_embedding_service.time.sleep"):
            vector = svc.embed_query("test query")
        assert vector == [0.5] * 512
        assert mock_post.call_count == 2

    @patch("search_v2.embedding.bedrock_embedding_service.requests.post")
    def test_network_error_raises_not_none(self, mock_post):
        import requests as requests_module
        mock_post.side_effect = requests_module.ConnectionError("connection reset")
        svc = _service()
        with patch("search_v2.embedding.bedrock_embedding_service.time.sleep"):
            with pytest.raises(BedrockEmbeddingError):
                svc.embed_query("test query")

    @patch("search_v2.embedding.bedrock_embedding_service.requests.post")
    def test_malformed_200_response_raises(self, mock_post):
        mock_post.return_value = _mock_response(200, {"unexpected": "shape"})
        svc = _service()
        with pytest.raises(BedrockEmbeddingError, match="no usable 'embedding' field"):
            svc.embed_query("test query")


class TestEmbedPassagesNotImplemented:
    def test_embed_passages_raises_not_implemented(self):
        """Bulk passage embedding is the Search repo's responsibility, not
        shopbot-main's — calling this here should fail loudly, not silently
        no-op, since that would indicate indexing work misrouted through the
        runtime service."""
        svc = _service()
        with pytest.raises(NotImplementedError, match="Search repo's responsibility"):
            svc.embed_passages(["some text"])


class TestIsAvailable:
    @patch("search_v2.embedding.bedrock_embedding_service.requests.post")
    def test_is_available_true_on_success(self, mock_post):
        mock_post.return_value = _mock_response(200, {"embedding": [0.0] * 512})
        assert _service().is_available() is True

    @patch("search_v2.embedding.bedrock_embedding_service.requests.post")
    def test_is_available_false_on_failure(self, mock_post):
        mock_post.return_value = _mock_response(403, text="denied")
        assert _service().is_available() is False
