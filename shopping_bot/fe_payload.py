from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Dict

from .enums import ResponseType
from .models import UserContext


def _to_json_safe(obj: Any) -> Any:
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


def map_fe_response_type(bot_resp_type: ResponseType, content: Dict[str, Any] | None) -> str:
    """
    Map internal response type to FE-facing response_type.
    Simple and clean: Check for products or summary_message to determine final_answer.
    """
    if bot_resp_type == ResponseType.QUESTION:
        return "ask_user"
    
    if bot_resp_type == ResponseType.ERROR:
        return "error"
    
    if bot_resp_type == ResponseType.PROCESSING_STUB:
        return "processing"
    
    # For FINAL_ANSWER, check content structure
    if bot_resp_type == ResponseType.FINAL_ANSWER:
        c = content or {}
        # If we have products or summary_message, it's a proper final_answer
        if c.get("products") or c.get("summary_message"):
            return "final_answer"
        # Otherwise it's casual (plain message only)
        return "casual"
    
    return "casual"


def normalize_content(bot_resp_type: ResponseType, content: Dict[str, Any] | None) -> Dict[str, Any]:
    """
    Normalize the content for the FE envelope.
    The content should already have the right structure from LLM service.
    """
    if not isinstance(content, dict):
        content = {}
    c = content or {}

    # Questions
    if bot_resp_type == ResponseType.QUESTION:
        question = c.get("message") or c.get("question") or "Please provide more details."
        options = c.get("options") or c.get("choices")
        out = {"question": question}
        if options:
            out["options"] = options
        if "currently_asking" in c:
            out["context"] = {"missing_slot": c["currently_asking"]}
        return out

    # Final answers with products
    if bot_resp_type == ResponseType.FINAL_ANSWER:
        # If content already contains structured fields (summary_message/products),
        # preserve the full payload including optional keys like ux_response and product_intent
        if (c.get("products") is not None) or (c.get("summary_message") is not None):
            # Enforce SPM single-product clamp
            try:
                if str(c.get("product_intent", "")).strip().lower() == "is_this_good":
                    if isinstance(c.get("products"), list) and c["products"]:
                        c["products"] = c["products"][:1]
            except Exception:
                pass
            # Ensure a text anchor when quick replies are present (SPM/MPM UX)
            try:
                ux = c.get("ux_response")
                has_qr = isinstance(ux, dict) and bool(ux.get("quick_replies"))
                if has_qr:
                    # Always use a standard anchor text for quick replies
                    c = {**c, "message": "Choose an option:"}
            except Exception:
                pass
            return c
        
        # Fallback for backward compatibility when only a message is present
        return {
            "summary_message": c.get("message", ""),
            "products": []
        }

    # Simple messages (casual/processing/error)
    return {"message": c.get("message", "")}


def build_envelope(
    *,
    wa_id: str | None,
    session_id: str,
    bot_resp_type: ResponseType,
    content: Dict[str, Any] | None,
    ctx: UserContext,
    elapsed_time_seconds: float,
    mode_async_enabled: bool | None = None,
    timestamp: str | None = None,
    functions_executed: list[str] | None = None,
) -> Dict[str, Any]:
    """
    Build the FE envelope.
    Clean and simple - the content should already be properly structured.
    """
    try:
        ui_type = map_fe_response_type(bot_resp_type, content)
        normalized = normalize_content(bot_resp_type, content or {})

        meta = {
            "elapsed_time": f"{elapsed_time_seconds:.3f}s",
            "functions_executed": functions_executed or [],
            "timestamp": timestamp,
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
            "content": {"message": f"Envelope creation failed: {str(e)}"},
            "meta": {"elapsed_time": f"{elapsed_time_seconds:.3f}s"},
        }