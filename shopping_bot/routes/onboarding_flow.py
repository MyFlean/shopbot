from flask import Blueprint, request, jsonify, current_app
import logging
import json
import base64
import os
import asyncio
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding as asympad
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from pathlib import Path

bp = Blueprint("flow_handler_enhanced", __name__)
log = logging.getLogger(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)

# Load RSA private key - same as original
_key_path = Path(os.getenv("FLOW_PRIVATE_KEY", "/secrets/Flow_Private_Key"))
if not _key_path.exists():
    alt_path = Path(__file__).resolve().parent / "private.pem"
    if alt_path.exists():
        _key_path = alt_path
    else:
        log.warning(f"Private key not found at {_key_path}")
        _private_key = None

if _key_path.exists():
    with open(_key_path, 'rb') as key_file:
        _private_key = serialization.load_pem_private_key(
            key_file.read(), 
            password=None,
            backend=default_backend()
        )
else:
    _private_key = None

def _rsa_decrypt(encrypted_aes_key_b64: str) -> bytes:
    """Decrypt RSA encrypted AES key using OAEP with SHA256."""
    if not _private_key:
        raise RuntimeError("Private key not available")
        
    encrypted_aes_key = base64.b64decode(encrypted_aes_key_b64)
    try:
        return _private_key.decrypt(
            encrypted_aes_key,
            asympad.OAEP(
                mgf=asympad.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )
    except Exception as e:
        log.error(f"RSA decryption failed: {e}")
        raise

def flip_iv(iv: bytes) -> bytes:
    """Flip all bits in IV for GCM mode response encryption."""
    return bytes(byte ^ 0xFF for byte in iv)

def _aes_gcm_decrypt(encrypted_flow_data_b64: str, aes_key: bytes, initial_vector_b64: str) -> str:
    """Decrypt using AES-GCM mode."""
    try:
        encrypted_flow_data = base64.b64decode(encrypted_flow_data_b64)
        iv = base64.b64decode(initial_vector_b64)
        
        encrypted_body = encrypted_flow_data[:-16]
        auth_tag = encrypted_flow_data[-16:]
        
        cipher = Cipher(
            algorithms.AES(aes_key),
            modes.GCM(iv, auth_tag),
            backend=default_backend()
        )
        
        decryptor = cipher.decryptor()
        decrypted_data = decryptor.update(encrypted_body) + decryptor.finalize()
        
        return decrypted_data.decode('utf-8')
        
    except Exception as e:
        log.error(f"AES-GCM decryption error: {e}")
        raise

def _aes_gcm_encrypt(response: str, aes_key: bytes, initial_vector_b64: str) -> str:
    """Encrypt response using AES-GCM mode with flipped IV."""
    try:
        iv = base64.b64decode(initial_vector_b64)
        flipped_iv = flip_iv(iv)
        
        cipher = Cipher(
            algorithms.AES(aes_key),
            modes.GCM(flipped_iv),
            backend=default_backend()
        )
        
        encryptor = cipher.encryptor()
        encrypted = encryptor.update(response.encode('utf-8')) + encryptor.finalize()
        
        encrypted_response = encrypted + encryptor.tag
        
        return base64.b64encode(encrypted_response).decode('utf-8')
        
    except Exception as e:
        log.error(f"AES-GCM encryption error: {e}")
        raise

# Static dropdown data for onboarding flow
ONBOARDING_DATA = {
    "societies": [
        {"id": "amrapali_sapphire", "title": "Amrapali Sapphire"},
        {"id": "parsvnath_prestige", "title": "Parsvnath Prestige"},
        {"id": "other", "title": "Other"},
    ],
    "genders": [
        {"id": "male", "title": "Male"},
        {"id": "female", "title": "Female"},
        {"id": "other", "title": "Other"},
        {"id": "prefer_not_to_say", "title": "Prefer not to say"},
    ],
    "age_groups": [
        {"id": "18_24", "title": "18-24 years"},
        {"id": "25_34", "title": "25-34 years"},
        {"id": "35_44", "title": "35-44 years"},
        {"id": "45_54", "title": "45-54 years"},
        {"id": "55_64", "title": "55-64 years"},
        {"id": "65_plus", "title": "65+ years"},
    ],
    "show_custom_society": False
}

def get_dummy_products():
    """Fallback dummy products if no background processing results"""
    return [
        {
            "id": "prod_90459",
            "title": "ASUS TUF Gaming A15",
            "subtitle": "AMD Ryzen 5 7535HS, RTX 3050",
            "price": "$699",
            "brand": "ASUS",
            "rating": 4.2,
            "availability": "In Stock",
            "discount": "Save $100",
            "image": "https://via.placeholder.com/150x150/4CAF50/FFFFFF?text=ASUS",
            "features": ["15.6\" 144Hz Display", "16GB RAM", "512GB SSD", "RTX 3050 Graphics"]
        },
        {
            "id": "prod_97199", 
            "title": "Acer Nitro 5",
            "subtitle": "Intel i5-12500H, RTX 3050 Ti",
            "price": "$799",
            "brand": "Acer",
            "rating": 4.1,
            "availability": "In Stock",
            "discount": "",
            "image": "https://via.placeholder.com/150x150/FF5722/FFFFFF?text=Acer",
            "features": ["15.6\" 144Hz IPS", "16GB RAM", "512GB NVMe", "RTX 3050 Ti"]
        },
        {
            "id": "prod_84521",
            "title": "HP Pavilion Gaming",
            "subtitle": "AMD Ryzen 7 5800H, GTX 1650",
            "price": "$649",
            "brand": "HP",
            "rating": 3.9,
            "availability": "In Stock", 
            "discount": "Save $50",
            "image": "https://via.placeholder.com/150x150/9C27B0/FFFFFF?text=HP",
            "features": ["15.6\" FHD Display", "8GB RAM", "256GB SSD", "GTX 1650 Graphics"]
        }
    ]

def get_product_by_id(product_id: str, products_list: list = None):
    """Get specific product details by ID"""
    if products_list is None:
        products_list = get_dummy_products()
        
    for product in products_list:
        if product["id"] == product_id:
            return product
    return None

def handle_onboarding_flow(payload, version="7.2"):
    """Handle onboarding flow actions"""
    action = payload.get("action", "")
    screen = payload.get("screen", "")
    data = payload.get("data", {})
    
    log.info(f"Onboarding flow - Action: {action}, Screen: {screen}")
    
    if action.upper() == "INIT":
        return {
            "version": version,
            "screen": "ONBOARDING",
            "data": ONBOARDING_DATA
        }
    
    elif action.upper() == "DATA_EXCHANGE":
        log.info(f"Onboarding data exchange: {data}")
        
        if data.get("selected_society") == "other":
            onboarding_data_with_custom = ONBOARDING_DATA.copy()
            onboarding_data_with_custom["show_custom_society"] = True
            return {
                "version": version,
                "screen": "ONBOARDING", 
                "data": onboarding_data_with_custom
            }
        
        # Validate complete form submission
        errors = {}
        
        if not data.get("society"):
            errors["society"] = "Please select your society"
        elif data.get("society") == "other" and not data.get("custom_society", "").strip():
            errors["custom_society"] = "Please enter your society name"
        
        if not data.get("gender"):
            errors["gender"] = "Please select your gender"
        
        if not data.get("age_group"):
            errors["age_group"] = "Please select your age group"
        
        if errors:
            return {
                "version": version,
                "screen": "ONBOARDING",
                "data": {
                    **ONBOARDING_DATA,
                    "error": errors
                }
            }
        else:
            log.info(f"Onboarding completed successfully with data: {data}")
            return {
                "version": version,
                "screen": "COMPLETE",
                "data": {}
            }

def handle_product_recommendation_flow(payload, version="7.2"):
    """Handle product recommendation flow that can use background processing results"""
    action = payload.get("action", "")
    screen = payload.get("screen", "")
    data = payload.get("data", {})
    
    log.info(f"Product flow - Action: {action}, Screen: {screen}")
    
    # For now, just use dummy products
    processing_id = data.get("processing_id") or payload.get("processing_id")
    
    if action.upper() == "INIT":
        products = get_dummy_products()
        header_text = "Product Recommendations"
        footer_text = "Select a product to view details"
        
        if processing_id:
            header_text = "Your Personalized Results"
            footer_text = f"Results from processing: {processing_id[:8]}..."
        
        product_options = [
            {"id": product["id"], "title": product["title"]} 
            for product in products
        ]
        
        return {
            "version": version,
            "screen": "PRODUCT_LIST",
            "data": {
                "products": products,
                "product_options": product_options,
                "header_text": header_text,
                "footer_text": footer_text,
                "selected_product_id": "",
                "processing_id": processing_id
            }
        }
    
    elif action.upper() == "DATA_EXCHANGE":
        if screen == "PRODUCT_LIST":
            selected_product_id = data.get("selected_product_id") or data.get("selected_product")
            log.info(f"Product selected: {selected_product_id}")
            
            products = get_dummy_products()
            product_options = [
                {"id": product["id"], "title": product["title"]} 
                for product in products
            ]
            
            return {
                "version": version,
                "screen": "PRODUCT_LIST",
                "data": {
                    "products": products,
                    "product_options": product_options,
                    "header_text": "Product Recommendations",
                    "footer_text": "Select a product to view details",
                    "selected_product_id": selected_product_id or "",
                    "processing_id": processing_id
                }
            }
    
    elif action.upper() == "NAVIGATE":
        if payload.get("next", {}).get("name") == "PRODUCT_DETAILS":
            product_id = data.get("product_id") or data.get("selected_product_id")
            log.info(f"Navigating to details for product: {product_id}")
            
            if product_id:
                products = get_dummy_products()
                product = get_product_by_id(product_id, products)
                
                if product:
                    details_text = f"""
{product['title']}
{product['subtitle']}

Price: {product['price']}
Brand: {product['brand']}
Rating: {product['rating']}/5.0
Status: {product['availability']}

Features:
{chr(10).join(f"• {feature}" for feature in product.get('features', []))}

{product.get('discount', '')}
                    """.strip()
                    
                    return {
                        "version": version,
                        "screen": "PRODUCT_DETAILS",
                        "data": {
                            "product_id": product_id,
                            "product_details": details_text,
                            "processing_id": processing_id
                        }
                    }
            
            return {
                "version": version,
                "screen": "PRODUCT_DETAILS", 
                "data": {
                    "product_id": "unknown",
                    "product_details": "Product details not available.",
                    "processing_id": processing_id
                }
            }
    
    # Default fallback
    products = get_dummy_products()
    product_options = [
        {"id": product["id"], "title": product["title"]} 
        for product in products
    ]
    
    return {
        "version": version,
        "screen": "PRODUCT_LIST",
        "data": {
            "products": products,
            "product_options": product_options,
            "header_text": "Product Recommendations",
            "footer_text": "Select a product to view details",
            "selected_product_id": "",
            "processing_id": processing_id
        }
    }

def handle_product_recommendations_flow(payload, version="7.2"):
    """Handle NEW product recommendations flow using background processing results"""
    action = payload.get("action", "")
    screen = payload.get("screen", "")
    data = payload.get("data", {})
    
    log.info(f"Product recommendations flow - Action: {action}, Screen: {screen}")
    
    # Get processing_id from payload
    processing_id = data.get("processing_id") or payload.get("processing_id")
    
    if not processing_id:
        log.error("No processing_id provided for product recommendations flow")
        return {
            "version": version,
            "screen": "COMPLETED",
            "data": {
                "message": "Sorry, no results available."
            }
        }
    
    if action.upper() == "INIT":
        # Get products from background processing results
        background_processor = current_app.extensions.get("background_processor")
        
        if background_processor:
            try:
                # Get products from completed background processing
                products = asyncio.run(background_processor.get_products_for_flow(processing_id))
                
                if products:
                    product_options = [
                        {"id": product["id"], "title": product["title"]} 
                        for product in products
                    ]
                    
                    return {
                        "version": version,
                        "screen": "PRODUCT_LIST",
                        "data": {
                            "products": products,
                            "product_options": product_options,
                            "header_text": "Recommended Products for You",
                            "footer_text": f"Found {len(products)} great options",
                            "selected_product_id": "",
                            "processing_id": processing_id
                        }
                    }
                else:
                    # No products found, show text summary instead
                    text_summary = asyncio.run(background_processor.get_text_summary_for_flow(processing_id))
                    return {
                        "version": version,
                        "screen": "COMPLETED",
                        "data": {
                            "message": text_summary[:500] + "..." if len(text_summary) > 500 else text_summary
                        }
                    }
                    
            except Exception as e:
                log.error(f"Failed to get products for flow: {e}")
        
        # Fallback to dummy products
        products = get_dummy_products()
        product_options = [
            {"id": product["id"], "title": product["title"]} 
            for product in products
        ]
        
        return {
            "version": version,
            "screen": "PRODUCT_LIST",
            "data": {
                "products": products,
                "product_options": product_options,
                "header_text": "Product Recommendations",
                "footer_text": "Select a product to view details",
                "selected_product_id": "",
                "processing_id": processing_id
            }
        }
    
    elif action.upper() == "DATA_EXCHANGE":
        if screen == "PRODUCT_LIST":
            selected_product_id = data.get("selected_product_id") or data.get("selected_product")
            log.info(f"Product selected: {selected_product_id}")
            
            # Get current products (same logic as INIT)
            background_processor = current_app.extensions.get("background_processor")
            products = []
            
            if background_processor:
                try:
                    products = asyncio.run(background_processor.get_products_for_flow(processing_id))
                except Exception as e:
                    log.error(f"Failed to get products for data exchange: {e}")
            
            if not products:
                products = get_dummy_products()
            
            product_options = [
                {"id": product["id"], "title": product["title"]} 
                for product in products
            ]
            
            return {
                "version": version,
                "screen": "PRODUCT_LIST",
                "data": {
                    "products": products,
                    "product_options": product_options,
                    "header_text": "Product Recommendations",
                    "footer_text": "Select a product to view details",
                    "selected_product_id": selected_product_id or "",
                    "processing_id": processing_id
                }
            }
    
    elif action.upper() == "NAVIGATE":
        if payload.get("next", {}).get("name") == "PRODUCT_DETAILS":
            product_id = data.get("product_id") or data.get("selected_product_id")
            log.info(f"Navigating to details for product: {product_id}")
            
            if product_id:
                # Get products and find the selected one
                background_processor = current_app.extensions.get("background_processor")
                products = []
                
                if background_processor:
                    try:
                        products = asyncio.run(background_processor.get_products_for_flow(processing_id))
                    except Exception as e:
                        log.error(f"Failed to get products for navigation: {e}")
                
                if not products:
                    products = get_dummy_products()
                
                product = get_product_by_id(product_id, products)
                
                if product:
                    details_text = f"""
{product['title']}
{product['subtitle']}

Price: {product['price']}
Brand: {product['brand']}
Rating: {product.get('rating', 'N/A')}/5.0
Status: {product['availability']}

Features:
{chr(10).join(f"• {feature}" for feature in product.get('features', []))}

{product.get('discount', '')}
                    """.strip()
                    
                    return {
                        "version": version,
                        "screen": "PRODUCT_DETAILS",
                        "data": {
                            "product_id": product_id,
                            "product_details": details_text,
                            "processing_id": processing_id
                        }
                    }
            
            return {
                "version": version,
                "screen": "PRODUCT_DETAILS", 
                "data": {
                    "product_id": "unknown",
                    "product_details": "Product details not available.",
                    "processing_id": processing_id
                }
            }
    
    # Default fallback
    return {
        "version": version,
        "screen": "COMPLETED",
        "data": {
            "message": "Thank you for using our product recommendations!"
        }
    }

@bp.post("/flow/onboarding")
def onboarding_flow():
    """Handle onboarding WhatsApp Flow requests."""
    return handle_flow_request("onboarding")

@bp.post("/flow/products")
def product_flow():
    """Handle product recommendation WhatsApp Flow requests."""
    return handle_flow_request("products")

@bp.post("/flow/product_recommendations")
def product_recommendations_flow():
    """Handle NEW product recommendations WhatsApp Flow requests."""
    return handle_flow_request("product_recommendations")

def handle_flow_request(flow_type):
    """Unified flow request handler for onboarding, products, and product_recommendations."""
    try:
        raw = request.get_json(silent=True)
        
        # Health check
        if not raw or raw == {}:
            log.info(f"Health check received for {flow_type}")
            return "", 200
        
        log.info(f"Received {flow_type} request with keys: {list(raw.keys())}")
        
        # Check if data is encrypted
        is_encrypted = "encrypted_flow_data" in raw
        aes_key = None
        
        if is_encrypted and _private_key:
            log.info("Processing encrypted request")
            try:
                encrypted_aes_key = raw.get("encrypted_aes_key", "")
                aes_key = _rsa_decrypt(encrypted_aes_key)
                
                encrypted_flow_data = raw.get("encrypted_flow_data", "")
                initial_vector = raw.get("initial_vector", "")
                
                decrypted_json = _aes_gcm_decrypt(encrypted_flow_data, aes_key, initial_vector)
                payload = json.loads(decrypted_json)
                
            except Exception as exc:
                log.exception(f"Decryption failed: {exc}")
                return jsonify({"error_type": "DECRYPTION_FAILED"}), 421
        else:
            log.warning("Received unencrypted flow data or no private key")
            payload = raw
        
        action = payload.get("action", "")
        version = payload.get("version", "7.2")
        
        log.info(f"Processing {flow_type} - action: {action}")
        
        if action.lower() == "ping":
            resp_obj = {
                "version": version,
                "data": {"status": "active"}
            }
        else:
            if flow_type == "onboarding":
                resp_obj = handle_onboarding_flow(payload, version)
            elif flow_type == "products":
                resp_obj = handle_product_recommendation_flow(payload, version)
            elif flow_type == "product_recommendations":
                resp_obj = handle_product_recommendations_flow(payload, version)
            else:
                return jsonify({"error_type": "UNKNOWN_FLOW_TYPE"}), 422
        
        # Encrypt response if needed
        if is_encrypted and aes_key:
            response_json = json.dumps(resp_obj)
            encrypted_response = _aes_gcm_encrypt(response_json, aes_key, raw["initial_vector"])
            return encrypted_response, 200, {'Content-Type': 'text/plain'}
        else:
            return jsonify(resp_obj), 200
            
    except Exception as e:
        log.exception(f"Unexpected error in {flow_type} flow endpoint: {e}")
        return jsonify({"error_type": "INTERNAL_ERROR"}), 500

# Health check endpoints
@bp.get("/flow/health")
def health_check():
    """Simple health check endpoint for testing."""
    return jsonify({"status": "healthy", "endpoints": ["onboarding", "products", "product_recommendations"]}), 200

@bp.get("/flow/onboarding/health")
def onboarding_health():
    """Health check for onboarding flow."""
    return jsonify({"status": "healthy", "flow": "onboarding"}), 200

@bp.get("/flow/products/health") 
def products_health():
    """Health check for products flow."""
    return jsonify({"status": "healthy", "flow": "products"}), 200

@bp.get("/flow/product_recommendations/health") 
def product_recommendations_health():
    """Health check for product recommendations flow."""
    return jsonify({"status": "healthy", "flow": "product_recommendations"}), 200