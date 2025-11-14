from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime
from typing import AsyncGenerator, Dict, Any
import threading
import queue

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

            # Initialize LLM service for streaming
            llm_service = LLMService()

            ctx = ctx_mgr.get_context(user_id, session_id)

            # Persist WA id if provided (best-effort)
            try:
                if wa_id:
                    ctx.session["wa_id"] = wa_id
                    ctx_mgr.save_context(ctx)
            except Exception:
                pass

            # ============================================================
            # CHECK: Are we in the middle of an ASK phase?
            # ============================================================
            assessment = ctx.session.get("assessment", {})
            if assessment.get("phase") == "asking" and assessment.get("currently_asking"):
                # User is answering a question in the ASK flow
                log.info(f"ASK_ANSWER_DETECTED | user={user_id} | answering={assessment['currently_asking']}")
                
                from ..bot_helpers import store_user_answer
                
                # Store the user's answer
                currently_asking = assessment.get("currently_asking")
                store_user_answer(message, assessment, ctx)
                log.info(f"ASK_ANSWER_STORED | slot={currently_asking} | answer='{message[:50]}'")
                
                # Check what's still missing
                fulfilled = set(assessment.get("fulfilled", []))
                priority_order = assessment.get("priority_order", [])
                still_missing = [slot for slot in priority_order if slot not in fulfilled]
                
                log.info(f"ASK_STATUS | fulfilled={list(fulfilled)} | still_missing={still_missing}")
                
                if still_missing:
                    # More questions to ask - signal frontend to show next question
                    next_slot = still_missing[0]
                    assessment["currently_asking"] = next_slot
                    assessment["last_completed_slot"] = currently_asking
                    ctx_mgr.save_context(ctx)
                    
                    log.info(f"ASK_NEXT | showing={next_slot} | remaining={len(still_missing)}")
                    log.info(f"SSE_EMIT | event=ask_next | session={session_id}")
                    yield _sse_event("ask_next", {
                        "slot_name": next_slot,
                        "completed_slot": currently_asking,
                        "remaining_count": len(still_missing)
                    })
                    log.info(f"SSE_EMIT | event=end | ok=True | session={session_id}")
                    yield _sse_event("end", {"ok": True})
                    return
                else:
                    # All questions answered - proceed to ES fetch
                    log.info(f"ASK_COMPLETE | all_slots_filled | proceeding_to_search")
                    assessment["phase"] = "complete"
                    assessment["currently_asking"] = None
                    ctx_mgr.save_context(ctx)
                    
                    # Signal frontend: ASK phase done
                    log.info(f"SSE_EMIT | event=ask_complete | session={session_id}")
                    yield _sse_event("ask_complete", {"message": "Got it! Searching for products..."})
                    log.info(f"SSE_EMIT | event=status | stage=product_search | session={session_id}")
                    yield _sse_event("status", {"stage": "product_search"})
                    
                    # Now run product search with all collected information
                    # CRITICAL: Set ctx.current_user_text to the original query (not the last answer)
                    # so that ES parameter extraction uses the right query context
                    original_query = assessment.get("original_query", message)
                    try:
                        setattr(ctx, "current_user_text", original_query)
                        setattr(ctx, "message_text", original_query)
                        ctx.session["current_user_text"] = original_query
                        ctx.session["last_user_message"] = original_query
                        ctx.session.setdefault("debug", {})["current_user_text"] = original_query
                        log.info(f"ASK_COMPLETE_SET_ORIGINAL_QUERY | original_query='{original_query[:80]}'")
                    except Exception as ctx_exc:
                        log.warning(f"ASK_COMPLETE_SET_QUERY_FAILED | error={ctx_exc}")
                    
                    log.info(f"RUNNING_PRODUCT_SEARCH | with_collected_slots | streaming=True")
                    
                    # ============================================================
                    # STREAMING FINAL ANSWER: Run ES fetch + stream LLM3 response
                    # ============================================================
                    
                    # Set up streaming callback for final answer
                    final_answer_queue: "queue.Queue[tuple[str, Any]]" = queue.Queue()
                    
                    def start_product_search() -> None:
                        async def search_and_stream():
                            try:
                                # Define streaming callback for LLM3
                                async def final_answer_callback(event_dict):
                                    event_name = event_dict.get("event", "delta")
                                    event_data = event_dict.get("data", {})
                                    log.info(f"FINAL_ANSWER_SSE | event={event_name} | session={session_id}")
                                    final_answer_queue.put_nowait(("stream", {"event": event_name, "data": event_data}))
                                
                                # Run ES fetchers using registered handler
                                from ..data_fetchers import get_fetcher
                                from ..enums import BackendFunction

                                fetched = {}
                                try:
                                    log.info(f"ES_FETCH_START | user={user_id}")
                                    search_handler = get_fetcher(BackendFunction.SEARCH_PRODUCTS)
                                    search_result = await search_handler(ctx)
                                    fetched[BackendFunction.SEARCH_PRODUCTS.value] = search_result
                                    try:
                                        prod_count = len((search_result or {}).get('products', []) or [])
                                    except Exception:
                                        prod_count = 0
                                    log.info(f"ES_FETCH_COMPLETE | products={prod_count}")
                                except Exception as fetch_exc:
                                    log.error(f"ES_FETCH_FAILED | error={fetch_exc}")
                                    fetched[BackendFunction.SEARCH_PRODUCTS.value] = {"error": str(fetch_exc)}
                                
                                # Generate streaming response via LLM3
                                intent_l3 = ctx.session.get("intent_l3", "")
                                product_intent = ctx.session.get("product_intent", "show_me_options")
                                from ..enums import QueryIntent
                                query_intent = QueryIntent.RECOMMENDATION  # Default
                                
                                log.info(f"LLM3_STREAM_START | intent={product_intent} | session={session_id}")
                                answer_dict = await llm_service.generate_response(
                                    original_query,
                                    ctx,
                                    fetched,
                                    intent_l3=intent_l3,
                                    query_intent=query_intent,
                                    product_intent=product_intent,
                                    emit_callback=final_answer_callback
                                )
                                
                                # Pass both answer and fetched data back to main thread
                                final_answer_queue.put_nowait(("answer", {"answer_dict": answer_dict, "fetched": fetched}))
                                
                            except Exception as exc:
                                log.error(f"PRODUCT_SEARCH_STREAM_ERROR | error={exc}")
                                final_answer_queue.put_nowait(("error", exc))
                            finally:
                                final_answer_queue.put_nowait(("done", None))
                        
                        try:
                            asyncio.run(search_and_stream())
                        except Exception as exc:
                            final_answer_queue.put_nowait(("error", exc))
                            final_answer_queue.put_nowait(("done", None))
                    
                    threading.Thread(target=start_product_search, daemon=True).start()
                    
                    # Stream events to frontend
                    answer_dict = None
                    fetched = {}
                    search_failed = False
                    
                    while True:
                        kind, payload = final_answer_queue.get()
                        if kind == "stream":
                            event_name = payload.get("event")
                            event_data = payload.get("data", {})
                            yield _sse_event(event_name, event_data)
                        elif kind == "answer":
                            # Payload now contains both answer_dict and fetched
                            answer_dict = payload.get("answer_dict")
                            fetched = payload.get("fetched", {})
                        elif kind == "error":
                            search_failed = True
                            err_msg = str(payload)
                            log.error(f"PRODUCT_SEARCH_ERROR | {err_msg}")
                            yield _sse_event("error", {"message": err_msg})
                        elif kind == "done":
                            break
                    
                    if search_failed or not answer_dict:
                        yield _sse_event("end", {"ok": False})
                        return
                    
                    # ============================================================
                    # CRITICAL: Save conversation context (mirrors bot_core logic)
                    # Without this, follow-up queries fail because LLM1 has no memory
                    # ============================================================
                    try:
                        from ..bot_helpers import snapshot_and_trim
                        from ..enums import BackendFunction
                        
                        # Reconstruct fetched dict for _store_last_recommendation
                        # (it was built inside the async thread, need to pass it)
                        # We need to get it from the thread - let's add it to the queue
                        # For now, we'll extract product IDs from answer_dict and build minimal snapshot
                        
                        # Store last_recommendation (product memory for follow-ups)
                        bot_core._store_last_recommendation(original_query, ctx, fetched)
                        
                        # Save conversation history turn
                        final_answer_summary = {
                            "response_type": answer_dict.get("response_type", "final_answer"),
                            "message_preview": (answer_dict.get("summary_message") or "")[:300],
                            "has_products": True,
                            "ux_intent": ctx.session.get("product_intent"),
                            "message_full": answer_dict.get("summary_message", ""),
                            "data_source": "es_fetch"
                        }
                        snapshot_and_trim(ctx, base_query=original_query, final_answer=final_answer_summary)
                        
                        # Clean up assessment state
                        ctx.session.pop("assessment", None)
                        ctx.session.pop("contextual_questions", None)
                        
                        # Persist everything to Redis
                        ctx_mgr.save_context(ctx)
                        
                        log.info(f"STREAMING_CONTEXT_SAVED | query='{original_query[:60]}' | has_last_rec=True")
                    except Exception as save_exc:
                        log.error(f"STREAMING_CONTEXT_SAVE_FAILED | error={save_exc}")
                    
                    # Build final envelope
                    elapsed = time.time() - start_ts
                    from ..enums import ResponseType
                    resp_type = ResponseType(answer_dict.get("response_type", "final_answer"))
                    
                    envelope = build_envelope(
                        wa_id=wa_id,
                        session_id=session_id,
                        bot_resp_type=resp_type,
                        content=answer_dict,
                        ctx=ctx,
                        elapsed_time_seconds=elapsed,
                        mode_async_enabled=False,
                        timestamp=datetime.utcnow().isoformat() + "Z",
                        functions_executed=["search_products", "_generate_product_response_stream"],
                    )
                    
                    log.info(f"SSE_EMIT | event=final_answer.complete | session={session_id}")
                    yield _sse_event("final_answer.complete", envelope)
                    
                    log.info(f"SSE_EMIT | event=end | ok=True | session={session_id}")
                    yield _sse_event("end", {"ok": True})
                    return

            # ============================================================
            # NORMAL FLOW: Initial query or non-ASK continuation
            # ============================================================
            
            # Emit early status
            log.info(f"SSE_EMIT | event=status | stage=classification | session={session_id}")
            yield _sse_event("status", {"stage": "classification"})

            # Quick classification FIRST to detect simple vs product queries
            # Using STREAMING version to emit incremental ASK messages and simple responses
            llm_service = LLMService()

            event_queue: "queue.Queue[tuple[str, Any]]" = queue.Queue()

            def start_streaming() -> None:
                async def stream_wrapper():
                    async def collect_callback(event_dict):
                        event_name = event_dict.get("event", "delta")
                        event_data = event_dict.get("data", {})
                        log.info(f"SSE_EMIT | event={event_name} | session={session_id} | data_keys={list(event_data.keys())}")
                        event_queue.put_nowait(("stream", {"event": event_name, "data": event_data}))

                    result = await llm_service.classify_and_assess_stream(message, ctx, emit_callback=collect_callback)
                    event_queue.put_nowait(("classification", result))

                try:
                    asyncio.run(stream_wrapper())
                except Exception as exc:  # pragma: no cover - defensive
                    event_queue.put_nowait(("error", exc))
                finally:
                    event_queue.put_nowait(("done", None))

            threading.Thread(target=start_streaming, daemon=True).start()

            accumulated_text = ""
            classification: Dict[str, Any] = {}
            streaming_failed = False

            while True:
                kind, payload = event_queue.get()
                if kind == "stream":
                    event_name = payload.get("event")
                    event_data = payload.get("data", {})
                    if event_name == "final_answer.delta":
                        delta_text = event_data.get("delta") or ""
                        accumulated_text += delta_text
                    yield _sse_event(event_name, event_data)
                elif kind == "classification":
                    classification = payload or {}
                elif kind == "error":
                    streaming_failed = True
                    err_msg = str(payload)
                    log.error(f"SSE_STREAM_ERROR | {err_msg}")
                    yield _sse_event("error", {"message": err_msg})
                elif kind == "done":
                    break

            if streaming_failed:
                yield _sse_event("end", {"ok": False})
                return
            
            route = classification.get("route", "general")
            data_strategy = classification.get("data_strategy", "none")
            
            log.info(f"SSE_CLASSIFY | route={route} | data_strategy={data_strategy} | session={session_id}")
            
            # Save classification to context
            ctx.session["intent_l3"] = classification.get("layer3", "general")
            ctx.session["is_product_related"] = classification.get("is_product_related", False)
            
            # ============================================================
            # INITIALIZE ASSESSMENT STATE for product queries with ASK slots
            # ============================================================
            if route == "product" and classification.get("ask"):
                ask_dict = classification.get("ask", {})
                if ask_dict:
                    slot_names = list(ask_dict.keys())
                    log.info(f"ASK_INIT | initializing_assessment | slots={slot_names}")
                    
                    # Initialize assessment state (mirrors non-streaming path in bot_core.py)
                    assessment_state = {
                        "phase": "asking",
                        "original_query": message,
                        "missing_data": slot_names,
                        "priority_order": slot_names,
                        "currently_asking": slot_names[0] if slot_names else None,
                        "fulfilled": [],
                        "user_provided_slots": [],
                    }
                    ctx.session["assessment"] = assessment_state
                    ctx.session["contextual_questions"] = ask_dict
                    ctx.session["domain"] = classification.get("domain", "")
                    ctx.session["category"] = classification.get("category", "")
                    ctx.session["product_intent"] = classification.get("product_intent", "show_me_options")
                    ctx_mgr.save_context(ctx)
                    
                    log.info(f"ASK_STATE_SAVED | currently_asking={slot_names[0]} | total_slots={len(slot_names)}")
                    
                    # Emit a special event signaling ASK phase is active
                    # Frontend should now wait for user to answer questions
                    log.info(f"SSE_EMIT | event=ask_phase_start | session={session_id}")
                    yield _sse_event("ask_phase_start", {
                        "total_questions": len(slot_names),
                        "first_question": slot_names[0]
                    })
                    
                    # End stream here - wait for user to answer
                    log.info(f"SSE_EMIT | event=end | ok=True | waiting_for_answer | session={session_id}")
                    yield _sse_event("end", {"ok": True, "awaiting_user_input": True})
                    return
            
            # If it's a simple reply (no product data needed), the response was already streamed during classification
            if data_strategy == "none" and route == "general":
                log.info(f"SSE_STREAM_PATH | simple_reply_streamed_during_classification | session={session_id}")

                # Get the pre-generated simple response from classification (for final envelope)
                simple_resp = classification.get("simple_response", {})
                summary_text = accumulated_text or simple_resp.get("message", "I'm here to help!")

                # Update context with conversation history
                ctx.session.setdefault("conversation_history", []).append({
                    "i": len(ctx.session.get("conversation_history", [])) + 1,
                    "user": message,
                    "bot": summary_text[:100]
                })
                ctx_mgr.save_context(ctx)

                # Build final envelope
                elapsed = time.time() - start_ts
                envelope = build_envelope(
                    wa_id=wa_id,
                    session_id=session_id,
                    bot_resp_type=ResponseType.FINAL_ANSWER,
                    content={"summary_message": summary_text},
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
