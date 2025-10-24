from __future__ import annotations

import logging
from typing import Dict, Generator, Optional

import anthropic  # type: ignore

from ..config import get_config

log = logging.getLogger(__name__)


class AnthropicStreamer:
    """
    Synchronous streaming wrapper for Anthropic messages.stream.
    - Logs every event type for debugging/observability
    - Emits text deltas for each content_block_delta with type=text_delta
    - Uses the SDK's expected message shape (content blocks)
    """

    def __init__(self) -> None:
        cfg = get_config()
        self._client = anthropic.Anthropic(api_key=cfg.ANTHROPIC_API_KEY)
        self._model = cfg.LLM_MODEL
        self._max_tokens = cfg.LLM_MAX_TOKENS

    def stream_text(self, prompt: str, *, temperature: float = 0.2, max_tokens: Optional[int] = None) -> Generator[Dict, None, Dict]:
        """
        Stream plain text deltas.
        - Yields dicts: {type: 'text_delta', text: '...'} for each text delta chunk
        - Returns final message dict at StopIteration via generator return value
        """
        model = self._model
        mx = int(max_tokens or self._max_tokens)

        with self._client.messages.stream(
            model=model,
            messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
            temperature=temperature,
            max_tokens=mx,
        ) as stream:
            try:
                for event in stream:
                    # Robust event type extraction (SDK event objects or dicts)
                    et = getattr(event, "type", None)
                    if et is None and isinstance(event, dict):
                        et = event.get("type")

                    # Log every event type (truncate payload for safety)
                    try:
                        log.debug(f"ANTHROPIC_EVT | type={et} | preview={str(event)[:200]}")
                    except Exception:
                        pass

                    if et == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        if delta is None and isinstance(event, dict):
                            delta = event.get("delta")
                        if delta is not None:
                            dt = getattr(delta, "type", None)
                            if dt is None and isinstance(delta, dict):
                                dt = delta.get("type")
                            if dt == "text_delta":
                                text = getattr(delta, "text", None)
                                if text is None and isinstance(delta, dict):
                                    text = delta.get("text", "")
                                if text:
                                    yield {"type": "text_delta", "text": text}
                    # Other notable events are logged but not yielded

                # Final message for completeness
                try:
                    final = stream.get_final_message()
                except Exception:
                    final = None
                return {"final": final}
            except Exception as e:
                log.error(f"ANTHROPIC_STREAM_ERROR | {e}")
                raise


