# shopping_bot/background_processor.py
"""
Background processing service with real HTTP notifications
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import json
import aiohttp

from .redis_manager import RedisContextManager
from .models import UserContext
from .config import get_config

Cfg = get_config()
log = logging.getLogger(__name__)

class BackgroundProcessor:
    """Handles background processing of complex queries and result storage"""
    
    def __init__(self, enhanced_bot_core, ctx_mgr: RedisContextManager):
        self.enhanced_bot = enhanced_bot_core
        self.ctx_mgr = ctx_mgr
        self.processing_ttl = timedelta(hours=2)  # How long to keep results
        
    async def process_query_background(
        self, 
        query: str, 
        user_id: str, 
        session_id: str,
        notification_callback: Optional[callable] = None
    ) -> str:
        """
        Process query in background and store results.
        Returns processing_id for tracking.
        """
        processing_id = f"bg_{user_id}_{session_id}_{int(datetime.now().timestamp())}"
        
        # Mark as processing
        await self._set_processing_status(processing_id, "processing", {"query": query})
        
        try:
            # Load user context
            ctx = self.ctx_mgr.get_context(user_id, session_id)
            
            # Process the query (this is the heavy operation)
            log.info(f"Starting background processing for {processing_id}")
            result = await self.enhanced_bot.process_query(query, ctx, enable_flows=True)
            
            # Store the results (safely handle both response types)
            await self._store_processing_result(processing_id, result, query, user_id, session_id)
            
            log.info(f"Background processing completed for {processing_id}")
            return processing_id
            
        except Exception as e:
            # Mark as failed
            await self._set_processing_status(processing_id, "failed", {"error": str(e)})
            
            # Notify failure too
            notifier = FrontendNotifier()
            await notifier.notify_completion(processing_id, "failed", user_id, {
                "error": str(e),
                "query": query
            })
            
            if notification_callback:
                await notification_callback(processing_id, "failed", user_id)
                
            log.error(f"Background processing failed for {processing_id}: {e}")
            raise
    
    async def get_processing_status(self, processing_id: str) -> Dict[str, Any]:
        """Get current processing status"""
        key = f"processing:{processing_id}:status"
        status_data = self.ctx_mgr._get_json(key, default={})
        
        if not status_data:
            return {"status": "not_found"}
            
        return status_data
    
    async def get_processing_result(self, processing_id: str) -> Optional[Dict[str, Any]]:
        """Get completed processing result"""
        key = f"processing:{processing_id}:result"
        result_data = self.ctx_mgr._get_json(key, default=None)
        
        if not result_data:
            return None
            
        return result_data
    
    async def get_products_for_flow(self, processing_id: str) -> List[Dict[str, Any]]:
        """
        Get products formatted for Flow consumption.
        This is what onboarding_flow.py will call.
        """
        result = await self.get_processing_result(processing_id)
        if not result:
            return []
            
        # Extract products from the stored result
        products = result.get("flow_data", {}).get("products", [])
        return products
    
    async def get_text_summary_for_flow(self, processing_id: str) -> str:
        """
        Get text summary for Flow display.
        This allows dumping everything as text in the Flow.
        """
        result = await self.get_processing_result(processing_id)
        if not result:
            return "No results available."
            
        # Extract text content
        text_content = result.get("text_content", "")
        sections = result.get("sections", {})
        
        # Combine text and sections into a comprehensive summary
        full_text = text_content
        
        if sections:
            full_text += "\n\n" + self._format_sections_as_text(sections)
            
        return full_text or "Results processed successfully."
    
    async def _store_processing_result(
        self, 
        processing_id: str, 
        result, 
        original_query: str,
        user_id: str,
        session_id: str
    ) -> None:
        """Store processing result in Redis (safely handles both response types)"""
        
        # Safely extract data from both BotResponse and EnhancedBotResponse
        products_data = []
        text_content = ""
        sections = {}
        response_type = "final_answer"
        functions_executed = []
        requires_flow = False
        
        try:
            # Get basic attributes that both response types should have
            response_type = getattr(result, 'response_type', 'final_answer')
            if hasattr(response_type, 'value'):
                response_type = response_type.value
                
            # Get content
            content = getattr(result, 'content', {})
            text_content = content.get("message", "") if isinstance(content, dict) else str(content)
            sections = content.get("sections", {}) if isinstance(content, dict) else {}
            
            # Get functions executed
            functions_executed = getattr(result, 'functions_executed', [])
            
            # Check if it's an EnhancedBotResponse with flow capabilities
            if hasattr(result, 'requires_flow'):
                requires_flow = getattr(result, 'requires_flow', False)
                
                # Try to extract products from flow_payload if available
                if requires_flow and hasattr(result, 'flow_payload') and result.flow_payload:
                    flow_payload = result.flow_payload
                    if hasattr(flow_payload, 'products') and flow_payload.products:
                        products_data = [
                            {
                                "id": getattr(p, 'product_id', f"prod_{i}"),
                                "title": getattr(p, 'title', 'Product'),
                                "subtitle": getattr(p, 'subtitle', ''),
                                "price": getattr(p, 'price', 'Price on request'),
                                "brand": getattr(p, 'brand', ''),
                                "rating": getattr(p, 'rating', None),
                                "availability": getattr(p, 'availability', 'In Stock'),
                                "discount": getattr(p, 'discount', ''),
                                "image": getattr(p, 'image_url', 'https://via.placeholder.com/200x200?text=Product'),
                                "features": getattr(p, 'key_features', [])
                            }
                            for i, p in enumerate(flow_payload.products)
                        ]
                        
        except Exception as e:
            log.warning(f"Error extracting enhanced response data: {e}")
            # Fall back to basic extraction
            
        # If no products from flow_payload, try to create dummy products from text
        if not products_data and text_content:
            products_data = self._create_dummy_products_from_text(text_content)
        
        # Store comprehensive result
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
            }
        }
        
        # Store in Redis with TTL
        result_key = f"processing:{processing_id}:result"
        self.ctx_mgr._set_json(result_key, result_data, ttl=self.processing_ttl)
        
        # Update status to completed
        await self._set_processing_status(processing_id, "completed", {
            "products_count": len(products_data),
            "has_flow": requires_flow,
            "text_length": len(text_content)
        })
        
        # ✅ Enhanced notification with flow_data
        notifier = FrontendNotifier()
        await notifier.notify_completion(processing_id, "completed", user_id, {
            "query": original_query,
            "session_id": session_id,
            "flow_data": result_data["flow_data"],  # ← Enhanced with flow data
            "has_products": len(products_data) > 0,  # ← Flag for frontend
            "has_flow_data": len(products_data) > 0 or bool(text_content)  # ← Overall flag
        })
    
    def _create_dummy_products_from_text(self, text_content: str) -> List[Dict[str, Any]]:
        """Create dummy products based on query analysis"""
        
        # Simple keyword-based product generation
        text_lower = text_content.lower()
        
        if any(word in text_lower for word in ["laptop", "computer", "gaming"]):
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
                    "features": ["High Performance", "Good Value", "Recommended Choice"]
                }
            ]
        elif any(word in text_lower for word in ["phone", "mobile", "smartphone"]):
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
                    "features": ["Latest Features", "Great Camera", "Long Battery Life"]
                }
            ]
        else:
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
                    "features": ["Quality Product", "Good Value", "Recommended"]
                }
            ]
    
    async def _set_processing_status(
        self, 
        processing_id: str, 
        status: str, 
        metadata: Dict[str, Any]
    ) -> None:
        """Set processing status in Redis"""
        status_data = {
            "processing_id": processing_id,
            "status": status,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata
        }
        
        status_key = f"processing:{processing_id}:status"
        self.ctx_mgr._set_json(status_key, status_data, ttl=self.processing_ttl)
    
    def _format_sections_as_text(self, sections: Dict[str, str]) -> str:
        """Format sections dictionary as readable text"""
        formatted_text = ""
        
        # Order sections logically
        section_order = ["MAIN", "ALT", "+", "INFO", "TIPS", "LINKS"]
        
        for section_key in section_order:
            if section_key in sections and sections[section_key].strip():
                content = sections[section_key].strip()
                
                # Add section headers
                if section_key == "MAIN":
                    formatted_text += f"Main Information:\n{content}\n\n"
                elif section_key == "ALT":
                    formatted_text += f"Alternative Options:\n{content}\n\n"
                elif section_key == "+":
                    formatted_text += f"Additional Benefits:\n{content}\n\n"
                elif section_key == "INFO":
                    formatted_text += f"Important Information:\n{content}\n\n"
                elif section_key == "TIPS":
                    formatted_text += f"Tips & Recommendations:\n{content}\n\n"
                elif section_key == "LINKS":
                    formatted_text += f"Useful Links:\n{content}\n\n"
        
        return formatted_text.strip()


class FrontendNotifier:
    """Service for notifying frontend when background processing completes"""
    
    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or getattr(Cfg, 'FRONTEND_WEBHOOK_URL', None)
    
    async def notify_completion(
        self, 
        processing_id: str, 
        status: str, 
        user_id: str,
        additional_data: Dict[str, Any] = None
    ) -> bool:
        """Notify frontend that processing is complete - triggers flow button"""
        
        # Check if we have products for flow
        has_flow_data = False
        flow_type = "text_summary"
        
        if additional_data:
            has_flow_data = additional_data.get("has_flow_data", False)
            if additional_data.get("has_products", False):
                flow_type = "product_recommendations"
        
        notification_payload = {
            "processing_id": processing_id,
            "status": status,
            "user_id": user_id,
            "timestamp": datetime.now().isoformat(),
            "action": "show_flow_button",  # This tells frontend to show flow button
            "has_flow_data": has_flow_data,
            "flow_type": flow_type,
            "webhook_url": self.webhook_url,  # Include for debugging
            "data": additional_data or {}
        }
        
        if self.webhook_url:
            try:
                # Send actual HTTP request to frontend
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        self.webhook_url,
                        json=notification_payload,
                        headers={'Content-Type': 'application/json'},
                        timeout=10  # 10 second timeout
                    ) as response:
                        response_text = await response.text()
                        
                        if response.status == 200:
                            log.info(f"✅ Flow button notification sent successfully to {self.webhook_url}")
                            log.info(f"Frontend response: {response_text}")
                            return True
                        else:
                            log.warning(f"⚠️ Flow button notification failed: {response.status} - {response_text}")
                            return False
                            
            except aiohttp.ClientTimeout:
                log.error(f"❌ Frontend notification timeout to {self.webhook_url}")
                return False
            except Exception as e:
                log.error(f"❌ Failed to send flow button notification: {e}")
                return False
        else:
            # No webhook configured, just log
            log.warning("⚠️ No FRONTEND_WEBHOOK_URL configured - notification not sent")
            log.info(f"Would send notification: {notification_payload}")
            return False