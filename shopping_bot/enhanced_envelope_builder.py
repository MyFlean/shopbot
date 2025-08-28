# shopping_bot/enhanced_envelope_builder.py
"""
Enhanced Envelope Builder for UX Responses
──────────────────────────────────────────
Creates frontend-compatible envelopes for both standard and UX-enhanced responses.
Maintains backward compatibility while supporting new UX patterns.
"""

from __future__ import annotations
from typing import Any, Dict
from dataclasses import asdict, is_dataclass
from enum import Enum

from .enums import EnhancedResponseType, UXIntentType, PSLType
from .models import UserContext, EnhancedBotResponse


def _to_json_safe(obj: Any) -> Any:
    """Convert objects to JSON-safe format"""
    try:
        if isinstance(obj, Enum):
            return obj.value
        if is_dataclass(obj):
            return _to_json_safe(asdict(obj))
        if isinstance(obj, dict):
            return {k: _to_json_safe(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [_to_json_safe(v) for v in obj]
        if hasattr(obj, "isoformat"):
            try:
                return obj.isoformat()
            except Exception:
                pass
        return obj
    except Exception:
        return str(obj)


def map_enhanced_response_type(
    enhanced_type: EnhancedResponseType, 
    content: Dict[str, Any] | None = None
) -> str:
    """
    Map enhanced response type to frontend-facing response_type.
    """
    mapping = {
        EnhancedResponseType.QUESTION: "ask_user",
        EnhancedResponseType.ERROR: "error", 
        EnhancedResponseType.PROCESSING_STUB: "processing",
        EnhancedResponseType.CASUAL: "casual",
        
        # New UX response types
        EnhancedResponseType.UX_SPM: "ux_spm",
        EnhancedResponseType.UX_CAROUSEL: "ux_carousel", 
        EnhancedResponseType.UX_MPM: "ux_mpm"
    }
    
    return mapping.get(enhanced_type, "casual")


def normalize_enhanced_content(
    enhanced_type: EnhancedResponseType, 
    content: Dict[str, Any] | None
) -> Dict[str, Any]:
    """
    Normalize content for enhanced response types.
    Creates structured content for UX patterns while maintaining backward compatibility.
    """
    if not isinstance(content, dict):
        content = {}
    c = content or {}

    # Handle standard response types (unchanged from original)
    if enhanced_type == EnhancedResponseType.QUESTION:
        question = c.get("message") or c.get("question") or "Please provide more details."
        options = c.get("options") or c.get("choices")
        out = {"question": question}
        if options:
            out["options"] = options
        if "currently_asking" in c:
            out["context"] = {"missing_slot": c["currently_asking"]}
        return out

    # Handle UX-enhanced response types
    if enhanced_type in [
        EnhancedResponseType.UX_SPM, 
        EnhancedResponseType.UX_CAROUSEL, 
        EnhancedResponseType.UX_MPM
    ]:
        # Create structured UX content
        normalized = {
            "ux_intent": c.get("ux_intent", "show_options"),
            "dpl": c.get("dpl", {"message": "Here are your options!"}),
            "psl": c.get("psl", {"template_type": "product_card_carousel", "products": []}),
            "quick_replies": c.get("quick_replies", []),
            "confidence_score": c.get("confidence_score", 0.8)
        }
        
        # Add backward compatibility fields
        normalized["message"] = c.get("message", normalized["dpl"]["message"])
        normalized["summary_message"] = c.get("summary_message", normalized["dpl"]["message"])
        normalized["products"] = c.get("products", [])
        
        return normalized

    # Handle casual/error/processing (standard responses)
    return {"message": c.get("message", "")}


def build_enhanced_envelope(
    *,
    wa_id: str | None,
    session_id: str,
    enhanced_response: EnhancedBotResponse,
    ctx: UserContext,
    elapsed_time_seconds: float,
    mode_async_enabled: bool | None = None,
    timestamp: str | None = None,
) -> Dict[str, Any]:
    """
    Build enhanced envelope that supports both legacy and UX response formats.
    """
    try:
        # Map response type
        ui_type = map_enhanced_response_type(enhanced_response.response_type, enhanced_response.content)
        
        # Normalize content
        normalized_content = normalize_enhanced_content(
            enhanced_response.response_type, 
            enhanced_response.content
        )
        
        # Build metadata
        meta = {
            "elapsed_time": f"{elapsed_time_seconds:.3f}s",
            "functions_executed": enhanced_response.functions_executed or [],
            "timestamp": timestamp or enhanced_response.timestamp,
            "mode_async_enabled": bool(mode_async_enabled) if mode_async_enabled is not None else None,
        }
        
        # Add UX-specific metadata if present
        if enhanced_response.ux_response:
            meta["ux_metadata"] = {
                "confidence_score": enhanced_response.ux_response.confidence_score,
                "personalization_applied": enhanced_response.ux_response.personalization_applied,
                "ux_intent": enhanced_response.ux_response.ux_intent.value
            }
        
        # Build envelope
        envelope = {
            "wa_id": ctx.session.get("wa_id") or wa_id,
            "session_id": session_id,
            "response_type": ui_type,
            "content": normalized_content,
            "meta": {k: v for k, v in meta.items() if v is not None},
        }
        
        return _to_json_safe(envelope)
        
    except Exception as e:
        # Fallback envelope
        return {
            "wa_id": wa_id,
            "session_id": session_id, 
            "response_type": "error",
            "content": {"message": f"Enhanced envelope creation failed: {str(e)}"},
            "meta": {"elapsed_time": f"{elapsed_time_seconds:.3f}s"},
        }


def build_legacy_compatible_envelope(
    *,
    wa_id: str | None,
    session_id: str,
    enhanced_response: EnhancedBotResponse,
    ctx: UserContext,
    elapsed_time_seconds: float,
    mode_async_enabled: bool | None = None,
    timestamp: str | None = None,
) -> Dict[str, Any]:
    """
    Build envelope compatible with legacy clients.
    Converts UX responses to standard final_answer format.
    """
    try:
        # Convert enhanced response to legacy format
        legacy_response = enhanced_response.to_legacy_response()
        
        # Use original envelope builder logic for backward compatibility
        from .fe_payload import map_fe_response_type, normalize_content
        
        ui_type = map_fe_response_type(legacy_response.response_type, legacy_response.content)
        normalized = normalize_content(legacy_response.response_type, legacy_response.content)
        
        meta = {
            "elapsed_time": f"{elapsed_time_seconds:.3f}s",
            "functions_executed": enhanced_response.functions_executed or [],
            "timestamp": timestamp or enhanced_response.timestamp,
            "mode_async_enabled": bool(mode_async_enabled) if mode_async_enabled is not None else None,
        }
        
        envelope = {
            "wa_id": ctx.session.get("wa_id") or wa_id,
            "session_id": session_id,
            "response_type": ui_type,
            "content": normalized,
            "meta": {k: v for k, v in meta.items() if v is not None},
        }
        
        return _to_json_safe(envelope)
        
    except Exception as e:
        return {
            "wa_id": wa_id,
            "session_id": session_id,
            "response_type": "error", 
            "content": {"message": f"Legacy envelope creation failed: {str(e)}"},
            "meta": {"elapsed_time": f"{elapsed_time_seconds:.3f}s"},
        }