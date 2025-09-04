# shopping_bot/routes/simplified_chat.py
"""
Simplified Chat Endpoint - Uses New Architecture Only
=====================================================

Uses only:
- bot_core.py (with 4-intent classification)
- ux_response_generator.py 
- fe_payload.build_envelope for response formatting
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Dict

from flask import Blueprint, Response, current_app, jsonify, request

from ..config import get_config
from ..enums import ResponseType
from ..fe_payload import build_envelope
from ..models import UserContext
from ..utils.smart_logger import get_smart_logger
from ..data_fetchers.es_products import get_es_fetcher  # type: ignore
from ..llm_service import LLMService  # type: ignore
from ..ux_response_generator import generate_ux_response_for_intent  # type: ignore

log = logging.getLogger(__name__)
smart_log = get_smart_logger("simplified_chat")
bp = Blueprint("chat", __name__)
Cfg = get_config()


def _elapsed_since(start_ts: float) -> float:
    """Calculate elapsed time since start timestamp."""
    loop = asyncio.get_event_loop()
    try:
        return loop.time() - start_ts
    except Exception:
        return 0.0


def _to_json_safe(obj: Any) -> Any:
    """Convert objects to JSON-safe format with error handling."""
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
    except Exception as e:
        log.warning(f"JSON_SAFE_CONVERSION_ERROR | obj_type={type(obj)} | error={e}")
        return str(obj)


# ─────────────────────────────────────────────────────────────
# Test endpoint
# ─────────────────────────────────────────────────────────────
@bp.get("/chat/test")
def chat_test():
    return {"test": "working", "architecture": "simplified", "features": ["4_intent_classification", "ux_generation"]}


# ─────────────────────────────────────────────────────────────
# Main chat endpoint
# ─────────────────────────────────────────────────────────────
@bp.post("/chat")
async def chat() -> Response:
    """
    Simplified chat endpoint using the new architecture.
    
    Flow:
    1. Parse and validate request
    2. Load user context  
    3. Process query with bot_core (includes 4-intent classification and UX generation)
    4. Build response envelope using fe_payload
    5. Return JSON response
    """
    request_start_time = asyncio.get_event_loop().time()

    try:
        # ─────────────────────────────────────────────────────────────
        # 1. Parse and validate request
        # ─────────────────────────────────────────────────────────────
        try:
            data: Dict[str, Any] = request.get_json(force=True)
            if not data:
                log.warning("CHAT_EMPTY_REQUEST | no JSON data received")
                return jsonify({"error": "No JSON data provided"}), 400
        except Exception as e:
            log.error(f"CHAT_JSON_PARSE_ERROR | error={e}")
            return jsonify({"error": "Invalid JSON format"}), 400

        # Validate required fields
        missing = [k for k in ("user_id", "message") if k not in data]
        if missing:
            log.warning(f"CHAT_MISSING_FIELDS | missing={missing}")
            return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

        user_id = str(data["user_id"])
        session_id = str(data.get("session_id", user_id))
        message = str(data["message"]).strip()
        wa_id = data.get("wa_id")
        channel = str(data.get("channel", "api")).lower()

        if not message:
            log.warning(f"CHAT_EMPTY_MESSAGE | user={user_id}")
            return jsonify({"error": "Message cannot be empty"}), 400

        log.info(
            f"SIMPLIFIED_CHAT_REQUEST | user={user_id} | session={session_id} | "
            f"channel={channel} | message='{message[:50]}...' | wa_id={wa_id}"
        )

        # ─────────────────────────────────────────────────────────────
        # 2. Get dependencies and load context
        # ─────────────────────────────────────────────────────────────
        try:
            ctx_mgr = current_app.extensions["ctx_mgr"]
            bot_core = current_app.extensions["bot_core"]
        except KeyError as e:
            log.error(f"CHAT_MISSING_EXTENSION | extension={e}")
            return jsonify({"error": f"Required service not available: {e}"}), 500

        if not bot_core:
            log.error("CHAT_NO_BOT_CORE | bot core not available")
            return jsonify({"error": "Bot core not initialized"}), 500

        # Load user context
        try:
            ctx = ctx_mgr.get_context(user_id, session_id)
            log.info(
                f"CONTEXT_LOADED | user={user_id} | session={session_id} | "
                f"has_assessment={bool(ctx.session.get('assessment'))}"
            )
        except Exception as e:
            log.error(f"CONTEXT_LOAD_ERROR | user={user_id} | session={session_id} | error={e}")
            return jsonify({"error": "Failed to load user context"}), 500

        # Inject CURRENT user text directly into ctx so downstream ES/LLM always see it
        try:
            setattr(ctx, "current_user_text", message)
            setattr(ctx, "message_text", message)
            ctx.session = ctx.session or {}
            ctx.session["current_user_text"] = message
            ctx.session["last_user_message"] = message
            ctx.session["last_query"] = message
            ctx.session.setdefault("debug", {})["current_user_text"] = message
            log.info(f"INGRESS_SET_CURRENT_TEXT | user={user_id} | text='{message[:80]}'")
        except Exception as _ing_exc:
            log.warning(f"INGRESS_SET_CURRENT_TEXT_FAILED | user={user_id} | error={_ing_exc}")

        # ─────────────────────────────────────────────────────────────
        # 3. Handle duplicate processing guard
        # ─────────────────────────────────────────────────────────────
        if "assessment" in ctx.session:
            current_phase = ctx.session["assessment"].get("phase")
            if current_phase == "processing":
                log.warning(f"DUPLICATE_PROCESSING_BLOCKED | user={user_id} | phase={current_phase}")
                elapsed_time = _elapsed_since(request_start_time)
                return jsonify({
                    "response_type": "processing",
                    "status": "already_processing",
                    "message": "Still working on your previous request. Please wait...",
                    "meta": {"elapsed_time": f"{elapsed_time:.3f}s"},
                }), 202

        # ─────────────────────────────────────────────────────────────
        # 4. Persist wa_id if provided
        # ─────────────────────────────────────────────────────────────
        if wa_id:
            try:
                ctx.session["wa_id"] = str(wa_id)
                user_bucket = ctx.session.get("user", {})
                user_bucket["wa_id"] = str(wa_id)
                ctx.session["user"] = user_bucket
                ctx_mgr.save_context(ctx)
                log.info(f"WA_ID_PERSISTED | user={user_id} | wa_id={wa_id}")
            except Exception as e:
                log.warning(f"WA_ID_PERSIST_FAILED | user={user_id} | wa_id={wa_id} | error={e}")

        # ─────────────────────────────────────────────────────────────
        # 5. Process query with bot core (includes 4-intent + UX generation)
        # ─────────────────────────────────────────────────────────────
        try:
            log.info(f"BOT_PROCESSING_START | user={user_id} | using simplified architecture")

            # NEW: Image selection pathway – user selected a product id from FE suggestions
            selected_product_id = str(data.get("selected_product_id") or "").strip()
            if selected_product_id:
                log.info(f"IMAGE_SELECTION_START | user={user_id} | selected_id={selected_product_id}")
                # Fetch full product doc via ES mget (sync API run in executor)
                fetcher = get_es_fetcher()
                loop = asyncio.get_running_loop()
                docs = await loop.run_in_executor(None, lambda: fetcher.mget_products([selected_product_id]))
                if not docs:
                    log.info("IMAGE_SELECTION_FALLBACK | mget returned 0 docs")
                    # Gracefully degrade: minimal response
                    content = {
                        "summary_message": "Couldn't fetch the selected product. Want me to show close matches?",
                        "ux_response": {
                            "ux_surface": "MPM",
                            "quick_replies": ["Show close matches", "Try another photo"],
                            "product_ids": []
                        },
                    }
                    envelope = build_envelope(
                        wa_id=wa_id,
                        session_id=session_id,
                        bot_resp_type=ResponseType.FINAL_ANSWER,
                        content=content,
                        ctx=ctx,
                        elapsed_time_seconds=_elapsed_since(request_start_time),
                        mode_async_enabled=getattr(Cfg, "ENABLE_ASYNC", False),
                        timestamp=None,
                        functions_executed=["image_selected_no_doc"],
                    )
                    smart_log.response_generated(user_id, envelope.get("response_type"), False, _elapsed_since(request_start_time))
                    return jsonify(envelope), 200

                doc = docs[0]
                try:
                    log.info(f"IMAGE_SELECTION_FETCH_OK | fields={list((doc or {}).keys())[:12]}")
                except Exception:
                    pass

                # Build a synthetic query and fetched block to reuse product flow
                latest_text = (
                    (ctx.session or {}).get("current_user_text")
                    or (ctx.session or {}).get("last_user_message")
                    or (ctx.session or {}).get("last_query")
                    or ""
                )
                brand = (doc.get("brand") or "").strip()
                name = (doc.get("name") or "").strip()
                if latest_text:
                    synthetic_query = f"is this good? {brand} {name}".strip()
                else:
                    synthetic_query = "is this good?"
                log.info(f"IMAGE_SELECTION_SYNTH_QUERY | text='{synthetic_query}'")

                # Inject fetched block shaped like search_products
                fetched = {
                    "search_products": {
                        "products": [doc],
                        "meta": {"total_hits": 1, "returned": 1}
                    }
                }

                # Call LLM response generator directly with SPM intent
                llm = LLMService()
                answer = await llm.generate_response(
                    synthetic_query,
                    ctx,
                    fetched,
                    intent_l3="Product_Discovery",
                    query_intent=None,
                    product_intent="is_this_good",
                )
                # Generate UX for SPM
                ux_answer = await generate_ux_response_for_intent(
                    intent="is_this_good",
                    previous_answer=answer,
                    ctx=ctx,
                    user_query=synthetic_query,
                )

                resp_type = ResponseType.FINAL_ANSWER
                envelope = build_envelope(
                    wa_id=wa_id,
                    session_id=session_id,
                    bot_resp_type=resp_type,
                    content=ux_answer,
                    ctx=ctx,
                    elapsed_time_seconds=_elapsed_since(request_start_time),
                    mode_async_enabled=getattr(Cfg, "ENABLE_ASYNC", False),
                    timestamp=None,
                    functions_executed=["image_selected_confirmed"],
                )
                log.info("IMAGE_SELECTION_GENERATED | surface=SPM")
                smart_log.response_generated(user_id, envelope.get("response_type"), False, _elapsed_since(request_start_time))
                return jsonify(envelope), 200

            # NEW: Image pathway — if image_url is present, run image flow and short-circuit
            image_url = str(data.get("image_url") or "").strip()
            if image_url:
                try:
                    ctx.session.setdefault("debug", {})["image_url"] = image_url
                except Exception:
                    pass
                # Run image flow to get top 3 product ids
                from ..vision_flow import process_image_query  # type: ignore
                log.info(f"IMAGE_FLOW_START | user={user_id} | url_present=true")
                image_result = await process_image_query(ctx, image_url)
                # Build minimal envelope content
                content = {
                    "summary_message": "Choose an option:",
                    "ux_response": {
                        "ux_surface": "MPM",
                        "quick_replies": ["Show healthier", "Cheaper", "More like this"],
                        "product_ids": image_result.get("product_ids", [])
                    },
                    "product_intent": "show_me_options",
                }
                envelope = build_envelope(
                    wa_id=wa_id,
                    session_id=session_id,
                    bot_resp_type=ResponseType.IMAGE_IDS,
                    content=content,
                    ctx=ctx,
                    elapsed_time_seconds=_elapsed_since(request_start_time),
                    mode_async_enabled=getattr(Cfg, "ENABLE_ASYNC", False),
                    timestamp=None,
                    functions_executed=["vision_image_match"],
                )
                smart_log.response_generated(user_id, envelope.get("response_type"), False, _elapsed_since(request_start_time))
                return jsonify(envelope), 200

            # Process text query using the updated bot core with 4-intent classification
            bot_resp = await bot_core.process_query(message, ctx)

            log.info(
                f"BOT_PROCESSING_COMPLETE | user={user_id} | response_type={bot_resp.response_type.value}"
            )
            
            # Log if UX response was generated
            if hasattr(bot_resp, 'content') and isinstance(bot_resp.content, dict):
                if bot_resp.content.get('ux_response'):
                    ux_intent = bot_resp.content.get('product_intent', 'unknown')
                    log.info(f"UX_RESPONSE_GENERATED | user={user_id} | intent={ux_intent}")

        except Exception as e:
            elapsed_time = _elapsed_since(request_start_time)
            log.error(f"BOT_PROCESSING_ERROR | user={user_id} | error={e}", exc_info=True)
            return jsonify({
                "wa_id": wa_id,
                "session_id": session_id,
                "response_type": "error",
                "content": {"message": "Bot processing failed"},
                "meta": {"elapsed_time": f"{elapsed_time:.3f}s"},
            }), 500

        # ─────────────────────────────────────────────────────────────
        # 6. Handle special response types
        # ─────────────────────────────────────────────────────────────
        
        # Handle processing stub (would need background processor for full functionality)
        if bot_resp.response_type == ResponseType.PROCESSING_STUB:
            elapsed_time = _elapsed_since(request_start_time)
            log.info(f"PROCESSING_STUB_FALLBACK | user={user_id} | elapsed_time={elapsed_time:.3f}s")
            
            return jsonify({
                "wa_id": wa_id,
                "session_id": session_id,
                "response_type": "processing",
                "content": {"message": "Processing your request... (simplified mode)"},
                "meta": {"elapsed_time": f"{elapsed_time:.3f}s"},
            }), 200

        # Handle CLI fallback for flow-only responses
        requires_flow = bool(getattr(bot_resp, "requires_flow", False))
        if requires_flow and channel in ["cli", "test"]:
            return await _handle_cli_fallback(bot_resp, ctx, user_id, channel)

        # ─────────────────────────────────────────────────────────────
        # 7. Build response envelope and return
        # ─────────────────────────────────────────────────────────────
        try:
            elapsed_time = _elapsed_since(request_start_time)

            if not hasattr(bot_resp, "response_type"):
                log.error(f"INVALID_BOT_RESPONSE | user={user_id} | missing response_type")
                raise ValueError("Bot response missing response_type")

            # Use the existing fe_payload.build_envelope for response formatting
            envelope = build_envelope(
                wa_id=wa_id,
                session_id=session_id,
                bot_resp_type=bot_resp.response_type,
                content=bot_resp.content or {},
                ctx=ctx,
                elapsed_time_seconds=elapsed_time,
                mode_async_enabled=getattr(Cfg, "ENABLE_ASYNC", False),
                timestamp=getattr(bot_resp, "timestamp", None),
                functions_executed=getattr(bot_resp, "functions_executed", []),
            )

            # Log success with UX info if present
            ux_info = ""
            if isinstance(bot_resp.content, dict) and bot_resp.content.get('ux_response'):
                ux_surface = bot_resp.content['ux_response'].get('ux_surface', 'unknown')
                qr_count = len(bot_resp.content['ux_response'].get('quick_replies', []))
                ux_info = f" | ux_surface={ux_surface} | qr_count={qr_count}"
            elif isinstance(bot_resp.content, dict) and bot_resp.content.get('product_intent'):
                # Derive and log implied UX type from intent when no explicit UX payload is present
                try:
                    intent_lower = str(bot_resp.content.get('product_intent', '')).strip().lower()
                    implied = 'UX_SPM' if intent_lower == 'is_this_good' else (
                        'UX_MPM' if intent_lower in {'which_is_better', 'show_me_options', 'show_me_alternate'} else 'unknown'
                    )
                    if implied != 'unknown':
                        ux_info = f" | ux_type={implied}"
                except Exception:
                    pass

            smart_log.response_generated(
                user_id, envelope.get("response_type"), False, elapsed_time
            )

            log.info(
                f"SIMPLIFIED_CHAT_SUCCESS | user={user_id} | response_type={envelope.get('response_type')} | "
                f"elapsed_time={elapsed_time:.3f}s{ux_info}"
            )
            
            return jsonify(envelope), 200

        except Exception as e:
            elapsed_time = _elapsed_since(request_start_time)
            log.error(f"RESPONSE_BUILD_ERROR | user={user_id} | error={e}", exc_info=True)
            return jsonify({
                "wa_id": wa_id,
                "session_id": session_id,
                "response_type": "error",
                "content": {"message": "Response formatting failed"},
                "meta": {"elapsed_time": f"{elapsed_time:.3f}s"},
            }), 500

    except Exception as exc:
        elapsed_time = _elapsed_since(request_start_time)
        log.error(f"CHAT_ENDPOINT_ERROR | elapsed_time={elapsed_time:.3f}s | error={exc}", exc_info=True)
        return jsonify({
            "wa_id": None,
            "session_id": None,
            "response_type": "error",
            "content": {"message": "Internal server error"},
            "meta": {"elapsed_time": f"{elapsed_time:.3f}s"},
        }), 500


# ─────────────────────────────────────────────────────────────
# CLI fallback helper
# ─────────────────────────────────────────────────────────────
async def _handle_cli_fallback(bot_resp, ctx: UserContext, user_id: str, channel: str) -> Response:
    """Provide text fallback for CLI/test channels when Flow is produced."""
    try:
        log.info(f"CLI_FALLBACK | user={user_id} | channel={channel}")

        # Extract products from fetched data for fallback
        fetched_data = ctx.fetched_data or {}
        products = []
        for key in ["search_products", "SEARCH_PRODUCTS"]:
            if key in fetched_data:
                data = fetched_data[key].get("data", {})
                if isinstance(data, dict) and "products" in data:
                    products = data["products"][:5]
                    break

        if products:
            lines = ["Here are the top options I found:"]
            for i, product in enumerate(products, 1):
                title = product.get("title") or product.get("name") or "Product"
                price = product.get("price", "Price on request")
                lines.append(f"{i}. {title} - {price}")
            fallback_text = "\n".join(lines)
        else:
            # Check for UX response text or assessment questions
            fallback_text = "I can help you find products. Please provide more details."
            
            if hasattr(bot_resp, 'content') and isinstance(bot_resp.content, dict):
                # Try to get UX response text
                ux_response = bot_resp.content.get('ux_response', {})
                if ux_response and ux_response.get('dpl_runtime_text'):
                    fallback_text = ux_response['dpl_runtime_text']
                elif bot_resp.content.get('summary_message'):
                    fallback_text = bot_resp.content['summary_message']
                elif bot_resp.content.get('message'):
                    fallback_text = bot_resp.content['message']
            
            # Check for ongoing assessment
            assessment = ctx.session.get("assessment", {})
            currently_asking = assessment.get("currently_asking")
            if currently_asking:
                contextual_questions = ctx.session.get("contextual_questions", {})
                question_text = (
                    contextual_questions.get(currently_asking, {}).get("message", "")
                )
                if question_text:
                    fallback_text = question_text
                else:
                    slot_name = currently_asking.replace('ASK_', '').replace('_', ' ').lower()
                    fallback_text = f"I need to know your {slot_name}"

        cli_response = {
            "wa_id": ctx.session.get("wa_id"),
            "session_id": ctx.session_id,
            "response_type": "final_answer",
            "content": {"message": fallback_text},
            "meta": {
                "functions_executed": getattr(bot_resp, "functions_executed", []),
                "cli_fallback": True,
                "original_response_type": getattr(bot_resp, "response_type", ResponseType.FINAL_ANSWER).value,
            },
        }

        log.info(f"CLI_FALLBACK_SUCCESS | user={user_id} | text_length={len(fallback_text)}")
        return jsonify(cli_response), 200

    except Exception as e:
        log.error(f"CLI_FALLBACK_ERROR | user={user_id} | error={e}", exc_info=True)
        return jsonify({
            "wa_id": ctx.session.get("wa_id") if ctx else None,
            "session_id": ctx.session_id if ctx else "unknown",
            "response_type": "final_answer",
            "content": {
                "message": "I can help you with shopping queries. Please provide more details."
            },
            "meta": {"cli_fallback": True, "fallback_error": str(e)},
        }), 200


# ─────────────────────────────────────────────────────────────
# Health and debug endpoints
# ─────────────────────────────────────────────────────────────
@bp.get("/chat/health")
def chat_health() -> Response:
    """Health check for chat service."""
    try:
        ctx_mgr = current_app.extensions.get("ctx_mgr")
        bot_core = current_app.extensions.get("bot_core")

        health_status = {
            "status": "healthy",
            "architecture": "simplified",
            "services": {
                "ctx_mgr": bool(ctx_mgr),
                "bot_core": bool(bot_core),
            },
            "features": {
                "4_intent_classification": True,
                "ux_generation": True,
                "product_search": True,
                "background_processing": False  # Not available in simplified mode
            }
        }

        if not (ctx_mgr and bot_core):
            health_status["status"] = "degraded"
            health_status["issues"] = []
            if not ctx_mgr:
                health_status["issues"].append("Redis context manager unavailable")
            if not bot_core:
                health_status["issues"].append("Bot core unavailable")

        return jsonify(health_status), 200

    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500


@bp.get("/chat/debug/<user_id>")
def debug_user_context(user_id: str) -> Response:
    """Get user context debug information."""
    try:
        ctx_mgr = current_app.extensions.get("ctx_mgr")
        if not ctx_mgr:
            return jsonify({"error": "Context manager not available"}), 500

        ctx = ctx_mgr.get_context(user_id, user_id)

        debug_info = {
            "user_id": user_id,
            "session_id": ctx.session_id,
            "architecture": "simplified",
            "context": {
                "session_keys": list(ctx.session.keys()),
                "permanent_keys": list(ctx.permanent.keys()),
                "fetched_data_keys": list(ctx.fetched_data.keys()),
            },
            "assessment": {
                "has_assessment": bool(ctx.session.get("assessment")),
                "assessment_phase": ctx.session.get("assessment", {}).get("phase"),
                "intent_l3": ctx.session.get("intent_l3"),
                "product_intent": ctx.session.get("product_intent"),
                "is_product_related": ctx.session.get("is_product_related"),
            },
            "features": {
                "4_intent_classification": True,
                "ux_generation": True,
            }
        }

        return jsonify(debug_info), 200

    except Exception as e:
        return jsonify({"error": str(e), "user_id": user_id}), 500


# ─────────────────────────────────────────────────────────────
# UX System testing endpoints
# ─────────────────────────────────────────────────────────────
@bp.post("/chat/test_ux")
async def test_ux_system() -> Response:
    """Test the UX generation system with a sample query."""
    try:
        data = request.get_json() or {}
        test_query = data.get("query", "show me some protein bars")
        user_id = data.get("user_id", "test_user")
        
        # Get dependencies
        ctx_mgr = current_app.extensions.get("ctx_mgr")
        bot_core = current_app.extensions.get("bot_core")
        
        if not (ctx_mgr and bot_core):
            return jsonify({"error": "Required services not available"}), 500
        
        # Load context
        ctx = ctx_mgr.get_context(user_id, user_id)
        
        # Process query
        bot_resp = await bot_core.process_query(test_query, ctx)
        
        # Extract UX information
        ux_info = {}
        if hasattr(bot_resp, 'content') and isinstance(bot_resp.content, dict):
            ux_response = bot_resp.content.get('ux_response')
            if ux_response:
                ux_info = {
                    "ux_generated": True,
                    "dpl_runtime_text": ux_response.get('dpl_runtime_text'),
                    "ux_surface": ux_response.get('ux_surface'),
                    "quick_replies": ux_response.get('quick_replies'),
                    "product_ids": ux_response.get('product_ids', []),
                }
            
            if bot_resp.content.get('product_intent'):
                ux_info["product_intent"] = bot_resp.content['product_intent']

            # Inject ux_type for validation
            try:
                if isinstance(ux_response, dict) and ux_response.get('ux_surface'):
                    surf = str(ux_response.get('ux_surface', '')).upper()
                    ux_info['ux_type'] = 'UX_SPM' if surf == 'SPM' else ('UX_MPM' if surf == 'MPM' else None)
                if not ux_info.get('ux_type') and isinstance(bot_resp.content.get('product_intent'), str):
                    intent_lower = bot_resp.content['product_intent'].strip().lower()
                    if intent_lower == 'is_this_good':
                        ux_info['ux_type'] = 'UX_SPM'
                    elif intent_lower in {'which_is_better', 'show_me_options', 'show_me_alternate'}:
                        ux_info['ux_type'] = 'UX_MPM'
            except Exception:
                pass
        
        test_result = {
            "test_query": test_query,
            "user_id": user_id,
            "response_type": bot_resp.response_type.value,
            "ux_system": ux_info or {"ux_generated": False},
            "intent_classification": {
                "intent_l3": ctx.session.get("intent_l3"),
                "is_product_related": ctx.session.get("is_product_related"),
                "product_intent": ctx.session.get("product_intent"),
            }
        }
        
        return jsonify(test_result), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500