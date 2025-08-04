"""
WhatsApp Flow Template Generator
──────────────────────────────────
Create this as shopping_bot/flow_generator.py
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from .models import FlowType, ProductData, FlowPayload

log = logging.getLogger(__name__)

class FlowTemplateGenerator:
    """Generates WhatsApp Flow templates with dynamic data injection"""
    
    def generate_product_catalog_flow(
        self, 
        products: List[ProductData], 
        query_context: str = "",
        header_text: str = "Product Options"
    ) -> FlowPayload:
        """Generate product catalog Flow"""
        
        # Limit products to avoid Flow size limits
        limited_products = products[:6]  # WhatsApp Flow best practices
        
        footer_text = f"Found {len(products)} options" if len(products) > len(limited_products) else None
        
        return FlowPayload(
            flow_type=FlowType.PRODUCT_CATALOG,
            products=limited_products,
            header_text=header_text,
            footer_text=footer_text,
            action_buttons=[
                {"label": "View Details", "action": "view_details"},
                {"label": "Compare", "action": "compare"},
                {"label": "Ask More", "action": "ask_followup"}
            ]
        )
    
    def generate_comparison_flow(
        self,
        products: List[ProductData],
        comparison_criteria: List[str],
        header_text: str = "Product Comparison"
    ) -> FlowPayload:
        """Generate product comparison Flow"""
        
        # Limit to 3 products for comparison
        comparison_products = products[:3]
        
        return FlowPayload(
            flow_type=FlowType.COMPARISON,
            products=comparison_products,
            header_text=header_text,
            footer_text="Side-by-side comparison",
            action_buttons=[
                {"label": "Select Winner", "action": "select_product"},
                {"label": "See More Options", "action": "see_more"},
                {"label": "Modify Criteria", "action": "modify_criteria"}
            ]
        )
    
    def generate_recommendation_flow(
        self,
        products: List[ProductData],
        recommendation_reason: str,
        header_text: str = "Recommended For You"
    ) -> FlowPayload:
        """Generate personalized recommendation Flow"""
        
        # Take top 4 recommendations
        top_products = products[:4]
        
        return FlowPayload(
            flow_type=FlowType.RECOMMENDATION,
            products=top_products,
            header_text=header_text,
            footer_text=f"Based on: {recommendation_reason}",
            action_buttons=[
                {"label": "Choose This", "action": "select_recommended"},
                {"label": "See Alternatives", "action": "see_alternatives"},
                {"label": "Explain Why", "action": "explain_recommendation"}
            ]
        )
    
    def create_flow_from_sections(self, sections: Dict[str, str]) -> Optional[FlowPayload]:
        """Create Flow from Flean's 6-element sections if suitable"""
        
        # Check if ALT section has structured product data
        alt_section = sections.get("ALT", "").strip()
        if not alt_section:
            return None
            
        # Try to extract product data from ALT section
        products = self._extract_products_from_alt_section(alt_section)
        if not products:
            return None
            
        # Determine appropriate Flow type
        plus_section = sections.get("+", "")
        if "recommend" in plus_section.lower():
            flow_type = FlowType.RECOMMENDATION
            header = "Recommended Options"
        elif "compare" in plus_section.lower():
            flow_type = FlowType.COMPARISON
            header = "Compare These Options"
        else:
            flow_type = FlowType.PRODUCT_CATALOG
            header = "Alternative Options"
            
        return FlowPayload(
            flow_type=flow_type,
            products=products,
            header_text=header,
            footer_text=sections.get("INFO", "").strip()[:50] + "..." if sections.get("INFO") else None
        )
    
    def _extract_products_from_alt_section(self, alt_text: str) -> List[ProductData]:
        """Extract structured product data from ALT section text"""
        products = []
        
        # Parse product information from formatted text
        lines = alt_text.split('\n')
        current_product = {}
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            # Look for product indicators (bullets, dashes, numbers)
            if re.match(r'^[•\-*\d+\.]\s*', line):
                # Save previous product if exists
                if current_product.get('title'):
                    product = self._create_product_from_dict(current_product)
                    if product:
                        products.append(product)
                
                # Start new product
                clean_title = re.sub(r'^[•\-*\d+\.]\s*', '', line).strip()
                current_product = {'title': clean_title}
                
            elif ':' in line and current_product.get('title'):
                # Extract key-value pairs
                key, value = line.split(':', 1)
                key = key.strip().lower()
                value = value.strip()
                
                if 'price' in key or '$' in value or '₹' in value:
                    current_product['price'] = value
                elif 'rating' in key or 'star' in key:
                    try:
                        # Extract rating number
                        rating_match = re.search(r'(\d+\.?\d*)', value)
                        if rating_match:
                            current_product['rating'] = float(rating_match.group(1))
                    except:
                        pass
                elif 'brand' in key:
                    current_product['brand'] = value
                elif any(word in key for word in ['feature', 'benefit', 'spec', 'highlight']):
                    features = current_product.get('features', [])
                    features.append(value)
                    current_product['features'] = features
        
        # Don't forget the last product
        if current_product.get('title'):
            product = self._create_product_from_dict(current_product)
            if product:
                products.append(product)
        
        return products[:6]  # Limit to 6 products
    
    def _create_product_from_dict(self, product_dict: Dict[str, Any]) -> Optional[ProductData]:
        """Create ProductData from dictionary"""
        try:
            title = product_dict.get('title', '').strip()
            if not title:
                return None
                
            return ProductData(
                product_id=f"prod_{hash(title)%100000}",
                title=title,
                subtitle=product_dict.get('subtitle', product_dict.get('brand', '')),
                image_url=product_dict.get('image_url', 'https://via.placeholder.com/200x200?text=Product'),
                price=product_dict.get('price', 'Price on request'),
                rating=product_dict.get('rating'),
                brand=product_dict.get('brand'),
                key_features=product_dict.get('features', []),
                availability='In Stock',
                discount=product_dict.get('discount')
            )
        except Exception as e:
            log.warning(f"Failed to create product from dict: {e}")
            return None
    
    def validate_flow_payload(self, payload: FlowPayload) -> bool:
        """Validate Flow payload meets WhatsApp requirements"""
        try:
            # Basic validation
            if not payload.products:
                return False
                
            if len(payload.products) > 10:  # WhatsApp limit
                return False
                
            # Validate product data
            for product in payload.products:
                if not product.title or not product.product_id:
                    return False
                if len(product.title) > 100:  # WhatsApp title limit
                    return False
                    
            return True
            
        except Exception as e:
            log.error(f"Flow validation error: {e}")
            return False