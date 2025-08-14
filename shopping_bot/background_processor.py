# shopping_bot/background_processor.py
"""
Background processing service – single-phase executor.

Design:
- The bot core decides backgrounding (returns PROCESSING_STUB after ask-loop).
- This worker rehydrates context and executes the heavy path.
- If the bot (defensively) returns PROCESSING_STUB again, we flip
  `ctx.session["needs_background"]=False` and re-run to force synchronous completion.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import aiohttp

from .redis_manager import RedisContextManager
from .models import UserContext
from .config import get_config
from .enums import ResponseType

# NEW: for Option B flow sending
try:
    from .dual_message_dispather import DualMessageDispatcher  # note: file name is 'dispather' in repo
except Exception:  # pragma: no cover
    DualMessageDispatcher = None  # type: ignore[assignment]

# If you want to build a FlowPayload fallback when core didn't provide one:
try:
    from .models import FlowPayload, ProductData, FlowType
except Exception:  # pragma: no cover
    FlowPayload = None  # type: ignore[assignment]
    ProductData = None  # type: ignore[assignment]
    FlowType = None  # type: ignore[assignment]

Cfg = get_config()
log = logging.getLogger(__name__)


class BackgroundProcessor:
    """Handles background execution of complex queries, result storage, and (Option B) sending Flow."""

    def __init__(
        self,
        enhanced_bot_core,
        ctx_mgr: RedisContextManager,
        dispatcher: Optional["DualMessageDispatcher"] = None,  # NEW
    ):
        self.enhanced_bot = enhanced_bot_core
        self.ctx_mgr = ctx_mgr
        self.dispatcher = dispatcher  # NEW
        self.processing_ttl = timedelta(hours=2)  # retention for status/result

    async def process_query_background(
        self,
        query: str,
        user_id: str,
        session_id: str,
        notification_callback: Optional[callable] = None,
    ) -> str:
        """
        Execute heavy work in background, store result, and (Option B) send the Flow.

        Returns:
            processing_id (str): key for polling status/result.
        """
        processing_id = f"bg_{user_id}_{session_id}_{int(datetime.now().timestamp())}"

        # Mark as processing upfront
        await self._set_processing_status(processing_id, "processing", {"query": query})

        try:
            # Rehydrate context (ask-loop should already be complete)
            ctx = self.ctx_mgr.get_context(user_id, session_id)

            # Make sure assessment is marked as processing (best-effort)
            if "assessment" in ctx.session:
                ctx.session["assessment"]["phase"] = "processing"
                self.ctx_mgr.save_context(ctx)

            log.info("Starting background processing for %s", processing_id)

            # First attempt: normal enhanced processing
            result = await self.enhanced_bot.process_query(query, ctx, enable_flows=True)

            # Defensive guard: if the core still returns a stub here, force sync path
            if getattr(result, "response_type", None) == ResponseType.PROCESSING_STUB:
                log.info(
                    "Core returned PROCESSING_STUB in background for %s – forcing sync",
                    processing_id,
                )
                ctx.session["needs_background"] = False
                self.ctx_mgr.save_context(ctx)
                result = await self.enhanced_bot.process_query(query, ctx, enable_flows=True)

            # Persist final result
            result_data = await self._store_processing_result(
                processing_id=processing_id,
                result=result,
                original_query=query,
                user_id=user_id,
                session_id=session_id,
            )

            # ──────────────────────────────────────────────────────────────
            # Option B: send the Flow from the server (if possible)
            # ──────────────────────────────────────────────────────────────
            await self._maybe_send_flow(
                processing_id=processing_id,
                result=result,
                result_data=result_data,
                ctx=ctx,
                user_id=user_id,
                session_id=session_id,
            )

            log.info("Background processing completed for %s", processing_id)
            return processing_id

        except Exception as e:  # noqa: BLE001
            # Store failure status
            await self._set_processing_status(processing_id, "failed", {"error": str(e)})

            # Still notify FE (optional)
            notifier = FrontendNotifier()
            await notifier.notify_completion(
                processing_id, "failed", user_id, {"error": str(e), "query": query}
            )
            if notification_callback:
                try:
                    await notification_callback(processing_id, "failed", user_id)
                except Exception as cb_exc:  # noqa: BLE001
                    log.warning("notification_callback failed: %s", cb_exc)

            log.exception("Background processing failed for %s", processing_id)
            raise

    async def get_processing_status(self, processing_id: str) -> Dict[str, Any]:
        """Return current status for a processing_id."""
        key = f"processing:{processing_id}:status"
        status_data = self.ctx_mgr._get_json(key, default={})
        return status_data or {"status": "not_found"}

    async def get_processing_result(self, processing_id: str) -> Optional[Dict[str, Any]]:
        """Return completed result (if any) for a processing_id."""
        key = f"processing:{processing_id}:result"
        return self.ctx_mgr._get_json(key, default=None)

    async def get_products_for_flow(self, processing_id: str) -> List[Dict[str, Any]]:
        """Extract products from stored result for Flow consumption (optional helper)."""
        result = await self.get_processing_result(processing_id)
        if not result:
            return []
        return result.get("flow_data", {}).get("products", []) or []

    async def get_text_summary_for_flow(self, processing_id: str) -> str:
        """Return a human-readable text summary composed from stored result."""
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
    # Storage & formatting
    # ────────────────────────────────────────────────────────
    async def _store_processing_result(
        self,
        processing_id: str,
        result,
        original_query: str,
        user_id: str,
        session_id: str,
    ) -> Dict[str, Any]:
        """Normalize and store result; update status; notify frontend. Returns the normalized payload."""

        products_data: List[Dict[str, Any]] = []
        text_content = ""
        sections: Dict[str, Any] = {}
        response_type = "final_answer"
        functions_executed: List[str] = []
        requires_flow = False

        try:
            # response_type
            rtype = getattr(result, "response_type", "final_answer")
            response_type = rtype.value if hasattr(rtype, "value") else str(rtype)

            # content
            content = getattr(result, "content", {}) or {}
            text_content = content.get("message", "") if isinstance(content, dict) else str(content)
            sections = content.get("sections", {}) if isinstance(content, dict) else {}

            # functions executed
            functions_executed = getattr(result, "functions_executed", []) or []

            # flow extraction (EnhancedBotResponse)
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

        # Fallback: synthesize a minimal product when no flow products available
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

        # Persist
        result_key = f"processing:{processing_id}:result"
        self.ctx_mgr._set_json(result_key, result_data, ttl=self.processing_ttl)

        # Update status → completed
        await self._set_processing_status(
            processing_id,
            "completed",
            {"products_count": len(products_data), "has_flow": requires_flow, "text_length": len(text_content)},
        )

        # Notify FE (kept as optional signal; Option B can work with/without this)
        notifier = FrontendNotifier()
        await notifier.notify_completion(
            processing_id,
            "completed",
            user_id,
            {
                "query": original_query,
                "session_id": session_id,
                "flow_data": result_data["flow_data"],
                "has_products": len(products_data) > 0,
                "has_flow_data": len(products_data) > 0 or bool(text_content),
            },
        )
        return result_data

    async def _maybe_send_flow(
        self,
        processing_id: str,
        result: Any,
        result_data: Dict[str, Any],
        ctx: "UserContext",
        user_id: str,
        session_id: str,
    ) -> None:
        """Option B: If possible, send the WhatsApp Flow directly from server."""
        if not self.dispatcher:
            log.info("No DualMessageDispatcher configured; skipping server-side Flow send.")
            return

        phone = self._resolve_phone_number(ctx)
        if not phone:
            log.warning("No phone number available in context; skipping Flow dispatch for %s", processing_id)
            return

        # If the core already built a FlowPayload, prefer that.
        core_flow_payload = getattr(result, "flow_payload", None)
        if core_flow_payload is not None:
            try:
                await self.dispatcher.dispatch_flow_only(
                    core_flow_payload,
                    phone_number=phone,
                    user_id=user_id,
                    session_id=session_id,
                    processing_id=processing_id,
                )
                log.info("Server-side Flow dispatched (core payload) for %s", processing_id)
                return
            except Exception as e:
                log.warning("Flow dispatch (core payload) failed for %s: %s; will try fallback.", processing_id, e)

        # Fallback: construct a minimal FlowPayload from stored result_data
        if FlowPayload and ProductData and FlowType:
            try:
                products = []
                for p in (result_data.get("flow_data", {}).get("products") or []):
                    products.append(
                        ProductData(
                            product_id=str(p.get("id") or ""),
                            title=p.get("title") or "Product",
                            subtitle=p.get("subtitle") or "",
                            image_url=p.get("image") or "https://via.placeholder.com/200x200?text=Product",
                            price=p.get("price") or "Price on request",
                            rating=p.get("rating"),
                            brand=p.get("brand"),
                            key_features=p.get("features") or [],
                            availability=p.get("availability") or "In Stock",
                            discount=p.get("discount"),
                        )
                    )

                header_text = result_data.get("flow_data", {}).get("header_text") or "Product Options"
                footer_text = result_data.get("flow_data", {}).get("footer_text") or "Tap to explore"

                fallback_payload = FlowPayload(
                    flow_type=FlowType.PRODUCT_CATALOG,
                    products=products,
                    header_text=header_text,
                    footer_text=footer_text,
                )

                await self.dispatcher.dispatch_flow_only(
                    fallback_payload,
                    phone_number=phone,
                    user_id=user_id,
                    session_id=session_id,
                    processing_id=processing_id,
                )
                log.info("Server-side Flow dispatched (fallback payload) for %s", processing_id)
            except Exception as e:  # noqa: BLE001
                log.warning("Flow dispatch (fallback) failed for %s: %s", processing_id, e)
        else:
            log.info("FlowPayload model not available; cannot build fallback payload.")

    def _resolve_phone_number(self, ctx: "UserContext") -> Optional[str]:
        """
        Best-effort extraction of the user's WhatsApp phone number from context.
        Adjust this to match your actual context structure.
        """
        # Common places to look:
        # 1) ctx.session["user"]["phone"]
        try:
            phone = (ctx.session.get("user") or {}).get("phone")
            if phone:
                return str(phone)
        except Exception:
            pass
        # 2) ctx.user_profile.phone (if present)
        try:
            profile = getattr(ctx, "user_profile", None)
            phone = getattr(profile, "phone", None) if profile else None
            if phone:
                return str(phone)
        except Exception:
            pass
        # 3) environment for dev/testing
        try:
            phone = getattr(Cfg, "DEFAULT_TEST_PHONE", None)
            if phone:
                return str(phone)
        except Exception:
            pass
        return None

    def _create_dummy_products_from_text(self, text_content: str) -> List[Dict[str, Any]]:
        """Very coarse fallback to keep the UI flowing when no products present."""
        tl = text_content.lower()
        if any(w in tl for w in ["laptop", "computer", "gaming"]):
            return [
                {
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
                }
            ]
        if any(w in tl for w in ["phone", "mobile", "smartphone"]):
            return [
                {
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
                }
            ]
        return [
            {
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
            }
        ]

    async def _set_processing_status(self, processing_id: str, status: str, metadata: Dict[str, Any]) -> None:
        """Persist processing status with TTL."""
        status_key = f"processing:{processing_id}:status"
        payload = {
            "processing_id": processing_id,
            "status": status,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {},
        }
        self.ctx_mgr._set_json(status_key, payload, ttl=self.processing_ttl)

    def _format_sections_as_text(self, sections: Dict[str, str]) -> str:
        """Render section dict to text for summary views."""
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
    """Notify the frontend when background processing completes (optional webhook)."""

    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or getattr(Cfg, "FRONTEND_WEBHOOK_URL", None)

    async def notify_completion(
        self,
        processing_id: str,
        status: str,
        user_id: str,
        additional_data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """POST a completion payload to the frontend, if configured."""
        additional_data = additional_data or {}
        has_flow_data = bool(additional_data.get("has_flow_data"))
        flow_type = "product_recommendations" if additional_data.get("has_products") else "text_summary"

        payload = {
            "processing_id": processing_id,
            "status": status,
            "user_id": user_id,
            "timestamp": datetime.now().isoformat(),
            "action": "show_flow_button",
            "has_flow_data": has_flow_data,
            "flow_type": flow_type,
            "webhook_url": self.webhook_url,
            "data": additional_data,
        }

        if not self.webhook_url:
            log.warning("No FRONTEND_WEBHOOK_URL configured – skipping webhook. Payload: %s", payload)
            return False

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=10,
                ) as resp:
                    text = await resp.text()
                    if resp.status == 200:
                        log.info("Flow button notification sent to %s", self.webhook_url)
                        log.debug("Frontend response: %s", text)
                        return True
                    log.warning("Frontend notification failed: %s - %s", resp.status, text)
                    return False
        except aiohttp.ClientTimeout:
            log.error("Frontend notification timeout to %s", self.webhook_url)
            return False
        except Exception as e:  # noqa: BLE001
            log.error("Failed to send frontend notification: %s", e)
            return False
