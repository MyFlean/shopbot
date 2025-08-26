from __future__ import annotations
from typing import Any, Dict
from dataclasses import asdict, is_dataclass
from enum import Enum

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
    c = content or {}
    if bot_resp_type == ResponseType.QUESTION:
        return "ask_user"
    if bot_resp_type == ResponseType.FINAL_ANSWER:
        if any(k in c for k in ("products", "summary_message", "sections")):
            return "final_answer"
        return "casual"
    if bot_resp_type == ResponseType.PROCESSING_STUB:
        return "processing"
    if bot_resp_type == ResponseType.ERROR:
        return "error"
    return "casual"


def normalize_content(bot_resp_type: ResponseType, content: Dict[str, Any] | None) -> Dict[str, Any]:
    # Ensure content is a dict and handle edge cases
    if not isinstance(content, dict):
        content = {}
    
    c = content or {}

    if bot_resp_type == ResponseType.QUESTION:
        question = c.get("message") or c.get("question") or "Please provide more details."
        options = c.get("options") or c.get("choices")
        ctx = {}
        if "currently_asking" in c:
            ctx["missing_slot"] = c["currently_asking"]
        out: Dict[str, Any] = {"question": question}
        if options:
            out["options"] = options
        if ctx:
            out["context"] = ctx
        return out

    if bot_resp_type == ResponseType.FINAL_ANSWER:
        return {
            "summary_message": c.get("summary_message", c.get("message", "")),
            "products": c.get("products", []),
        }

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
    try:
        # Validate inputs
        if not isinstance(bot_resp_type, ResponseType):
            raise ValueError(f"Invalid bot_resp_type: {type(bot_resp_type)}")
        
        if not isinstance(ctx, UserContext):
            raise ValueError(f"Invalid ctx: {type(ctx)}")
        
        # Ensure content is safe
        if content is not None and not isinstance(content, dict):
            content = {}
        
        ui_type = map_fe_response_type(bot_resp_type, content)
        normalized = normalize_content(bot_resp_type, content)

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
        # Return a safe fallback envelope
        return {
            "wa_id": wa_id,
            "session_id": session_id,
            "response_type": "error",
            "content": {"message": f"Envelope creation failed: {str(e)}"},
            "meta": {"elapsed_time": f"{elapsed_time_seconds:.3f}s"}
        }
