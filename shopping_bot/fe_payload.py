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
        # Check for support response type from LLM
        if str(c.get("response_type")).strip().lower() in {"support", "support_related", "support_routing"} or bool(c.get("is_support_query")):
            return "support_related"
        # If we have products or summary_message, it's a proper final_answer
        if c.get("products") or c.get("summary_message"):
            return "final_answer"
        # Treat presence of UX payload or product_intent as final_answer (even if products array is empty)
        if isinstance(c.get("ux_response"), dict) or c.get("product_intent"):
            return "final_answer"
        # Otherwise it's casual (plain message only)
        return "casual"
    # New image ids response type
    if bot_resp_type == ResponseType.IMAGE_IDS:
        return "image_ids"

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
        # Normalize options into [{label,value}]
        norm_options = []
        if isinstance(options, list):
            for opt in options[:10]:
                if isinstance(opt, dict) and "label" in opt and "value" in opt:
                    norm_options.append({"label": str(opt["label"]).strip(), "value": str(opt["value"]).strip()})
                elif isinstance(opt, str):
                    val = opt.strip()
                    if val:
                        norm_options.append({"label": val, "value": val})
        out = {
            "question": question,
        }
        if norm_options:
            out["type"] = c.get("type") or "multi_choice"
            out["options"] = norm_options
        else:
            # If no options, default to free text
            out["type"] = c.get("type") or "text"
        # Context passthrough
        if isinstance(c.get("context"), dict):
            out["context"] = c.get("context")
        elif "currently_asking" in c:
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
            # Derive and inject ux_type when UX data present or intent implies it
            try:
                ux_type: str | None = None
                ux_payload = c.get("ux_response") if isinstance(c.get("ux_response"), dict) else None
                if ux_payload:
                    surface = str(ux_payload.get("ux_surface", "")).upper()
                    if surface == "SPM":
                        ux_type = "UX_SPM"
                    elif surface == "MPM":
                        ux_type = "UX_MPM"
                if not ux_payload and isinstance(c.get("product_intent"), str):
                    intent_lower = c["product_intent"].strip().lower()
                    if intent_lower == "is_this_good":
                        ux_type = "UX_SPM"
                    elif intent_lower in {"which_is_better", "show_me_options", "show_me_alternate"}:
                        ux_type = "UX_MPM"
                if ux_type:
                    c = {**c, "ux_type": ux_type}
            except Exception:
                pass

            # Ensure a text anchor when quick replies are present (SPM/MPM UX)
            try:
                ux = c.get("ux_response")
                has_qr = isinstance(ux, dict) and bool(ux.get("quick_replies"))
                if has_qr:
                    # Always use a standard anchor text for quick replies
                    c = {**c, "message": "Choose an option:"}
                    # Also ensure summary_message exists for FE consumption
                    if not c.get("summary_message"):
                        c = {**c, "summary_message": "Choose an option:"}
            except Exception:
                pass
            # Leave summary_message raw for external parser service

            # Lean MPM: keep only summary_message, ux_response (product_ids, quick_replies, ux_surface, dpl_runtime_text), and product_intent
            try:
                ux_payload = c.get("ux_response") if isinstance(c.get("ux_response"), dict) else None
                is_mpm = False
                if ux_payload:
                    surface = str(ux_payload.get("ux_surface", "")).upper()
                    is_mpm = (surface == "MPM")
                if (not is_mpm) and isinstance(c.get("product_intent"), str):
                    is_mpm = c["product_intent"].strip().lower() in {"which_is_better", "show_me_options", "show_me_alternate"}
                if is_mpm:
                    lean_ux = {}
                    if ux_payload:
                        lean_ux = {
                            "ux_surface": ux_payload.get("ux_surface"),
                            "quick_replies": ux_payload.get("quick_replies", []),
                            "product_ids": ux_payload.get("product_ids", []),
                        }
                        if ux_payload.get("dpl_runtime_text"):
                            # Leave DPL runtime text raw for external parser
                            lean_ux["dpl_runtime_text"] = ux_payload.get("dpl_runtime_text")
                    return {
                        "summary_message": c.get("summary_message", "Choose an option:"),
                        "ux_response": lean_ux,
                        "product_intent": c.get("product_intent"),
                    }
            except Exception:
                pass
            try:
                # Remove nested response_type to avoid duplication with top-level
                c.pop("response_type", None)
            except Exception:
                pass
            return c
        
        # Keep content unchanged for non-structured final answers (including support cases),
        # but drop nested response_type to avoid duplication with the top-level.
        try:
            c.pop("response_type", None)
        except Exception:
            pass
        return c

    # Image-IDs response: keep ux_response with product_ids and summary_message
    if bot_resp_type == ResponseType.IMAGE_IDS:
        ux = c.get("ux_response") if isinstance(c.get("ux_response"), dict) else {}
        out = {
            "summary_message": c.get("summary_message", ""),
            "ux_response": ux,
        }
        return out

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

        # Ensure minimal UX response for MPM when missing (safety net)
        try:
            if bot_resp_type == ResponseType.FINAL_ANSWER:
                intent_lower = str(normalized.get("product_intent", "")).strip().lower()
                is_mpm_intent = intent_lower in {"which_is_better", "show_me_options", "show_me_alternate"}
                has_ux = isinstance(normalized.get("ux_response"), dict)
                if is_mpm_intent and not has_ux:
                    # Build product_ids from content.products or fetched_data
                    product_ids: list[str] = []
                    try:
                        products = normalized.get("products") or []
                        if isinstance(products, list):
                            for p in products[:10]:
                                pid = (p or {}).get("id") or (p or {}).get("product_id")
                                if pid:
                                    product_ids.append(str(pid))
                    except Exception:
                        pass
                    if not product_ids:
                        try:
                            fetched_block = (ctx.fetched_data or {}).get("search_products") or {}
                            payload = fetched_block.get("data", fetched_block)
                            products = payload.get("products", []) if isinstance(payload, dict) else []
                            for p in products[:10]:
                                pid = p.get("id") or p.get("product_id") or f"prod_{hash(p.get('name','') or p.get('title',''))%1000000}"
                                if pid:
                                    product_ids.append(str(pid))
                        except Exception:
                            pass
                    if product_ids:
                        normalized["ux_response"] = {
                            "ux_surface": "MPM",
                            "product_ids": product_ids,
                            "quick_replies": []
                        }
                # Also ensure ux_response.product_ids is populated when ux exists but ids missing/empty
                if has_ux:
                    try:
                        ux = normalized.get("ux_response") or {}
                        ux_ids = ux.get("product_ids") if isinstance(ux.get("product_ids"), list) else []
                        if not ux_ids:
                            # Prefer existing normalized product_ids if present
                            from_content_ids = []
                            try:
                                for p in (normalized.get("products") or [])[:10]:
                                    pid = (p or {}).get("id") or (p or {}).get("product_id")
                                    if pid:
                                        from_content_ids.append(str(pid))
                            except Exception:
                                pass
                            if not from_content_ids:
                                fetched_block = (ctx.fetched_data or {}).get("search_products") or {}
                                payload = fetched_block.get("data", fetched_block)
                                products = payload.get("products", []) if isinstance(payload, dict) else []
                                for p in products[:10]:
                                    pid = p.get("id") or p.get("product_id") or f"prod_{hash(p.get('name','') or p.get('title',''))%1000000}"
                                    if pid:
                                        from_content_ids.append(str(pid))
                            if from_content_ids:
                                ux["product_ids"] = from_content_ids
                                normalized["ux_response"] = ux
                    except Exception:
                        pass
        except Exception:
            pass

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