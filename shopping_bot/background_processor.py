from __future__ import annotations

import asyncio
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
        notification_callback: Optional[callable] = None,
    ) -> str:
        """
        Execute heavy work, persist the full result for polling,
        and ping FE with a *minimal* button-show payload.
        """
        processing_id = f"bg_{user_id}_{session_id}_{int(datetime.now().timestamp())}"
        await self._set_processing_status(processing_id, "processing", {"query": query})

        ctx = None
        try:
            ctx = self.ctx_mgr.get_context(user_id, session_id)

            # Mark assessment phase
            if "assessment" in ctx.session:
                ctx.session["assessment"]["phase"] = "processing"
                self.ctx_mgr.save_context(ctx)

            log.info("Starting background processing for %s", processing_id)

            # Do the real work
            result = await self.enhanced_bot.process_query(query, ctx, enable_flows=True)

            # Defensive: if core still returned PROCESSING_STUB here, force a sync run
            if getattr(result, "response_type", None) == ResponseType.PROCESSING_STUB:
                log.info("Core returned PROCESSING_STUB in background for %s – forcing sync", processing_id)
                ctx.session["needs_background"] = False
                self.ctx_mgr.save_context(ctx)
                result = await self.enhanced_bot.process_query(query, ctx, enable_flows=True)

            # Normalize + store full result for polling APIs
            await self._store_processing_result(
                processing_id=processing_id,
                result=result,
                original_query=query,
                user_id=user_id,
                session_id=session_id,
            )

            # Minimal FE notify (COMPLETED)
            wa_id = self._resolve_wa_id(ctx, user_id)
            await self._notify_fe_minimal(processing_id, user_id, wa_id, status="completed")

            log.info("Background processing completed for %s", processing_id)
            return processing_id

        except Exception as e:  # noqa: BLE001
            await self._set_processing_status(processing_id, "failed", {"error": str(e)})

            # Try to notify FE even on failure (best effort)
            try:
                wa_id = self._resolve_wa_id(ctx, user_id) if ctx else os.getenv("FE_TEST_WA_ID", "917398580865")
                await self._notify_fe_minimal(processing_id, user_id, wa_id, status="failed")
            except Exception:
                pass

            log.exception("Background processing failed for %s", processing_id)
            raise

    async def get_processing_status(self, processing_id: str) -> Dict[str, Any]:
        key = f"processing:{processing_id}:status"
        status_data = self.ctx_mgr._get_json(key, default={})
        return status_data or {"status": "not_found"}

    async def get_processing_result(self, processing_id: str) -> Optional[Dict[str, Any]]:
        key = f"processing:{processing_id}:result"
        return self.ctx_mgr._get_json(key, default=None)

    async def get_products_for_flow(self, processing_id: str) -> List[Dict[str, Any]]:
        result = await self.get_processing_result(processing_id)
        if not result:
            return []
        return result.get("flow_data", {}).get("products", []) or []

    async def get_text_summary_for_flow(self, processing_id: str) -> str:
        result = await self.get_processing_result(processing_id)
        if not result:
            return "No results available."
        text_content = result.get("text_content", "") or ""
        sections = result.get("sections", {}) or {}
        full_text = text_content
        if sections:
            full_text = (full_text + "\n\n" if full_text else "") + self._format_sections_as_text(sections)
        return full_text or "Results processed successfully."

    # ────────────────────────────────────────────────────────
    # Storage (schema kept for polling endpoints)
    # ────────────────────────────────────────────────────────
    async def _store_processing_result(
        self,
        processing_id: str,
        result,
        original_query: str,
        user_id: str,
        session_id: str,
    ) -> None:
        products_data: List[Dict[str, Any]] = []
        text_content = ""
        sections: Dict[str, Any] = {}
        response_type = "final_answer"
        functions_executed: List[str] = []
        requires_flow = False

        try:
            rtype = getattr(result, "response_type", "final_answer")
            response_type = rtype.value if hasattr(rtype, "value") else str(rtype)

            content = getattr(result, "content", {}) or {}
            text_content = content.get("message", "") if isinstance(content, dict) else str(content)
            sections = content.get("sections", {}) if isinstance(content, dict) else {}

            functions_executed = getattr(result, "functions_executed", []) or []

            if hasattr(result, "requires_flow"):
                requires_flow = bool(getattr(result, "requires_flow", False))
                flow_payload = getattr(result, "flow_payload", None)
                if requires_flow and flow_payload and hasattr(flow_payload, "products"):
                    for i, p in enumerate(flow_payload.products or []):
                        products_data.append(
                            {
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
                        )
        except Exception as e:  # noqa: BLE001
            log.warning("Result normalization error: %s", e)

        if not products_data and text_content:
            products_data = self._create_dummy_products_from_text(text_content)

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

        # Persist for polling APIs
        result_key = f"processing:{processing_id}:result"
        self.ctx_mgr._set_json(result_key, result_data, ttl=self.processing_ttl)

        # Update status
        await self._set_processing_status(
            processing_id,
            "completed",
            {"products_count": len(products_data), "has_flow": requires_flow, "text_length": len(text_content)},
        )

        # Optional: send legacy static test payloads only if explicitly enabled
        if os.getenv("FE_TEST_STATIC_PAYLOADS", "false").lower() == "true":
            try:
                wa_id = self._resolve_wa_id(self.ctx_mgr.get_context(user_id, session_id), user_id)
                await self._send_static_payloads(wa_id)
            except Exception as e:  # noqa: BLE001
                log.warning("Failed sending static FE test payloads: %s", e)

    # ────────────────────────────────────────────────────────
    # Minimal FE notify
    # ────────────────────────────────────────────────────────
    async def _notify_fe_minimal(self, processing_id: str, user_id: str, wa_id: str, status: str) -> None:
        """
        Send the minimal payload to FE:
        processing_id, flow_id, wa_id, status, user_id, timestamp
        """
        payload = {
            "processing_id": processing_id,
            "flow_id": getattr(Cfg, "WHATSAPP_FLOW_ID", "") or "",
            "wa_id": str(wa_id or ""),
            "status": status,  # "completed" or "failed"
            "user_id": user_id,
            "timestamp": datetime.now().isoformat(),
        }
        notifier = FrontendNotifier()
        await notifier.post_json(payload)

    # ────────────────────────────────────────────────────────
    # Helpers
    # ────────────────────────────────────────────────────────
    def _resolve_wa_id(self, ctx, user_id: str) -> str:
        """
        Pull wa_id from context set by /chat; fallback to env or empty string.
        """
        try:
            if ctx and isinstance(ctx.session, dict):
                if "wa_id" in ctx.session and ctx.session["wa_id"]:
                    return str(ctx.session["wa_id"])
                user_bucket = ctx.session.get("user") or {}
                if user_bucket.get("wa_id"):
                    return str(user_bucket["wa_id"])
        except Exception:
            pass
        # Fallback (dev)
        return os.getenv("FE_TEST_WA_ID", "917398580865")

    async def _send_static_payloads(self, wa_id: str) -> None:
        # For one-off testing; disabled unless FE_TEST_STATIC_PAYLOADS=true
        now_iso = datetime.now().isoformat()
        samples = [
            {
                "wa_id": wa_id,
                "content": {
                    "message": (
                        "Alternatives: I'd be happy to help you find shoes, but unfortunately we don't have any "
                        "shoes currently available in our inventory that match your budget of under ₹10k. You might "
                        "want to check back later as our inventory updates regularly, or consider expanding your "
                        "search criteria.\n\n"
                        "*Watch-outs:* No shoes are currently available in our inventory within your specified "
                        "budget range.\n\n"
                        "*Extra info:* Based on your preferences for size-focused shoes under ₹10k, I searched our "
                        "current inventory but found no matching products available at this time."
                    ),
                    "sections": {
                        "+": "",
                        "-": "No shoes are currently available in our inventory within your specified budget range.",
                        "ALT": (
                            "I'd be happy to help you find shoes, but unfortunately we don't have any shoes currently "
                            "available in our inventory that match your budget of under ₹10k. You might want to check "
                            "back later as our inventory updates regularly, or consider expanding your search criteria."
                        ),
                        "BUY": "",
                        "INFO": (
                            "Based on your preferences for size-focused shoes under ₹10k, I searched our current "
                            "inventory but found no matching products available at this time."
                        ),
                        "OVERRIDE": ""
                    }
                },
                "functions_executed": ["FETCH_PRODUCT_INVENTORY"],
                "response_type": "final_answer",
                "timestamp": now_iso
            },
            {
                "wa_id": wa_id,
                "content": {
                    "hints": ["Consider size, brand, quality, features, etc."],
                    "message": "What features matter most to you?",
                    "options": [
                        {
                            "label": "Consider size, brand, quality, features, etc.",
                            "value": "Consider size, brand, quality, features, etc."
                        },
                        {"label": "Other", "value": "Other"}
                    ],
                    "type": "multi_choice"
                },
                "functions_executed": [],
                "response_type": "question",
                "timestamp": now_iso
            }
        ]
        notifier = FrontendNotifier()
        for p in samples:
            await notifier.post_json(p)

    async def _set_processing_status(self, processing_id: str, status: str, metadata: Dict[str, Any]) -> None:
        status_key = f"processing:{processing_id}:status"
        payload = {
            "processing_id": processing_id,
            "status": status,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {},
        }
        self.ctx_mgr._set_json(status_key, payload, ttl=self.processing_ttl)

    def _format_sections_as_text(self, sections: Dict[str, str]) -> str:
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

    def _create_dummy_products_from_text(self, text_content: str) -> List[Dict[str, Any]]:
        tl = text_content.lower()
        if any(w in tl for w in ["laptop", "computer", "gaming"]):
            return [{
                "id": "prod_laptop_1",
                "title": "Gaming Laptop Recommendation",
                "subtitle": "Based on your query analysis",
                "price": "$899",
                "brand": "Recommended",
                "rating": 4.5,
                "availability": "Available",
                "discount": "",
                "image": "https://via.placeholder.com/200x200/4CAF50/FFFFFF?text=Laptop",
                "features": ["High Performance", "Good Value", "Recommended Choice"],
            }]
        if any(w in tl for w in ["phone", "mobile", "smartphone"]):
            return [{
                "id": "prod_phone_1",
                "title": "Smartphone Recommendation",
                "subtitle": "Based on your query analysis",
                "price": "$699",
                "brand": "Recommended",
                "rating": 4.3,
                "availability": "Available",
                "discount": "",
                "image": "https://via.placeholder.com/200x200/2196F3/FFFFFF?text=Phone",
                "features": ["Latest Features", "Great Camera", "Long Battery Life"],
            }]
        return [{
            "id": "prod_general_1",
            "title": "Product Recommendation",
            "subtitle": "Based on your analysis",
            "price": "Contact for price",
            "brand": "Various",
            "rating": 4.0,
            "availability": "Available",
            "discount": "",
            "image": "https://via.placeholder.com/200x200/9C27B0/FFFFFF?text=Product",
            "features": ["Quality Product", "Good Value", "Recommended"],
        }]


class FrontendNotifier:
    """Webhook poster with explicit, compact logs of the outbound payload and FE response."""

    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or getattr(Cfg, "FRONTEND_WEBHOOK_URL", None)
        self.insecure = os.getenv("FRONTEND_WEBHOOK_INSECURE", "false").lower() == "true"
        try:
            self.timeout = int(os.getenv("FRONTEND_WEBHOOK_TIMEOUT", "10"))
        except Exception:
            self.timeout = 10

        # Logging controls
        self.log_payloads = os.getenv("FE_WEBHOOK_LOG_PAYLOADS", "true").lower() == "true"
        self.log_response = os.getenv("FE_WEBHOOK_LOG_RESPONSE", "true").lower() == "true"
        try:
            self.max_log_bytes = int(os.getenv("FE_WEBHOOK_LOG_MAX_BYTES", "8192"))
        except Exception:
            self.max_log_bytes = 8192

    def _truncate(self, s: str) -> str:
        if len(s) <= self.max_log_bytes:
            return s
        return f"{s[:self.max_log_bytes]}... (truncated {len(s) - self.max_log_bytes} bytes)"

    async def post_json(self, payload: Dict[str, Any]) -> bool:
        """POST JSON to FE webhook; returns True on 2xx, logging payload & response."""
        if not self.webhook_url:
            log.warning("No FRONTEND_WEBHOOK_URL configured – skipping webhook. Payload: %s", payload)
            return False

        # Log exactly what we are sending
        try:
            payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            payload_json = str(payload)

        if self.log_payloads:
            log.info(
                "FE webhook POST url=%s insecure=%s timeout=%ss payload=%s",
                self.webhook_url, self.insecure, self.timeout, self._truncate(payload_json)
            )

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        connector = aiohttp.TCPConnector(ssl=False) if self.insecure else None

        try:
            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                async with session.post(
                    self.webhook_url,
                    data=payload_json,  # send the exact json string we logged
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    text = await resp.text()
                    if self.log_response:
                        log.info(
                            "FE webhook response status=%s reason=%s body=%s",
                            resp.status, getattr(resp, "reason", ""), self._truncate(text or "")
                        )
                    if 200 <= resp.status < 300:
                        return True
                    log.warning("Frontend notification failed: %s - %s", resp.status, text)
                    return False

        except asyncio.TimeoutError:
            log.error("Frontend notification timeout to %s", self.webhook_url)
            return False
        except aiohttp.ClientError as e:
            msg = str(e)
            if "CERTIFICATE_VERIFY_FAILED" in msg:
                log.error(
                    "SSL verification failed posting to %s (%s). "
                    "For dev/ngrok set FRONTEND_WEBHOOK_INSECURE=true to bypass verify.",
                    self.webhook_url, e
                )
            else:
                log.error("HTTP client error posting to %s (%s).", self.webhook_url, e)
            return False
        except Exception as e:  # noqa: BLE001
            log.error("Failed to send frontend notification: %s", e)
            return False
