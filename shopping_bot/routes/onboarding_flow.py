from __future__ import annotations

from flask import Blueprint, request, jsonify, current_app
import logging
import json
import base64
import os
from typing import Any, Dict, List, Optional
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding as asympad
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from pathlib import Path

bp = Blueprint("flow_handler_enhanced", __name__)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# RSA private key loading (for encrypted Flow payloads)
# ─────────────────────────────────────────────────────────────
_private_key = None
_key_path = Path(os.getenv("FLOW_PRIVATE_KEY", "/secrets/Flow_Private_Key"))
if not _key_path.exists():
    alt_path = Path(__file__).resolve().parent / "private.pem"
    if alt_path.exists():
        _key_path = alt_path

if _key_path.exists():
    try:
        with open(_key_path, "rb") as key_file:
            _private_key = serialization.load_pem_private_key(
                key_file.read(), password=None, backend=default_backend()
            )
    except Exception as e:
        log.warning(f"Failed to load private key from '{_key_path}': {e}")
else:
    log.warning(f"Private key not found at {_key_path}. Will accept unencrypted Flow payloads only.")

# ─────────────────────────────────────────────────────────────
# Crypto helpers
# ─────────────────────────────────────────────────────────────
def _rsa_decrypt(encrypted_aes_key_b64: str) -> bytes:
    """Decrypt RSA-encrypted AES key using OAEP(SHA-256)."""
    if not _private_key:
        raise RuntimeError("Private key not available")
    encrypted_aes_key = base64.b64decode(encrypted_aes_key_b64)
    return _private_key.decrypt(
        encrypted_aes_key,
        asympad.OAEP(mgf=asympad.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )

def flip_iv(iv: bytes) -> bytes:
    """Flip all bits in IV for GCM response encryption (per WA requirement)."""
    return bytes(b ^ 0xFF for b in iv)

def _aes_gcm_decrypt(encrypted_flow_data_b64: str, aes_key: bytes, initial_vector_b64: str) -> str:
    """Decrypt using AES-GCM."""
    encrypted_flow_data = base64.b64decode(encrypted_flow_data_b64)
    iv = base64.b64decode(initial_vector_b64)
    encrypted_body = encrypted_flow_data[:-16]
    auth_tag = encrypted_flow_data[-16:]
    cipher = Cipher(algorithms.AES(aes_key), modes.GCM(iv, auth_tag), backend=default_backend())
    decryptor = cipher.decryptor()
    return (decryptor.update(encrypted_body) + decryptor.finalize()).decode("utf-8")

def _aes_gcm_encrypt(response: str, aes_key: bytes, initial_vector_b64: str) -> str:
    """Encrypt response using AES-GCM with flipped IV."""
    iv = base64.b64decode(initial_vector_b64)
    flipped_iv = flip_iv(iv)
    cipher = Cipher(algorithms.AES(aes_key), modes.GCM(flipped_iv), backend=default_backend())
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(response.encode("utf-8")) + encryptor.finalize()
    encrypted_response = encrypted + encryptor.tag
    return base64.b64encode(encrypted_response).decode("utf-8")

# ─────────────────────────────────────────────────────────────
# Static data and utilities
# ─────────────────────────────────────────────────────────────
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
    "show_custom_society": False,
}

def get_dummy_products() -> List[Dict[str, Any]]:
    """Fallback dummy products if no background results."""
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
            "features": ["15.6\" 144Hz Display", "16GB RAM", "512GB SSD", "RTX 3050 Graphics"],
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
            "features": ["15.6\" 144Hz IPS", "16GB RAM", "512GB NVMe", "RTX 3050 Ti"],
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
            "features": ["15.6\" FHD Display", "8GB RAM", "256GB SSD", "GTX 1650 Graphics"],
        },
    ]

def get_product_by_id(product_id: str, products_list: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """Get specific product details by ID."""
    products_list = products_list or get_dummy_products()
    for product in products_list:
        if product["id"] == product_id:
            return product
    return None

# ─────────────────────────────────────────────────────────────
# Onboarding flow (sync)
# ─────────────────────────────────────────────────────────────
def handle_onboarding_flow(payload: Dict[str, Any], version: str = "7.2") -> Dict[str, Any]:
    action = payload.get("action", "")
    screen = payload.get("screen", "")
    data = payload.get("data", {}) or {}

    log.info(f"Onboarding flow - Action: {action}, Screen: {screen}")

    if action.upper() == "INIT":
        return {"version": version, "screen": "ONBOARDING", "data": ONBOARDING_DATA}

    if action.upper() == "DATA_EXCHANGE":
        log.info(f"Onboarding data exchange: {data}")

        # Unify society keys: prefer `society`, accept `selected_society` as alias
        society_value = data.get("society") or data.get("selected_society")

        if society_value == "other":
            onboarding_data_with_custom = dict(ONBOARDING_DATA)
            onboarding_data_with_custom["show_custom_society"] = True
            return {"version": version, "screen": "ONBOARDING", "data": onboarding_data_with_custom}

        # Validate complete form submission
        errors: Dict[str, str] = {}

        if not society_value:
            errors["society"] = "Please select your society"
        elif society_value == "other" and not data.get("custom_society", "").strip():
            errors["custom_society"] = "Please enter your society name"

        if not data.get("gender"):
            errors["gender"] = "Please select your gender"

        if not data.get("age_group"):
            errors["age_group"] = "Please select your age group"

        if errors:
            merged = dict(ONBOARDING_DATA)
            merged["error"] = errors
            return {"version": version, "screen": "ONBOARDING", "data": merged}

        log.info(f"Onboarding completed successfully with data: {data}")
        return {"version": version, "screen": "COMPLETE", "data": {}}

    # Default
    return {"version": version, "screen": "ONBOARDING", "data": ONBOARDING_DATA}

# ─────────────────────────────────────────────────────────────
# Product recommendation (dummy products; sync)
# ─────────────────────────────────────────────────────────────
def handle_product_recommendation_flow(payload: Dict[str, Any], version: str = "7.2") -> Dict[str, Any]:
    """Basic product flow using dummy items (kept for demos)."""
    action = payload.get("action", "")
    screen = payload.get("screen", "")
    data = payload.get("data", {}) or {}

    log.info(f"Product flow - Action: {action}, Screen: {screen}")

    processing_id = data.get("processing_id") or payload.get("processing_id")

    if action.upper() == "INIT":
        products = get_dummy_products()
        header_text = "Product Recommendations"
        footer_text = "Select a product to view details"
        if processing_id:
            header_text = "Your Personalized Results"
            footer_text = f"Results from processing: {processing_id[:8]}..."
        product_options = [{"id": p["id"], "title": p["title"]} for p in products]
        return {
            "version": version,
            "screen": "PRODUCT_LIST",
            "data": {
                "products": products,
                "product_options": product_options,
                "header_text": header_text,
                "footer_text": footer_text,
                "selected_product_id": "",
                "processing_id": processing_id,
            },
        }

    if action.upper() == "DATA_EXCHANGE" and screen == "PRODUCT_LIST":
        selected_product_id = data.get("selected_product_id") or data.get("selected_product")
        log.info(f"Product selected: {selected_product_id}")
        products = get_dummy_products()
        product_options = [{"id": p["id"], "title": p["title"]} for p in products]
        return {
            "version": version,
            "screen": "PRODUCT_LIST",
            "data": {
                "products": products,
                "product_options": product_options,
                "header_text": "Product Recommendations",
                "footer_text": "Select a product to view details",
                "selected_product_id": selected_product_id or "",
                "processing_id": processing_id,
            },
        }

    if action.upper() == "NAVIGATE" and payload.get("next", {}).get("name") == "PRODUCT_DETAILS":
        product_id = data.get("product_id") or data.get("selected_product_id")
        log.info(f"Navigating to details for product: {product_id}")
        products = get_dummy_products()
        product = get_product_by_id(product_id, products) if product_id else None
        if product:
            details_text = (
                f"{product['title']}\n{product['subtitle']}\n\n"
                f"Price: {product['price']}\nBrand: {product['brand']}\n"
                f"Rating: {product.get('rating', 'N/A')}/5.0\n"
                f"Status: {product['availability']}\n\n"
                "Features:\n" + "\n".join(f"• {ft}" for ft in product.get("features", [])) + "\n\n"
                f"{product.get('discount', '')}"
            ).strip()
            return {
                "version": version,
                "screen": "PRODUCT_DETAILS",
                "data": {"product_id": product_id, "product_details": details_text, "processing_id": processing_id},
            }
        return {
            "version": version,
            "screen": "PRODUCT_DETAILS",
            "data": {"product_id": "unknown", "product_details": "Product details not available.", "processing_id": processing_id},
        }

    # Default
    products = get_dummy_products()
    product_options = [{"id": p["id"], "title": p["title"]} for p in products]
    return {
        "version": version,
        "screen": "PRODUCT_LIST",
        "data": {
            "products": products,
            "product_options": product_options,
            "header_text": "Product Recommendations",
            "footer_text": "Select a product to view details",
            "selected_product_id": "",
            "processing_id": processing_id,
        },
    }

# ─────────────────────────────────────────────────────────────
# Product recommendations (real results via BackgroundProcessor; async)
# ─────────────────────────────────────────────────────────────
async def handle_product_recommendations_flow(payload: Dict[str, Any], version: str = "7.2") -> Dict[str, Any]:
    """
    Product recommendations flow backed by background processing results.
    Requires `processing_id` to be included in the Flow action payload's `data`.
    """
    action = payload.get("action", "")
    screen = payload.get("screen", "")
    data = payload.get("data", {}) or {}

    log.info(f"Product recommendations flow - Action: {action}, Screen: {screen}")

    processing_id = data.get("processing_id") or payload.get("processing_id")
    if not processing_id:
        log.error("No processing_id provided for product recommendations flow")
        return {"version": version, "screen": "COMPLETED", "data": {"message": "Sorry, no results available."}}

    background_processor = current_app.extensions.get("background_processor")

    if action.upper() == "INIT":
        products: List[Dict[str, Any]] = []
        if background_processor:
            try:
                products = await background_processor.get_products_for_flow(processing_id)  # async call
            except Exception as e:
                log.error(f"Failed to get products for flow: {e}")

        if products:
            product_options = [{"id": p["id"], "title": p.get("title") or p.get("name", "Product")} for p in products]
            return {
                "version": version,
                "screen": "PRODUCT_LIST",
                "data": {
                    "products": products,
                    "product_options": product_options,
                    "header_text": "Recommended Products for You",
                    "footer_text": f"Found {len(products)} great options",
                    "selected_product_id": "",
                    "processing_id": processing_id,
                },
            }
        else:
            # No products found, try text summary
            text_summary = ""
            if background_processor:
                try:
                    text_summary = await background_processor.get_text_summary_for_flow(processing_id)  # async call
                except Exception as e:
                    log.error(f"Failed to get text summary for flow: {e}")
            msg = (text_summary[:500] + "...") if text_summary and len(text_summary) > 500 else (text_summary or "No results found.")
            return {"version": version, "screen": "COMPLETED", "data": {"message": msg}}

    if action.upper() == "DATA_EXCHANGE" and screen == "PRODUCT_LIST":
        selected_product_id = data.get("selected_product_id") or data.get("selected_product")
        log.info(f"Product selected: {selected_product_id}")

        products: List[Dict[str, Any]] = []
        if background_processor:
            try:
                products = await background_processor.get_products_for_flow(processing_id)
            except Exception as e:
                log.error(f"Failed to get products for data exchange: {e}")
        if not products:
            products = get_dummy_products()

        product_options = [{"id": p["id"], "title": p.get("title") or p.get("name", "Product")} for p in products]
        return {
            "version": version,
            "screen": "PRODUCT_LIST",
            "data": {
                "products": products,
                "product_options": product_options,
                "header_text": "Product Recommendations",
                "footer_text": "Select a product to view details",
                "selected_product_id": selected_product_id or "",
                "processing_id": processing_id,
            },
        }

    if action.upper() == "NAVIGATE" and payload.get("next", {}).get("name") == "PRODUCT_DETAILS":
        product_id = data.get("product_id") or data.get("selected_product_id")
        log.info(f"Navigating to details for product: {product_id}")

        products: List[Dict[str, Any]] = []
        if background_processor:
            try:
                products = await background_processor.get_products_for_flow(processing_id)
            except Exception as e:
                log.error(f"Failed to get products for navigation: {e}")
        if not products:
            products = get_dummy_products()

        product = get_product_by_id(product_id, products) if product_id else None
        if product:
            details_text = (
                f"{product.get('title') or product.get('name', 'Product')}\n"
                f"{product.get('subtitle', '')}\n\n"
                f"Price: {product.get('price', 'N/A')}\n"
                f"Brand: {product.get('brand', 'N/A')}\n"
                f"Rating: {product.get('rating', 'N/A')}/5.0\n"
                f"Status: {product.get('availability', 'N/A')}\n\n"
                "Features:\n" + "\n".join(f"• {ft}" for ft in product.get("features", [])) + "\n\n"
                f"{product.get('discount', '')}"
            ).strip()
            return {"version": version, "screen": "PRODUCT_DETAILS", "data": {"product_id": product_id, "product_details": details_text, "processing_id": processing_id}}

        return {"version": version, "screen": "PRODUCT_DETAILS", "data": {"product_id": "unknown", "product_details": "Product details not available.", "processing_id": processing_id}}

    # Default
    return {"version": version, "screen": "COMPLETED", "data": {"message": "Thank you for using our product recommendations!"}}

# ─────────────────────────────────────────────────────────────
# Unified Flow request handler (now async to support awaits)
# ─────────────────────────────────────────────────────────────
@bp.post("/flow/onboarding")
async def onboarding_flow():
    """Handle onboarding WhatsApp Flow requests."""
    return await _handle_flow_request("onboarding")

@bp.post("/flow/products")
async def product_flow():
    """Handle product recommendation WhatsApp Flow requests (dummy)."""
    return await _handle_flow_request("products")

@bp.post("/flow/product_recommendations")
async def product_recommendations_flow():
    """Handle NEW product recommendations WhatsApp Flow requests (uses background results)."""
    return await _handle_flow_request("product_recommendations")

async def _handle_flow_request(flow_type: str):
    """Unified handler for onboarding, products, and product_recommendations."""
    try:
        raw = request.get_json(silent=True)

        # Health check
        if not raw or raw == {}:
            log.info(f"Health check received for {flow_type}")
            return "", 200

        log.info(f"Received {flow_type} request with keys: {list(raw.keys())}")

        # Decrypt if needed
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
            if is_encrypted and not _private_key:
                log.warning("Encrypted flow data received but no private key is configured.")
            payload = raw

        action = payload.get("action", "")
        version = payload.get("version", "7.2")
        log.info(f"Processing {flow_type} - action: {action}")

        # Dispatch
        if action.lower() == "ping":
            resp_obj = {"version": version, "data": {"status": "active"}}
        else:
            if flow_type == "onboarding":
                resp_obj = handle_onboarding_flow(payload, version)
            elif flow_type == "products":
                resp_obj = handle_product_recommendation_flow(payload, version)
            elif flow_type == "product_recommendations":
                resp_obj = await handle_product_recommendations_flow(payload, version)
            else:
                return jsonify({"error_type": "UNKNOWN_FLOW_TYPE"}), 422

        # Encrypt response if needed
        if is_encrypted and aes_key:
            response_json = json.dumps(resp_obj)
            encrypted_response = _aes_gcm_encrypt(response_json, aes_key, raw["initial_vector"])
            # Some stacks prefer 'application/octet-stream'
            return encrypted_response, 200, {"Content-Type": "text/plain"}
        else:
            return jsonify(resp_obj), 200

    except Exception as e:
        log.exception(f"Unexpected error in {flow_type} flow endpoint: {e}")
        return jsonify({"error_type": "INTERNAL_ERROR"}), 500

# ─────────────────────────────────────────────────────────────
# Health checks
# ─────────────────────────────────────────────────────────────
@bp.get("/flow/health")
def health_check():
    return jsonify({"status": "healthy", "endpoints": ["onboarding", "products", "product_recommendations"]}), 200

@bp.get("/flow/onboarding/health")
def onboarding_health():
    return jsonify({"status": "healthy", "flow": "onboarding"}), 200

@bp.get("/flow/products/health")
def products_health():
    return jsonify({"status": "healthy", "flow": "products"}), 200

@bp.get("/flow/product_recommendations/health")
def product_recommendations_health():
    return jsonify({"status": "healthy", "flow": "product_recommendations"}), 200
