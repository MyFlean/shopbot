from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime
from typing import AsyncGenerator, Dict, Any

from flask import Blueprint, Response, current_app, request, stream_with_context

from ..config import get_config
from ..fe_payload import build_envelope
from ..utils.helpers import safe_get
from ..llm_service import LLMService  # type: ignore
from ..enums import ResponseType

log = logging.getLogger(__name__)

bp = Blueprint("chat_stream", __name__)


def _sse_event(event: str, data: Dict[str, Any]) -> bytes:
    payload = json.dumps(data, ensure_ascii=False)
    return (f"event: {event}\ndata: {payload}\n\n").encode("utf-8")


def _heartbeat_event() -> str:
    return _sse_event("heartbeat", {"ts": datetime.utcnow().isoformat() + "Z"})


@bp.post("/chat/stream")
def chat_stream() -> Response:
    cfg = get_config()
    if not getattr(cfg, "ENABLE_STREAMING", False):
        return Response(
            json.dumps({"error": "Streaming disabled", "hint": "Set ENABLE_STREAMING=true"}),
            status=400,
            mimetype="application/json",
        )

    def generate():
        request_id = str(uuid.uuid4())
        start_ts = time.time()

        try:
            data = request.get_json(silent=True) or {}
            user_id = str(data.get("user_id") or "").strip() or "anonymous"
            session_id = str(data.get("session_id") or user_id)
            message = str(data.get("message") or "").strip()
            wa_id = data.get("wa_id")
            channel = str(data.get("channel") or "web").lower()

            evt = _sse_event("ack", {"request_id": request_id, "session_id": session_id, "ts": datetime.utcnow().isoformat() + "Z"})
            log.info(f"SSE_EMIT | event=ack | session={session_id}")
            yield evt

            # Access shared components from app.extensions
            ctx_mgr = current_app.extensions.get("ctx_mgr")
            bot_core = current_app.extensions.get("bot_core")
            if not ctx_mgr or not bot_core:
                yield _sse_event("error", {"message": "Server not initialized"})
                yield _sse_event("end", {"ok": False})
                return

            ctx = ctx_mgr.get_context(user_id, session_id)

            # Persist WA id if provided (best-effort)
            try:
                if wa_id:
                    ctx.session["wa_id"] = wa_id
                    ctx_mgr.save_context(ctx)
            except Exception:
                pass

            # Emit early status
            log.info(f"SSE_EMIT | event=status | stage=classification | session={session_id}")
            yield _sse_event("status", {"stage": "classification"})

            # Quick classification FIRST to detect simple vs product queries
            # Using STREAMING version to emit incremental ASK messages and simple responses
            llm_service = LLMService()
            
            # Streaming classification with delta emission
            classification_deltas = []
            async def stream_wrapper():
                """Wrapper to collect deltas from streaming classification"""
                async def collect_callback(event_dict):
                    event_name = event_dict.get("event", "delta")
                    event_data = event_dict.get("data", {})
                    log.info(f"SSE_EMIT | event={event_name} | session={session_id} | data_keys={list(event_data.keys())}")
                    classification_deltas.append(_sse_event(event_name, event_data))
                
                result = await llm_service.classify_and_assess_stream(message, ctx, emit_callback=collect_callback)
                return result
            
            classification = asyncio.run(stream_wrapper())
            
            # Yield all collected deltas
            for delta in classification_deltas:
                yield delta
            
            route = classification.get("route", "general")
            data_strategy = classification.get("data_strategy", "none")
            
            log.info(f"SSE_CLASSIFY | route={route} | data_strategy={data_strategy} | session={session_id}")
            
            # Save classification to context
            ctx.session["intent_l3"] = classification.get("layer3", "general")
            ctx.session["is_product_related"] = classification.get("is_product_related", False)
            
            # If it's a simple reply (no product data needed), the response was already streamed during classification
            if data_strategy == "none" and route == "general":
                log.info(f"SSE_STREAM_PATH | simple_reply_streamed_during_classification | session={session_id}")
                
                # Get the pre-generated simple response from classification (for final envelope)
                simple_resp = classification.get("simple_response", {})
                accumulated_text = simple_resp.get("message", "I'm here to help!")
                
                # Update context with conversation history
                ctx.session.setdefault("conversation_history", []).append({
                    "i": len(ctx.session.get("conversation_history", [])) + 1,
                    "user": message,
                    "bot": accumulated_text[:100]
                })
                ctx_mgr.save_context(ctx)
                
                # Build final envelope
                elapsed = time.time() - start_ts
                envelope = build_envelope(
                    wa_id=wa_id,
                    session_id=session_id,
                    bot_resp_type=ResponseType.FINAL_ANSWER,
                    content={"summary_message": accumulated_text},
                    ctx=ctx,
                    elapsed_time_seconds=elapsed,
                    mode_async_enabled=False,
                    timestamp=datetime.utcnow().isoformat() + "Z",
                    functions_executed=["classify_and_assess_stream"],
                )
                
                log.info(f"SSE_EMIT | event=final_answer.complete | session={session_id}")
                yield _sse_event("final_answer.complete", envelope)
                
                log.info(f"SSE_EMIT | event=end | ok=True | session={session_id}")
                yield _sse_event("end", {"ok": True})
                return
            
            # For product queries, run full pipeline (no streaming yet for product path)
            log.info(f"SSE_STANDARD_PATH | product_query | session={session_id}")
            yield _sse_event("status", {"stage": "product_search"})
            bot_resp = asyncio.run(bot_core.process_query(message, ctx))

            # If response is an MPM/UX surface with product IDs, send an early bootstrap
            try:
                content = getattr(bot_resp, "content", {}) or {}
                intent = (content.get("product_intent") or "").lower()
                ux = content.get("ux_response") or {}
                product_ids = ux.get("product_ids") or content.get("product_ids") or []
                if isinstance(product_ids, list) and product_ids:
                    log.info(f"SSE_EMIT | event=ux_bootstrap | ids={len(product_ids)} | session={session_id}")
                    yield _sse_event("ux_bootstrap", {"content": {"ux_response": {"ux_surface": ux.get("ux_surface", "MPM"), "product_ids": product_ids, "quick_replies": ux.get("quick_replies", [])}}})
            except Exception:
                pass

            # Complete with canonical envelope (preserves FE contract)
            elapsed = time.time() - start_ts
            envelope = build_envelope(
                wa_id=wa_id,
                session_id=session_id,
                bot_resp_type=bot_resp.response_type,
                content=bot_resp.content or {},
                ctx=ctx,
                elapsed_time_seconds=elapsed,
                mode_async_enabled=getattr(get_config(), "ENABLE_ASYNC", False),
                timestamp=getattr(bot_resp, "timestamp", None),
                functions_executed=getattr(bot_resp, "functions_executed", []),
            )
            log.info(f"SSE_EMIT | event=final_answer.complete | session={session_id}")
            yield _sse_event("final_answer.complete", envelope)

            log.info(f"SSE_EMIT | event=end | ok=True | session={session_id}")
            yield _sse_event("end", {"ok": True})

        except Exception as e:
            log.exception("STREAM_ERROR")
            log.error(f"SSE_EMIT | event=error | err={e} | session={session_id if 'session_id' in locals() else 'unknown'}")
            yield _sse_event("error", {"message": str(e)})
            yield _sse_event("end", {"ok": False})

    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
        "Access-Control-Allow-Origin": request.headers.get("Origin", "*"),
    }

    return Response(stream_with_context(generate()), headers=headers, mimetype="text/event-stream", direct_passthrough=True)
