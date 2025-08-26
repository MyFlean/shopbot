"""
Fixed Chat Endpoint - ADDRESSES CRITICAL ISSUES
"""
from __future__ import annotations

import logging
import asyncio
from typing import Any, Dict
from dataclasses import asdict, is_dataclass
from enum import Enum

from flask import Blueprint, current_app, jsonify, request, Response
from shopping_bot.config import get_config
from shopping_bot.enums import ResponseType
from shopping_bot.models import UserContext
from shopping_bot.utils.smart_logger import get_smart_logger
from shopping_bot.fe_payload import build_envelope

log = logging.getLogger(__name__)
smart_log = get_smart_logger("chat_routes")
bp = Blueprint("chat", __name__)
Cfg = get_config()


def _elapsed_since(start_ts: float) -> float:
    loop = asyncio.get_event_loop()
    try:
        return loop.time() - start_ts
    except Exception:
        return 0.0


# ─────────────────────────────────────────────────────────────
# Utilities: make any object JSON-safe (dataclasses, Enums, etc.)
# ─────────────────────────────────────────────────────────────
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
# Health ping
# ─────────────────────────────────────────────────────────────
@bp.get("/chat/test")
def chat_test():
    return {"test": "working"}


# ─────────────────────────────────────────────────────────────
# POST /chat - FIXED VERSION with comprehensive error handling
# ─────────────────────────────────────────────────────────────
@bp.post("/chat")
async def chat() -> Response:
    """
    Fixed chat endpoint with proper async handling and comprehensive logging.
    """
    request_start_time = asyncio.get_event_loop().time()

    try:
        # Parse JSON
        try:
            data: Dict[str, Any] = request.get_json(force=True)
            if not data:
                log.warning("CHAT_EMPTY_REQUEST | no JSON data received")
                return jsonify({"error": "No JSON data provided"}), 400
        except Exception as e:
            log.error(f"CHAT_JSON_PARSE_ERROR | error={e}")
            return jsonify({"error": "Invalid JSON format"}), 400

        # Validate input
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
            f"CHAT_REQUEST | user={user_id} | session={session_id} | channel={channel} | message='{message[:50]}...' | wa_id={wa_id}"
        )

        # Resolve deps
        try:
            ctx_mgr = current_app.extensions["ctx_mgr"]
            background_processor = current_app.extensions.get("background_processor")
            enhanced_bot = current_app.extensions.get("enhanced_bot_core")
            base_bot = current_app.extensions.get("bot_core")
        except KeyError as e:
            log.error(f"CHAT_MISSING_EXTENSION | extension={e}")
            return jsonify({"error": f"Required service not available: {e}"}), 500

        if not (enhanced_bot or base_bot):
            log.error("CHAT_NO_BOT_CORE | neither enhanced nor base bot available")
            return jsonify({"error": "Bot core not initialized"}), 500

        # Load context
        try:
            ctx = ctx_mgr.get_context(user_id, session_id)
            log.info(
                f"CONTEXT_LOADED | user={user_id} | session={session_id} | has_assessment={bool(ctx.session.get('assessment'))}"
            )
        except Exception as e:
            log.error(
                f"CONTEXT_LOAD_ERROR | user={user_id} | session={session_id} | error={e}"
            )
            return jsonify({"error": "Failed to load user context"}), 500

        # Duplicate guard
        if "assessment" in ctx.session:
            current_phase = ctx.session["assessment"].get("phase")
            if current_phase == "processing":
                log.warning(
                    f"DUPLICATE_PROCESSING_BLOCKED | user={user_id} | phase={current_phase}"
                )
                elapsed_time = _elapsed_since(request_start_time)
                return (
                    jsonify(
                        {
                            "response_type": "processing",
                            "status": "already_processing",
                            "message": "Still working on your previous request. Please wait...",
                            "suppress_user_channel": True,
                            "meta": {"elapsed_time": f"{elapsed_time:.3f}s"},
                        }
                    ),
                    202,
                )

        # Persist wa_id (best-effort)
        if wa_id:
            try:
                ctx.session["wa_id"] = str(wa_id)
                user_bucket = ctx.session.get("user", {})
                user_bucket["wa_id"] = str(wa_id)
                ctx.session["user"] = user_bucket
                ctx_mgr.save_context(ctx)
                log.info(f"WA_ID_PERSISTED | user={user_id} | wa_id={wa_id}")
            except Exception as e:
                log.warning(
                    f"WA_ID_PERSIST_FAILED | user={user_id} | wa_id={wa_id} | error={e}"
                )

        # Call the core
        try:
            log.info(
                f"BOT_PROCESSING_START | user={user_id} | using_enhanced={bool(enhanced_bot)}"
            )

            if enhanced_bot:
                bot_resp = await enhanced_bot.process_query(
                    message, ctx, enable_flows=Cfg.ENABLE_ASYNC
                )
            else:
                bot_resp = await base_bot.process_query(message, ctx)

            log.info(
                f"BOT_PROCESSING_COMPLETE | user={user_id} | response_type={bot_resp.response_type.value}"
            )
            log.debug(
                f"BOT_RESPONSE_DEBUG | user={user_id} | content_type={type(bot_resp.content)} | keys={list(bot_resp.content.keys()) if isinstance(bot_resp.content, dict) else 'n/a'}"
            )

        except Exception as e:
            elapsed_time = _elapsed_since(request_start_time)
            log.error(f"BOT_PROCESSING_ERROR | user={user_id} | error={e}", exc_info=True)
            return (
                jsonify(
                    {
                        "wa_id": wa_id,
                        "session_id": session_id,
                        "response_type": "error",
                        "content": {"message": "Bot processing failed"},
                        "meta": {"elapsed_time": f"{elapsed_time:.3f}s"},
                    }
                ),
                500,
            )

        # PROCESSING_STUB handling
        if bot_resp.response_type == ResponseType.PROCESSING_STUB:
            if not Cfg.ENABLE_ASYNC:
                smart_log.background_decision(user_id, "FORCE_SYNC", "ENABLE_ASYNC=false")
                try:
                    original_q = ctx.session.get("assessment", {}).get(
                        "original_query", message
                    )
                    log.info(
                        f"FORCE_SYNC_MODE | user={user_id} | original_query='{original_q[:50]}...'"
                    )
                    processing_id = await background_processor.process_query_background(
                        query=original_q,
                        user_id=user_id,
                        session_id=session_id,
                        wa_id=wa_id,
                        notification_callback=None,
                        inline=True,
                    )
                    elapsed_time = _elapsed_since(request_start_time)
                    log.info(
                        f"FORCE_SYNC_DONE | user={user_id} | processing_id={processing_id} | elapsed_time={elapsed_time:.3f}s"
                    )
                    return (
                        jsonify(
                            {
                                "wa_id": wa_id,
                                "session_id": session_id,
                                "response_type": "processing",
                                "content": {"message": "Processing completed"},
                                "meta": {
                                    "elapsed_time": f"{elapsed_time:.3f}s",
                                    "processing_id": processing_id,
                                },
                            }
                        ),
                        200,
                    )
                except Exception as e:
                    elapsed_time = _elapsed_since(request_start_time)
                    log.error(
                        f"FORCE_SYNC_FAILED | user={user_id} | error={e}", exc_info=True
                    )
                    return (
                        jsonify(
                            {
                                "wa_id": wa_id,
                                "session_id": session_id,
                                "response_type": "error",
                                "content": {"message": "Synchronous completion failed"},
                                "meta": {"elapsed_time": f"{elapsed_time:.3f}s"},
                            }
                        ),
                        500,
                    )

            if not background_processor:
                elapsed_time = _elapsed_since(request_start_time)
                log.error(f"BACKGROUND_UNAVAILABLE | user={user_id}")
                return (
                    jsonify(
                        {
                            "wa_id": wa_id,
                            "session_id": session_id,
                            "response_type": "error",
                            "content": {"message": "Background processor unavailable"},
                            "meta": {"elapsed_time": f"{elapsed_time:.3f}s"},
                        }
                    ),
                    503,
                )

            try:
                original_q = ctx.session.get("assessment", {}).get(
                    "original_query", message
                )
                log.info(
                    f"BACKGROUND_INLINE_START | user={user_id} | original_query='{original_q[:50]}...'"
                )

                processing_id = await background_processor.process_query_background(
                    query=original_q,
                    user_id=user_id,
                    session_id=session_id,
                    wa_id=wa_id,
                    notification_callback=None,
                    inline=True,
                )

                elapsed_time = _elapsed_since(request_start_time)
                log.info(
                    f"BACKGROUND_INLINE_DONE | user={user_id} | processing_id={processing_id} | elapsed_time={elapsed_time:.3f}s"
                )

                return (
                    jsonify(
                        {
                            "wa_id": wa_id,
                            "session_id": session_id,
                            "response_type": "processing",
                            "content": {"message": "Processing completed"},
                            "meta": {
                                "elapsed_time": f"{elapsed_time:.3f}s",
                                "processing_id": processing_id,
                            },
                        }
                    ),
                    200,
                )

            except Exception as e:
                elapsed_time = _elapsed_since(request_start_time)
                log.error(
                    f"BACKGROUND_SPAWN_ERROR | user={user_id} | error={e}",
                    exc_info=True,
                )
                return (
                    jsonify(
                        {
                            "wa_id": wa_id,
                            "session_id": session_id,
                            "response_type": "error",
                            "content": {"message": "Failed to start background processing"},
                            "meta": {"elapsed_time": f"{elapsed_time:.3f}s"},
                        }
                    ),
                    500,
                )

        # Flow-only gating
        requires_flow = bool(getattr(bot_resp, "requires_flow", False))
        if requires_flow:
            log.warning(f"UNEXPECTED_FLOW_SYNC | user={user_id} | suppressing text")

            elapsed_time = _elapsed_since(request_start_time)

            if channel in ["cli", "test"]:
                return await _handle_cli_fallback(bot_resp, ctx, user_id, channel)

            return (
                jsonify(
                    {
                        "wa_id": wa_id,
                        "session_id": session_id,
                        "response_type": "processing",
                        "content": {"message": "Content available via interactive elements"},
                        "meta": {"elapsed_time": f"{elapsed_time:.3f}s"},
                    }
                ),
                200,
            )

        # Standard sync path → FE envelope
        try:
            elapsed_time = _elapsed_since(request_start_time)

            if not hasattr(bot_resp, "response_type"):
                log.error(
                    f"INVALID_BOT_RESPONSE | user={user_id} | missing response_type | bot_resp={type(bot_resp)}"
                )
                raise ValueError("Bot response missing response_type")

            bot_content = bot_resp.content or {}

            envelope = build_envelope(
                wa_id=wa_id,
                session_id=session_id,
                bot_resp_type=bot_resp.response_type,
                content=bot_content,
                ctx=ctx,
                elapsed_time_seconds=elapsed_time,
                mode_async_enabled=getattr(Cfg, "ENABLE_ASYNC", False),
                timestamp=getattr(bot_resp, "timestamp", None),
                functions_executed=getattr(bot_resp, "functions_executed", []),
            )

            smart_log.response_generated(
                user_id, envelope.get("response_type"), False, elapsed_time
            )

            log.info(
                f"CHAT_SYNC_RESPONSE | user={user_id} | ui_type={envelope.get('response_type')} | elapsed_time={elapsed_time:.3f}s"
            )
            return jsonify(envelope), 200

        except Exception as e:
            elapsed_time = _elapsed_since(request_start_time)
            log.error(
                f"RESPONSE_SERIALIZATION_ERROR | user={user_id} | error={e}", exc_info=True
            )
            return (
                jsonify(
                    {
                        "wa_id": wa_id,
                        "session_id": session_id,
                        "response_type": "error",
                        "content": {"message": "Response serialization failed"},
                        "meta": {"elapsed_time": f"{elapsed_time:.3f}s"},
                    }
                ),
                500,
            )

    except Exception as exc:
        elapsed_time = _elapsed_since(request_start_time)
        log.error(
            f"CHAT_ENDPOINT_ERROR | elapsed_time={elapsed_time:.3f}s | error={exc}",
            exc_info=True,
        )
        return (
            jsonify(
                {
                    "wa_id": None,
                    "session_id": None,
                    "response_type": "error",
                    "content": {"message": "Internal server error"},
                    "meta": {"elapsed_time": f"{elapsed_time:.3f}s"},
                }
            ),
            500,
        )


# ─────────────────────────────────────────────────────────────
# CLI text fallback for flow_only responses
# ─────────────────────────────────────────────────────────────
async def _handle_cli_fallback(bot_resp, ctx: UserContext, user_id: str, channel: str) -> Response:
    """
    Provide text fallback for CLI/test channels when Flow is produced.
    """
    try:
        log.info(f"CLI_FALLBACK | user={user_id} | channel={channel}")

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
            log.info(
                f"CLI_PRODUCTS_FALLBACK | user={user_id} | products_count={len(products)}"
            )
        else:
            assessment = ctx.session.get("assessment", {})
            currently_asking = assessment.get("currently_asking")
            if currently_asking:
                contextual_questions = ctx.session.get("contextual_questions", {})
                question_text = (
                    contextual_questions.get(currently_asking, {}).get("message", "")
                )
                fallback_text = question_text or f"I need: {currently_asking.replace('ASK_', '').replace('_', ' ').lower()}"
            else:
                content = getattr(bot_resp, "content", {})
                message = (
                    content.get("message", "")
                    if isinstance(content, dict)
                    else str(content)
                )
                fallback_text = message or "I can help you find products. Please provide more details."

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

        log.info(
            f"CLI_FALLBACK_SUCCESS | user={user_id} | text_length={len(fallback_text)}"
        )
        return jsonify(cli_response), 200

    except Exception as e:
        log.error(f"CLI_FALLBACK_ERROR | user={user_id} | error={e}", exc_info=True)
        return jsonify(
            {
                "wa_id": ctx.session.get("wa_id") if ctx else None,
                "session_id": ctx.session_id if ctx else "unknown",
                "response_type": "final_answer",
                "content": {
                    "message": "I can help you with shopping queries. Please provide more details."
                },
                "meta": {"cli_fallback": True, "fallback_error": str(e)},
            }
        ), 200


# ─────────────────────────────────────────────────────────────
# Background processing polling endpoints
# ─────────────────────────────────────────────────────────────
@bp.get("/chat/processing/<processing_id>/status")
async def get_processing_status(processing_id: str) -> Response:
    try:
        log.info(f"STATUS_LOOKUP | processing_id={processing_id}")

        background_processor = current_app.extensions.get("background_processor")
        if not background_processor:
            log.error(f"STATUS_NO_PROCESSOR | processing_id={processing_id}")
            return jsonify({"error": "Background processor not available"}), 500

        status = await background_processor.get_processing_status(processing_id)

        if not status or status.get("status") == "not_found":
            log.warning(f"STATUS_NOT_FOUND | processing_id={processing_id}")
            return jsonify({"error": "Processing ID not found"}), 404

        log.info(
            f"STATUS_FOUND | processing_id={processing_id} | status={status.get('status')}"
        )
        return jsonify(_to_json_safe(status)), 200

    except Exception as exc:
        log.error(
            f"STATUS_LOOKUP_ERROR | processing_id={processing_id} | error={exc}",
            exc_info=True,
        )
        return jsonify({"error": str(exc)}), 500


@bp.get("/chat/processing/<processing_id>/result")
async def get_processing_result(processing_id: str) -> Response:
    try:
        log.info(f"RESULT_LOOKUP | processing_id={processing_id}")

        background_processor = current_app.extensions.get("background_processor")
        if not background_processor:
            log.error(f"RESULT_NO_PROCESSOR | processing_id={processing_id}")
            return jsonify({"error": "Background processor not available"}), 500

        result = await background_processor.get_processing_result(processing_id)

        if not result:
            log.warning(f"RESULT_NOT_FOUND | processing_id={processing_id}")
            return jsonify({"error": "Processing result not found"}), 404

        flow_data = result.get("flow_data", {})
        products_count = len(flow_data.get("products", [])) if flow_data else 0
        text_length = len(result.get("text_content", ""))

        log.info(
            f"RESULT_FOUND | processing_id={processing_id} | products_count={products_count} | text_length={text_length}"
        )

        return jsonify(_to_json_safe(result)), 200

    except Exception as exc:
        log.error(
            f"RESULT_LOOKUP_ERROR | processing_id={processing_id} | error={exc}",
            exc_info=True,
        )
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────
# Health and debug
# ─────────────────────────────────────────────────────────────
@bp.get("/chat/health")
def chat_health() -> Response:
    try:
        ctx_mgr = current_app.extensions.get("ctx_mgr")
        background_processor = current_app.extensions.get("background_processor")
        enhanced_bot = current_app.extensions.get("enhanced_bot_core")
        base_bot = current_app.extensions.get("bot_core")

        health_status = {
            "status": "healthy",
            "services": {
                "ctx_mgr": bool(ctx_mgr),
                "background_processor": bool(background_processor),
                "enhanced_bot": bool(enhanced_bot),
                "base_bot": bool(base_bot),
            },
        }

        if not (enhanced_bot or base_bot) or not ctx_mgr:
            health_status["status"] = "degraded"

        return jsonify(health_status), 200

    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500


@bp.get("/chat/debug/<user_id>")
def debug_user_context(user_id: str) -> Response:
    try:
        ctx_mgr = current_app.extensions.get("ctx_mgr")
        if not ctx_mgr:
            return jsonify({"error": "Context manager not available"}), 500

        ctx = ctx_mgr.get_context(user_id, user_id)

        debug_info = {
            "user_id": user_id,
            "session_id": ctx.session_id,
            "session_keys": list(ctx.session.keys()),
            "permanent_keys": list(ctx.permanent.keys()),
            "fetched_data_keys": list(ctx.fetched_data.keys()),
            "has_assessment": bool(ctx.session.get("assessment")),
            "assessment_phase": ctx.session.get("assessment", {}).get("phase"),
            "needs_background": ctx.session.get("needs_background"),
            "intent_l3": ctx.session.get("intent_l3"),
        }

        return jsonify(debug_info), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
