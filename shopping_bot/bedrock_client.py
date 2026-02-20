# shopping_bot/bedrock_client.py
"""
AWS Bedrock Client Wrapper for Claude Models
────────────────────────────────────────────
Provides async interface to AWS Bedrock Converse API with:
- Bearer token authentication (API key)
- Anthropic-style tool schema conversion to Bedrock format
- Response normalization to match Anthropic SDK response structure
- Vision/image support

Uses aiohttp for async HTTP requests with bearer token auth.
This approach works with Bedrock API keys which use Authorization: Bearer.

Created: Feb 2026
Purpose: Replace direct Anthropic API calls with AWS Bedrock
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

import aiohttp
import requests

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Response Data Classes (mimicking Anthropic SDK structure)
# ─────────────────────────────────────────────────────────────

@dataclass
class ToolUseBlock:
    """Mimics Anthropic's ToolUseBlock structure"""
    type: str = "tool_use"
    id: str = ""
    name: str = ""
    input: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.input is None:
            self.input = {}


@dataclass
class TextBlock:
    """Mimics Anthropic's TextBlock structure"""
    type: str = "text"
    text: str = ""


@dataclass
class Usage:
    """Mimics Anthropic's Usage structure"""
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class BedrockResponse:
    """
    Normalized response structure that mimics Anthropic's Message response.
    This allows existing code using pick_tool() and response parsing to work unchanged.
    """
    content: List[Union[ToolUseBlock, TextBlock]]
    stop_reason: str
    usage: Usage
    model: str = ""
    
    def __init__(self, content=None, stop_reason="end_turn", usage=None, model=""):
        self.content = content or []
        self.stop_reason = stop_reason
        self.usage = usage or Usage()
        self.model = model


# ─────────────────────────────────────────────────────────────
# Tool Schema Converter
# ─────────────────────────────────────────────────────────────

def convert_anthropic_tool_to_bedrock(tool: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert Anthropic tool schema to Bedrock Converse API format.
    
    Anthropic format:
        {
            "name": "classify_intent",
            "description": "...",
            "input_schema": {"type": "object", "properties": {...}}
        }
    
    Bedrock format:
        {
            "toolSpec": {
                "name": "classify_intent",
                "description": "...",
                "inputSchema": {"json": {"type": "object", "properties": {...}}}
            }
        }
    """
    return {
        "toolSpec": {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "inputSchema": {
                "json": tool.get("input_schema", {"type": "object", "properties": {}})
            }
        }
    }


def convert_anthropic_tool_choice_to_bedrock(tool_choice: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert Anthropic tool_choice to Bedrock format.
    
    Anthropic: {"type": "tool", "name": "classify_intent"}
    Bedrock:   {"tool": {"name": "classify_intent"}}
    
    Anthropic: {"type": "any"}
    Bedrock:   {"any": {}}
    
    Anthropic: {"type": "auto"} or None
    Bedrock:   {"auto": {}}
    """
    if tool_choice is None:
        return {"auto": {}}
    
    choice_type = tool_choice.get("type", "auto")
    
    if choice_type == "tool":
        return {"tool": {"name": tool_choice.get("name", "")}}
    elif choice_type == "any":
        return {"any": {}}
    else:
        return {"auto": {}}


def convert_anthropic_messages_to_bedrock(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Convert Anthropic message format to Bedrock Converse format.
    
    Anthropic: {"role": "user", "content": "text"} 
           or: {"role": "user", "content": [{"type": "text", "text": "..."}, {"type": "image", ...}]}
    
    Bedrock:   {"role": "user", "content": [{"text": "..."}]}
           or: {"role": "user", "content": [{"text": "..."}, {"image": {...}}]}
    """
    bedrock_messages = []
    
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        
        bedrock_content = []
        
        if isinstance(content, str):
            # Simple string content
            bedrock_content.append({"text": content})
        elif isinstance(content, list):
            # Array of content blocks
            for block in content:
                if isinstance(block, str):
                    bedrock_content.append({"text": block})
                elif isinstance(block, dict):
                    block_type = block.get("type", "text")
                    
                    if block_type == "text":
                        bedrock_content.append({"text": block.get("text", "")})
                    elif block_type == "image":
                        # Convert Anthropic image format to Bedrock
                        source = block.get("source", {})
                        if source.get("type") == "base64":
                            bedrock_content.append({
                                "image": {
                                    "format": _get_image_format(source.get("media_type", "image/jpeg")),
                                    "source": {
                                        "bytes": source.get("data", "")  # Pass base64 string directly
                                    }
                                }
                            })
        else:
            # Fallback
            bedrock_content.append({"text": str(content)})
        
        bedrock_messages.append({
            "role": role,
            "content": bedrock_content
        })
    
    return bedrock_messages


def _get_image_format(media_type: str) -> str:
    """Convert MIME type to Bedrock image format"""
    format_map = {
        "image/jpeg": "jpeg",
        "image/jpg": "jpeg", 
        "image/png": "png",
        "image/gif": "gif",
        "image/webp": "webp"
    }
    return format_map.get(media_type.lower(), "jpeg")


def _parse_bedrock_response(response: Dict[str, Any], model: str) -> BedrockResponse:
    """
    Parse Bedrock Converse API response into Anthropic-compatible structure.
    
    Bedrock response structure:
    {
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {"text": "..."},
                    {"toolUse": {"toolUseId": "...", "name": "...", "input": {...}}}
                ]
            }
        },
        "stopReason": "tool_use" | "end_turn" | ...,
        "usage": {"inputTokens": N, "outputTokens": N}
    }
    """
    content_blocks = []
    
    output = response.get("output", {})
    message = output.get("message", {})
    raw_content = message.get("content", [])
    
    for block in raw_content:
        if "text" in block:
            content_blocks.append(TextBlock(type="text", text=block["text"]))
        elif "toolUse" in block:
            tool_use = block["toolUse"]
            content_blocks.append(ToolUseBlock(
                type="tool_use",
                id=tool_use.get("toolUseId", ""),
                name=tool_use.get("name", ""),
                input=tool_use.get("input", {})
            ))
    
    # Map Bedrock stop reasons to Anthropic format
    stop_reason_map = {
        "tool_use": "tool_use",
        "end_turn": "end_turn",
        "max_tokens": "max_tokens",
        "stop_sequence": "stop_sequence"
    }
    stop_reason = stop_reason_map.get(response.get("stopReason", "end_turn"), "end_turn")
    
    usage_data = response.get("usage", {})
    usage = Usage(
        input_tokens=usage_data.get("inputTokens", 0),
        output_tokens=usage_data.get("outputTokens", 0)
    )
    
    return BedrockResponse(
        content=content_blocks,
        stop_reason=stop_reason,
        usage=usage,
        model=model
    )


# ─────────────────────────────────────────────────────────────
# Async Bedrock Client (using aiohttp)
# ─────────────────────────────────────────────────────────────

class AsyncBedrockClient:
    """
    Async wrapper for AWS Bedrock Converse API using direct HTTP requests.
    
    Uses aiohttp with bearer token authentication.
    This is compatible with AWS Bedrock API keys (ABSK...).
    
    Usage:
        client = AsyncBedrockClient(
            bearer_token="ABSK...",
            region="ap-south-1",
            model_id="us.anthropic.claude-3-5-sonnet-20241022-v2:0"
        )
        
        resp = await client.converse(
            messages=[{"role": "user", "content": "Hello"}],
            tools=[TOOL],
            tool_choice={"type": "tool", "name": "my_tool"},
            temperature=0,
            max_tokens=1000
        )
    """
    
    def __init__(
        self,
        bearer_token: str,
        region: str = "ap-south-1",
        model_id: str = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
    ):
        """
        Initialize Bedrock client.
        
        Args:
            bearer_token: AWS Bedrock API key (starts with ABSK)
            region: AWS region (default: ap-south-1 Mumbai)
            model_id: Bedrock model identifier
        """
        self.bearer_token = bearer_token
        self.region = region
        self.model_id = model_id
        
        # Validate bearer token
        if not bearer_token:
            raise RuntimeError("Missing AWS_BEARER_TOKEN_BEDROCK. Set it in environment.")
        
        # Build the Bedrock runtime endpoint
        self.endpoint = f"https://bedrock-runtime.{region}.amazonaws.com"
        
        log.info(f"BEDROCK_CLIENT_INIT | region={region} | model={model_id} | endpoint={self.endpoint}")
    
    async def converse(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Dict[str, Any]] = None,
        temperature: float = 0,
        max_tokens: int = 1000,
        system: Optional[str] = None,
        model: Optional[str] = None
    ) -> BedrockResponse:
        """
        Call Bedrock Converse API with Anthropic-compatible interface.
        
        Args:
            messages: List of message dicts (Anthropic format, auto-converted)
            tools: List of tool definitions (Anthropic format, auto-converted)
            tool_choice: Tool choice config (Anthropic format, auto-converted)
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            system: Optional system prompt
            model: Optional model override
            
        Returns:
            BedrockResponse: Anthropic-compatible response object
        """
        model_id = model or self.model_id
        
        # Convert messages to Bedrock format
        bedrock_messages = convert_anthropic_messages_to_bedrock(messages)
        
        # Build request body
        request_body = {
            "modelId": model_id,
            "messages": bedrock_messages,
            "inferenceConfig": {
                "maxTokens": max_tokens,
                "temperature": temperature
            }
        }
        
        # Add system prompt if provided
        if system:
            request_body["system"] = [{"text": system}]
        
        # Add tools if provided
        if tools:
            bedrock_tools = [convert_anthropic_tool_to_bedrock(t) for t in tools]
            request_body["toolConfig"] = {
                "tools": bedrock_tools
            }
            
            # Add tool choice if provided
            if tool_choice:
                request_body["toolConfig"]["toolChoice"] = convert_anthropic_tool_choice_to_bedrock(tool_choice)
        
        # Build URL for converse endpoint
        url = f"{self.endpoint}/model/{model_id}/converse"
        
        headers = {
            "Authorization": f"Bearer {self.bearer_token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        try:
            log.info(f"BEDROCK_CONVERSE | model={model_id} | temp={temperature} | max_tokens={max_tokens} | tools={len(tools) if tools else 0}")
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=request_body, headers=headers, timeout=aiohttp.ClientTimeout(total=120)) as response:
                    response_text = await response.text()
                    
                    if response.status != 200:
                        log.error(f"BEDROCK_ERROR | status={response.status} | response={response_text[:500]}")
                        raise RuntimeError(f"Bedrock API error: {response.status} - {response_text[:200]}")
                    
                    response_json = json.loads(response_text)
            
            # Parse response to Anthropic-compatible format
            result = _parse_bedrock_response(response_json, model_id)
            
            log.info(f"BEDROCK_RESPONSE | stop_reason={result.stop_reason} | input_tokens={result.usage.input_tokens} | output_tokens={result.usage.output_tokens}")
            
            return result
            
        except aiohttp.ClientError as e:
            log.error(f"BEDROCK_CLIENT_ERROR | error={e}")
            raise RuntimeError(f"Bedrock client error: {e}")
        except json.JSONDecodeError as e:
            log.error(f"BEDROCK_JSON_ERROR | error={e}")
            raise RuntimeError(f"Failed to parse Bedrock response: {e}")
        except Exception as e:
            log.error(f"BEDROCK_CONVERSE_ERROR | error={e}")
            raise


# ─────────────────────────────────────────────────────────────
# Sync Bedrock Client (using requests)
# ─────────────────────────────────────────────────────────────

class BedrockClient:
    """
    Synchronous wrapper for AWS Bedrock Converse API using direct HTTP requests.
    Used in code paths that don't use async/await.
    """
    
    def __init__(
        self,
        bearer_token: str,
        region: str = "ap-south-1",
        model_id: str = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
    ):
        self.bearer_token = bearer_token
        self.region = region
        self.model_id = model_id
        
        if not bearer_token:
            raise RuntimeError("Missing AWS_BEARER_TOKEN_BEDROCK. Set it in environment.")
        
        self.endpoint = f"https://bedrock-runtime.{region}.amazonaws.com"
        log.info(f"BEDROCK_SYNC_CLIENT_INIT | region={region} | model={model_id}")
    
    def converse(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Dict[str, Any]] = None,
        temperature: float = 0,
        max_tokens: int = 1000,
        system: Optional[str] = None,
        model: Optional[str] = None
    ) -> BedrockResponse:
        """Synchronous Bedrock Converse API call."""
        model_id = model or self.model_id
        
        bedrock_messages = convert_anthropic_messages_to_bedrock(messages)
        
        request_body = {
            "modelId": model_id,
            "messages": bedrock_messages,
            "inferenceConfig": {
                "maxTokens": max_tokens,
                "temperature": temperature
            }
        }
        
        if system:
            request_body["system"] = [{"text": system}]
        
        if tools:
            bedrock_tools = [convert_anthropic_tool_to_bedrock(t) for t in tools]
            request_body["toolConfig"] = {
                "tools": bedrock_tools
            }
            
            if tool_choice:
                request_body["toolConfig"]["toolChoice"] = convert_anthropic_tool_choice_to_bedrock(tool_choice)
        
        url = f"{self.endpoint}/model/{model_id}/converse"
        
        headers = {
            "Authorization": f"Bearer {self.bearer_token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        try:
            log.info(f"BEDROCK_SYNC_CONVERSE | model={model_id} | temp={temperature} | max_tokens={max_tokens}")
            
            response = requests.post(url, json=request_body, headers=headers, timeout=120)
            
            if response.status_code != 200:
                log.error(f"BEDROCK_SYNC_ERROR | status={response.status_code} | response={response.text[:500]}")
                raise RuntimeError(f"Bedrock API error: {response.status_code} - {response.text[:200]}")
            
            response_json = response.json()
            result = _parse_bedrock_response(response_json, model_id)
            
            log.info(f"BEDROCK_SYNC_RESPONSE | stop_reason={result.stop_reason} | input_tokens={result.usage.input_tokens} | output_tokens={result.usage.output_tokens}")
            
            return result
            
        except requests.RequestException as e:
            log.error(f"BEDROCK_SYNC_REQUEST_ERROR | error={e}")
            raise RuntimeError(f"Bedrock request error: {e}")
        except Exception as e:
            log.error(f"BEDROCK_SYNC_CONVERSE_ERROR | error={e}")
            raise


# ─────────────────────────────────────────────────────────────
# Factory Functions
# ─────────────────────────────────────────────────────────────

def get_async_bedrock_client() -> AsyncBedrockClient:
    """
    Factory function to create AsyncBedrockClient from environment config.
    """
    from .config import get_config
    cfg = get_config()
    
    return AsyncBedrockClient(
        bearer_token=cfg.AWS_BEARER_TOKEN_BEDROCK,
        region=cfg.BEDROCK_REGION,
        model_id=cfg.BEDROCK_MODEL_ID
    )


def get_bedrock_client() -> BedrockClient:
    """
    Factory function to create sync BedrockClient from environment config.
    """
    from .config import get_config
    cfg = get_config()
    
    return BedrockClient(
        bearer_token=cfg.AWS_BEARER_TOKEN_BEDROCK,
        region=cfg.BEDROCK_REGION,
        model_id=cfg.BEDROCK_MODEL_ID
    )
