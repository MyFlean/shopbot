"""
Background Processor - FIXED VERSION
====================================
Fixes:
1. Always write processing result BEFORE setting status to completed
2. Never emit processing status after completion  
3. Return immediately after spawning background work (no await)
4. Proper error handling with failed status
5. Enhanced logging for debugging
"""
from __future__ import annotations

import asyncio
import time
import random
from urllib.parse import urlparse
import logging
import os
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import aiohttp

from .redis_manager import RedisContextManager
from .config import get_config
from .enums import ResponseType

Cfg = get_config()
log = logging.getLogger(__name__)


class BackgroundProcessor:
    """Runs heavy work in the background and notifies FE with a minimal payload."""

    def __init__(self, enhanced_bot_core, ctx_mgr: RedisContextManager):
        self.enhanced_bot = enhanced_bot_core
        self.ctx_mgr = ctx_mgr
        self.processing_ttl = timedelta(hours=2)

    async def process_query_background(
        self,
        query: str,
        user_id: str,
        session_id: str,
        wa_id: Optional[str] = None,
        notification_callback: Optional[callable] = None,
        inline: bool = False,
    ) -> str:
        """
        Execute heavy work, persist the full result for polling,
        and ping FE with a minimal button-show payload.
        
        FIX: This method now properly handles the async workflow:
        1. Set initial processing status
        2. Execute work WITHOUT blocking the caller
        3. Always write result BEFORE setting completed status
        4. Handle errors properly with failed status
        """
        processing_id = f"bg_{user_id}_{session_id}_{int(datetime.now().timestamp())}"
        
        # FIX: Set initial status immediately
        await self._set_processing_status(processing_id, "processing", {
            "query": query,
            "user_id": user_id,
            "session_id": session_id,
            "wa_id": wa_id
        })
        log.info(f"BACKGROUND_START | processing_id={processing_id} | query='{query[:50]}...'")

        # Pragmatic option: execute inline (no 202 path). Useful to avoid lifecycle issues.
        if inline:
            log.info(f"BACKGROUND_INLINE_EXEC | processing_id={processing_id} | starting inline execution")
            await self._execute_background_work(
                processing_id, query, user_id, session_id, wa_id, notification_callback
            )
        else:
            # FIX: Spawn background work outside the request's task lifecycle to avoid cancellations
            # Toggle with env BACKGROUND_TASK_MODE=create_task|executor (default: executor)
            spawn_mode = os.getenv("BACKGROUND_TASK_MODE", "executor").lower()
            if spawn_mode == "create_task":
                log.info(f"BACKGROUND_SPAWN_MODE | processing_id={processing_id} | mode=create_task")
                asyncio.create_task(self._execute_background_work(
                    processing_id, query, user_id, session_id, wa_id, notification_callback
                ))
            else:
                log.info(f"BACKGROUND_SPAWN_MODE | processing_id={processing_id} | mode=executor")
                loop = asyncio.get_running_loop()

                def _runner() -> None:
                    try:
                        asyncio.run(self._execute_background_work(
                            processing_id, query, user_id, session_id, wa_id, notification_callback
                        ))
                    except Exception as e:  # noqa: BLE001
                        log.error(f"BACKGROUND_EXECUTOR_ERROR | processing_id={processing_id} | error={e}", exc_info=True)

                loop.run_in_executor(None, _runner)
        
        log.info(f"BACKGROUND_SPAWNED | processing_id={processing_id} | returning immediately")
        return processing_id

    async def _execute_background_work(
        self,
        processing_id: str,
        query: str,
        user_id: str,
        session_id: str,
        wa_id: Optional[str],
        notification_callback: Optional[callable],
    ) -> None:
        """
        FIX: Separate method for actual background execution.
        This ensures the original caller returns immediately while work continues.
        """
        ctx = None
        t0 = time.perf_counter()
        try:
            log.info(f"BACKGROUND_EXEC_START | processing_id={processing_id}")
            
            # Get context and mark assessment phase
            t_ctx0 = time.perf_counter()
            ctx = self.ctx_mgr.get_context(user_id, session_id)
            if "assessment" in ctx.session:
                ctx.session["assessment"]["phase"] = "processing"
                self.ctx_mgr.save_context(ctx)
                log.info(f"ASSESSMENT_PHASE_MARKED | processing_id={processing_id}")
            log.info(f"TIMING | processing_id={processing_id} | step=context_load_and_mark | duration_ms={(time.perf_counter()-t_ctx0)*1000:.1f}")

            # Execute the actual work
            t_core0 = time.perf_counter()
            log.info(f"CORE_PROCESSING_START | processing_id={processing_id}")
            result = await self.enhanced_bot.process_query(query, ctx, enable_flows=True)
            log.info(f"CORE_PROCESSING_COMPLETE | processing_id={processing_id} | response_type={result.response_type.value} | duration_ms={(time.perf_counter()-t_core0)*1000:.1f}")

            # FIX: If core still returned PROCESSING_STUB, force sync completion
            if getattr(result, "response_type", None) == ResponseType.PROCESSING_STUB:
                log.warning(f"CORE_STUB_IN_BACKGROUND | processing_id={processing_id} | forcing sync")
                ctx.session["needs_background"] = False
                
                # FIX: Mark assessment as done to prevent loops
                if "assessment" in ctx.session:
                    ctx.session["assessment"]["phase"] = "done"
                
                self.ctx_mgr.save_context(ctx)
                t_core_forced0 = time.perf_counter()
                result = await self.enhanced_bot.process_query(query, ctx, enable_flows=True)
                log.info(f"CORE_FORCED_SYNC | processing_id={processing_id} | new_response_type={result.response_type.value} | duration_ms={(time.perf_counter()-t_core_forced0)*1000:.1f}")

            # FIX: CRITICAL - Store result FIRST, then update status
            t_store0 = time.perf_counter()
            await self._store_processing_result(
                processing_id=processing_id,
                result=result,
                original_query=query,
                user_id=user_id,
                session_id=session_id,
            )
            log.info(f"RESULT_STORED | processing_id={processing_id} | duration_ms={(time.perf_counter()-t_store0)*1000:.1f}")

            # FIX: Mark assessment as complete and save context
            if "assessment" in ctx.session:
                ctx.session["assessment"]["phase"] = "done"
                ctx.session["assessment"]["completed_at"] = datetime.now().isoformat()
            
            # FIX: Mark session as not needing background work
            ctx.session["needs_background"] = False
            t_ctxsave0 = time.perf_counter()
            self.ctx_mgr.save_context(ctx)
            log.info(f"SESSION_UPDATED | processing_id={processing_id} | needs_background=false | duration_ms={(time.perf_counter()-t_ctxsave0)*1000:.1f}")

            # FIX: Only AFTER result is stored, set status to completed
            t_status0 = time.perf_counter()
            await self._set_processing_status(processing_id, "completed", {
                "result_available": True,
                "execution_time": "computed_if_needed"
            })
            log.info(f"STATUS_COMPLETED | processing_id={processing_id} | duration_ms={(time.perf_counter()-t_status0)*1000:.1f}")

            # Notify FE with minimal payload
            wa_id_resolved = self._resolve_wa_id(ctx, user_id) if wa_id is None else wa_id
            t_notify0 = time.perf_counter()
            await self._notify_fe_minimal(processing_id, user_id, wa_id_resolved, status="completed")
            log.info(f"FE_NOTIFIED | processing_id={processing_id} | wa_id={wa_id_resolved} | duration_ms={(time.perf_counter()-t_notify0)*1000:.1f}")

            log.info(f"TIMING | processing_id={processing_id} | step=total_background | duration_ms={(time.perf_counter()-t0)*1000:.1f}")

        except asyncio.CancelledError as e:
            # Explicitly log cancellations (some frameworks cancel child tasks on response end)
            log.error(f"BACKGROUND_EXEC_CANCELLED | processing_id={processing_id} | error={e}", exc_info=True)
            raise
        except Exception as e:
            # FIX: Proper error handling with failed status
            log.error(f"BACKGROUND_EXEC_FAILED | processing_id={processing_id} | error={str(e)}", exc_info=True)
            
            # Set status to failed with error details
            await self._set_processing_status(processing_id, "failed", {
                "error": str(e),
                "error_type": type(e).__name__,
                "failed_at": datetime.now().isoformat()
            })

            # Try to notify FE even on failure
            try:
                wa_id_resolved = self._resolve_wa_id(ctx, user_id) if wa_id is None else wa_id
                if not wa_id_resolved:
                    wa_id_resolved = os.getenv("FE_TEST_WA_ID", "917398580865")
                await self._notify_fe_minimal(processing_id, user_id, wa_id_resolved, status="failed")
                log.info(f"FE_NOTIFIED_FAILURE | processing_id={processing_id}")
            except Exception as notify_error:
                log.error(f"FE_NOTIFY_FAILED | processing_id={processing_id} | error={notify_error}")

    async def get_processing_status(self, processing_id: str) -> Dict[str, Any]:
        """Get processing status from Redis."""
        key = f"processing:{processing_id}:status"
        status_data = self.ctx_mgr._get_json(key, default={})
        log.debug(f"STATUS_LOOKUP | processing_id={processing_id} | status={status_data.get('status', 'not_found')}")
        return status_data or {"status": "not_found"}

    async def get_processing_result(self, processing_id: str) -> Optional[Dict[str, Any]]:
        """Get processing result from Redis."""
        key = f"processing:{processing_id}:result"
        result = self.ctx_mgr._get_json(key, default=None)
        log.debug(f"RESULT_LOOKUP | processing_id={processing_id} | found={result is not None}")
        return result

    async def get_products_for_flow(self, processing_id: str) -> List[Dict[str, Any]]:
        """Extract products from processing result for Flow display."""
        result = await self.get_processing_result(processing_id)
        if not result:
            log.warning(f"PRODUCTS_LOOKUP_EMPTY | processing_id={processing_id}")
            return []
        
        products = result.get("flow_data", {}).get("products", []) or []
        log.info(f"PRODUCTS_LOOKUP | processing_id={processing_id} | count={len(products)}")
        return products

    async def get_text_summary_for_flow(self, processing_id: str) -> str:
        """Extract text summary from processing result."""
        result = await self.get_processing_result(processing_id)
        if not result:
            log.warning(f"TEXT_LOOKUP_EMPTY | processing_id={processing_id}")
            return "No results available."
        
        text_content = result.get("text_content", "") or ""
        sections = result.get("sections", {}) or {}
        
        if sections:
            formatted_sections = self._format_sections_as_text(sections)
            full_text = (text_content + "\n\n" + formatted_sections) if text_content else formatted_sections
        else:
            full_text = text_content
            
        log.debug(f"TEXT_LOOKUP | processing_id={processing_id} | length={len(full_text)}")
        return full_text or "Results processed successfully."

    # ────────────────────────────────────────────────────────
    # FIX: Enhanced result storage with proper error handling
    # ────────────────────────────────────────────────────────

    async def _store_processing_result(
        self,
        processing_id: str,
        result,
        original_query: str,
        user_id: str,
        session_id: str,
    ) -> None:
        """
        FIX: Enhanced result storage with comprehensive logging and error handling.
        """
        log.info(f"RESULT_STORE_START | processing_id={processing_id}")
        
        # Initialize default values
        products_data: List[Dict[str, Any]] = []
        text_content = ""
        sections: Dict[str, Any] = {}
        response_type = "final_answer"
        functions_executed: List[str] = []
        requires_flow = False

        try:
            # Extract response type
            rtype = getattr(result, "response_type", "final_answer")
            response_type = rtype.value if hasattr(rtype, "value") else str(rtype)
            log.debug(f"RESULT_EXTRACT_TYPE | processing_id={processing_id} | response_type={response_type}")

            # Extract content
            content = getattr(result, "content", {}) or {}
            if isinstance(content, dict):
                text_content = content.get("message", "")
                sections = content.get("sections", {})
            else:
                text_content = str(content)
            log.debug(f"RESULT_EXTRACT_CONTENT | processing_id={processing_id} | text_len={len(text_content)} | sections_count={len(sections)}")

            # Extract functions executed
            functions_executed = getattr(result, "functions_executed", []) or []
            log.debug(f"RESULT_EXTRACT_FUNCTIONS | processing_id={processing_id} | functions={functions_executed}")

            # Extract Flow data if present
            if hasattr(result, "requires_flow"):
                requires_flow = bool(getattr(result, "requires_flow", False))
                flow_payload = getattr(result, "flow_payload", None)
                
                if requires_flow and flow_payload and hasattr(flow_payload, "products"):
                    log.info(f"RESULT_EXTRACT_FLOW | processing_id={processing_id} | products_count={len(flow_payload.products or [])}")
                    
                    for i, p in enumerate(flow_payload.products or []):
                        try:
                            product_data = {
                                "id": getattr(p, "product_id", f"prod_{i}"),
                                "title": getattr(p, "title", "Product"),
                                "subtitle": getattr(p, "subtitle", ""),
                                "price": getattr(p, "price", "Price on request"),
                                "brand": getattr(p, "brand", ""),
                                "rating": getattr(p, "rating", None),
                                "availability": getattr(p, "availability", "In Stock"),
                                "discount": getattr(p, "discount", ""),
                                "image": getattr(p, "image_url", "https://via.placeholder.com/200x200?text=Product"),
                                "features": getattr(p, "key_features", []),
                            }
                            products_data.append(product_data)
                            log.debug(f"PRODUCT_EXTRACTED | processing_id={processing_id} | product_{i}={product_data['title']}")
                        except Exception as product_error:
                            log.warning(f"PRODUCT_EXTRACT_ERROR | processing_id={processing_id} | product_{i} | error={product_error}")

        except Exception as extract_error:
            log.error(f"RESULT_EXTRACT_ERROR | processing_id={processing_id} | error={extract_error}", exc_info=True)

        # Build complete result data
        result_data = {
            "processing_id": processing_id,
            "user_id": user_id,
            "session_id": session_id,
            "original_query": original_query,
            "timestamp": datetime.now().isoformat(),
            "response_type": response_type,
            "text_content": text_content,
            "sections": sections,
            "functions_executed": functions_executed,
            "requires_flow": requires_flow,
            "flow_data": {
                "products": products_data,
                "flow_type": "product_catalog" if products_data else "text_summary",
                "header_text": f"Results for: {original_query[:50]}...",
                "footer_text": f"Found {len(products_data)} options" if products_data else "Analysis complete",
            },
        }

        # FIX: Store result with proper error handling
        try:
            result_key = f"processing:{processing_id}:result"
            self.ctx_mgr._set_json(result_key, result_data, ttl=self.processing_ttl)
            log.info(f"RESULT_STORE_SUCCESS | processing_id={processing_id} | key={result_key} | products={len(products_data)}")
        except Exception as store_error:
            log.error(f"RESULT_STORE_FAILED | processing_id={processing_id} | error={store_error}", exc_info=True)
            raise  # Re-raise to trigger failure status

    # ────────────────────────────────────────────────────────
    # FIX: Enhanced status management
    # ────────────────────────────────────────────────────────

    async def _set_processing_status(self, processing_id: str, status: str, metadata: Dict[str, Any]) -> None:
        """
        FIX: Enhanced status setting with atomic operations and logging.
        """
        try:
            status_key = f"processing:{processing_id}:status"
            payload = {
                "processing_id": processing_id,
                "status": status,
                "timestamp": datetime.now().isoformat(),
                "metadata": metadata or {},
            }
            
            t0 = time.perf_counter()
            self.ctx_mgr._set_json(status_key, payload, ttl=self.processing_ttl)
            log.info(f"STATUS_SET | processing_id={processing_id} | status={status} | metadata_keys={list(metadata.keys())} | duration_ms={(time.perf_counter()-t0)*1000:.1f}")
            
        except Exception as e:
            log.error(f"STATUS_SET_FAILED | processing_id={processing_id} | status={status} | error={e}", exc_info=True)
            raise

    # ────────────────────────────────────────────────────────
    # Minimal FE notify (unchanged but enhanced logging)
    # ────────────────────────────────────────────────────────
    
    async def _notify_fe_minimal(self, processing_id: str, user_id: str, wa_id: str, status: str) -> None:
        """Send minimal payload to FE with enhanced logging."""
        payload = {
            "processing_id": processing_id,
            "flow_id": getattr(Cfg, "WHATSAPP_FLOW_ID", "") or "",
            "wa_id": str(wa_id or ""),
            "status": status,
            "user_id": user_id,
            "timestamp": datetime.now().isoformat(),
        }
        
        log.info(f"FE_NOTIFY_START | processing_id={processing_id} | status={status} | wa_id={wa_id}")
        notifier = FrontendNotifier()
        success = await notifier.post_json(payload)
        
        if success:
            log.info(f"FE_NOTIFY_SUCCESS | processing_id={processing_id}")
        else:
            log.warning(f"FE_NOTIFY_FAILED | processing_id={processing_id}")

    def _resolve_wa_id(self, ctx, user_id: str) -> str:
        """Pull wa_id from context with enhanced logging."""
        try:
            if ctx and isinstance(ctx.session, dict):
                if "wa_id" in ctx.session and ctx.session["wa_id"]:
                    wa_id = str(ctx.session["wa_id"])
                    log.debug(f"WA_ID_FROM_SESSION | user_id={user_id} | wa_id={wa_id}")
                    return wa_id
                
                user_bucket = ctx.session.get("user") or {}
                if user_bucket.get("wa_id"):
                    wa_id = str(user_bucket["wa_id"])
                    log.debug(f"WA_ID_FROM_USER_BUCKET | user_id={user_id} | wa_id={wa_id}")
                    return wa_id
        except Exception as e:
            log.warning(f"WA_ID_RESOLVE_ERROR | user_id={user_id} | error={e}")
        
        # Fallback
        fallback = os.getenv("FE_TEST_WA_ID", "917398580865")
        log.debug(f"WA_ID_FALLBACK | user_id={user_id} | wa_id={fallback}")
        return fallback

    def _format_sections_as_text(self, sections: Dict[str, str]) -> str:
        """Format sections dict as readable text."""
        formatted = []
        order = ["MAIN", "ALT", "+", "INFO", "TIPS", "LINKS"]
        names = {
            "MAIN": "Main Information",
            "ALT": "Alternative Options", 
            "+": "Additional Benefits",
            "INFO": "Important Information",
            "TIPS": "Tips & Recommendations",
            "LINKS": "Useful Links",
        }
        
        for key in order:
            val = (sections.get(key) or "").strip()
            if val:
                formatted.append(f"{names[key]}:\n{val}")
        
        return "\n\n".join(formatted)


class FrontendNotifier:
    """Enhanced webhook poster with detailed logging."""

    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or getattr(Cfg, "FRONTEND_WEBHOOK_URL", None)
        self.insecure = os.getenv("FRONTEND_WEBHOOK_INSECURE", "false").lower() == "true"
        try:
            self.timeout = int(os.getenv("FRONTEND_WEBHOOK_TIMEOUT", "10"))
        except Exception:
            self.timeout = 10

        # Retries
        try:
            self.max_retries = int(os.getenv("FE_WEBHOOK_MAX_RETRIES", "3"))
        except Exception:
            self.max_retries = 3
        try:
            self.retry_base_ms = int(os.getenv("FE_WEBHOOK_RETRY_BASE_MS", "250"))
        except Exception:
            self.retry_base_ms = 250

        # Logging controls
        self.log_payloads = os.getenv("FE_WEBHOOK_LOG_PAYLOADS", "true").lower() == "true"
        self.log_response = os.getenv("FE_WEBHOOK_LOG_RESPONSE", "true").lower() == "true"
        try:
            self.max_log_bytes = int(os.getenv("FE_WEBHOOK_LOG_MAX_BYTES", "8192"))
        except Exception:
            self.max_log_bytes = 8192

        # Derived
        self._parsed = urlparse(self.webhook_url) if self.webhook_url else None
        log.info(
            "FE_WEBHOOK_INIT | url=%s | host=%s | insecure=%s | timeout_s=%s | max_retries=%s | retry_base_ms=%s",
            self.webhook_url,
            (self._parsed.hostname if self._parsed else None),
            self.insecure,
            self.timeout,
            self.max_retries,
            self.retry_base_ms,
        )

    def _truncate(self, s: str) -> str:
        if len(s) <= self.max_log_bytes:
            return s
        return f"{s[:self.max_log_bytes]}... (truncated {len(s) - self.max_log_bytes} bytes)"

    async def post_json(self, payload: Dict[str, Any]) -> bool:
        """POST JSON to FE webhook with comprehensive logging and retries."""
        if not self.webhook_url:
            log.warning(f"WEBHOOK_NO_URL | payload={payload}")
            return False

        # Prepare JSON payload
        try:
            payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        except Exception as e:
            log.error(f"WEBHOOK_JSON_ERROR | error={e} | payload={payload}")
            return False

        if self.log_payloads:
            log.info(f"WEBHOOK_POST | url={self.webhook_url} | payload={self._truncate(payload_json)}")

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        connector = aiohttp.TCPConnector(ssl=False) if self.insecure else None

        # Trace callbacks for deep visibility
        trace_config = aiohttp.TraceConfig()

        @trace_config.on_request_start.append
        async def on_request_start(session, context, params):  # noqa: ANN001
            log.info(
                "AIOHTTP_REQUEST_START | method=%s | url=%s | headers=%s",
                params.method,
                params.url,
                self._truncate(str(params.headers)),
            )

        @trace_config.on_request_end.append
        async def on_request_end(session, context, params):  # noqa: ANN001
            log.info(
                "AIOHTTP_REQUEST_END | method=%s | url=%s | elapsed_ms=unknown",
                params.method,
                params.url,
            )

        @trace_config.on_connection_create_start.append
        async def on_conn_start(session, context, params):  # noqa: ANN001
            log.info("AIOHTTP_CONN_CREATE_START | host=%s | port=%s | ssl=%s", params.host, params.port, params.ssl)

        @trace_config.on_connection_create_end.append
        async def on_conn_end(session, context, params):  # noqa: ANN001
            log.info("AIOHTTP_CONN_CREATE_END | connection=%s", params.transport)

        # Retry loop
        start_overall = time.perf_counter()
        for attempt in range(1, self.max_retries + 1):
            attempt_start = time.perf_counter()
            try:
                async with aiohttp.ClientSession(timeout=timeout, connector=connector, trace_configs=[trace_config]) as session:
                    async with session.post(
                        self.webhook_url,
                        json=payload,
                        headers={"Content-Type": "application/json"},
                    ) as resp:
                        text = await resp.text()
                        headers_str = self._truncate(str(dict(resp.headers)))
                        log.info(
                            "WEBHOOK_RESPONSE | status=%s | attempt=%s/%s | elapsed_ms=%.1f | headers=%s | body=%s",
                            resp.status,
                            attempt,
                            self.max_retries,
                            (time.perf_counter() - attempt_start) * 1000,
                            headers_str,
                            self._truncate(text or ""),
                        )
                        if 200 <= resp.status < 300:
                            log.info(
                                "WEBHOOK_SUCCESS | total_elapsed_ms=%.1f | attempts=%s",
                                (time.perf_counter() - start_overall) * 1000,
                                attempt,
                            )
                            return True

                        # Non-2xx
                        if attempt < self.max_retries:
                            delay = (self.retry_base_ms * (2 ** (attempt - 1))) / 1000.0
                            jitter = random.uniform(0, delay * 0.2)
                            sleep_for = delay + jitter
                            log.warning(
                                "WEBHOOK_BAD_STATUS | status=%s | attempt=%s/%s | retry_in_ms=%.0f",
                                resp.status,
                                attempt,
                                self.max_retries,
                                sleep_for * 1000,
                            )
                            await asyncio.sleep(sleep_for)
                            continue
                        else:
                            log.warning("WEBHOOK_GIVING_UP | status=%s | attempts=%s", resp.status, attempt)
                            return False

            except asyncio.TimeoutError:
                if attempt < self.max_retries:
                    delay = (self.retry_base_ms * (2 ** (attempt - 1))) / 1000.0
                    jitter = random.uniform(0, delay * 0.2)
                    sleep_for = delay + jitter
                    log.error(
                        "WEBHOOK_TIMEOUT | attempt=%s/%s | retry_in_ms=%.0f | url=%s",
                        attempt,
                        self.max_retries,
                        sleep_for * 1000,
                        self.webhook_url,
                    )
                    await asyncio.sleep(sleep_for)
                    continue
                else:
                    log.error("WEBHOOK_TIMEOUT_FINAL | attempts=%s | url=%s", attempt, self.webhook_url)
                    return False
            except aiohttp.ClientError as e:
                if attempt < self.max_retries:
                    delay = (self.retry_base_ms * (2 ** (attempt - 1))) / 1000.0
                    jitter = random.uniform(0, delay * 0.2)
                    sleep_for = delay + jitter
                    log.error(
                        "WEBHOOK_CLIENT_ERROR | attempt=%s/%s | retry_in_ms=%.0f | error=%s | type=%s",
                        attempt,
                        self.max_retries,
                        sleep_for * 1000,
                        e,
                        type(e).__name__,
                    )
                    await asyncio.sleep(sleep_for)
                    continue
                else:
                    log.error(
                        "WEBHOOK_CLIENT_ERROR_FINAL | attempts=%s | error=%s | type=%s",
                        attempt,
                        e,
                        type(e).__name__,
                    )
                    return False
            except Exception as e:
                log.error(
                    "WEBHOOK_UNEXPECTED_ERROR | attempt=%s/%s | url=%s | error=%s | type=%s",
                    attempt,
                    self.max_retries,
                    self.webhook_url,
                    e,
                    type(e).__name__,
                    exc_info=True,
                )
                return False

        # Should not reach here
        return False