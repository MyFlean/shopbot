# shopping_bot/streaming/anthropic_stream.py
"""
Streaming Module (DEPRECATED)
────────────────────────────
This module previously provided streaming support via Anthropic's streaming API.

With the migration to AWS Bedrock API keys, streaming is NOT supported.
AWS Bedrock API keys cannot use InvokeModelWithBidirectionalStream.

The streaming feature flag (ENABLE_STREAMING) should be set to false.
All LLM calls now use the non-streaming converse API via bedrock_client.py.

Kept for backwards compatibility - methods raise NotImplementedError.
"""

from __future__ import annotations

import logging
from typing import Dict, Generator, Optional

log = logging.getLogger(__name__)


class AnthropicStreamer:
    """
    DEPRECATED: Streaming is not supported with AWS Bedrock API keys.
    
    This class is kept for backwards compatibility but all methods
    will raise NotImplementedError.
    
    Use the non-streaming BedrockClient.converse() method instead.
    """

    def __init__(self) -> None:
        log.warning(
            "STREAMING_DEPRECATED | AnthropicStreamer is deprecated. "
            "Streaming is not supported with AWS Bedrock API keys. "
            "Use BedrockClient.converse() for non-streaming calls."
        )

    def stream_text(
        self, 
        prompt: str, 
        *, 
        temperature: float = 0.2, 
        max_tokens: Optional[int] = None
    ) -> Generator[Dict, None, Dict]:
        """
        DEPRECATED: Streaming not supported with Bedrock API keys.
        
        Raises:
            NotImplementedError: Streaming is disabled in this architecture.
        """
        raise NotImplementedError(
            "Streaming is not supported with AWS Bedrock API keys. "
            "Set ENABLE_STREAMING=false and use non-streaming converse API. "
            "See bedrock_client.py for the recommended approach."
        )
