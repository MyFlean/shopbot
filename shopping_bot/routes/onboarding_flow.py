from __future__ import annotations

from flask import Blueprint, request, jsonify, current_app
import logging
import json
import base64
import os
from typing import Any, Dict, List, Optional, Tuple
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
    if not _private_key:
        raise RuntimeError("Private key not available")
    encrypted_aes_key = base64.b64decode(encrypted_aes_key_b64)
    return _private_key.decrypt(
        encrypted_aes_key,
        asympad.OAEP(mgf=asympad.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )

def flip_iv(iv: bytes) -> bytes:
    return bytes(b ^ 0xFF for b in iv)

def _aes_gcm_decrypt(encrypted_flow_data_b64: str, aes_key: bytes, initial_vector_b64: str) -> str:
    encrypted_flow_data = base64.b64decode(encrypted_flow_data_b64)
    iv = base64.b64decode(initial_vector_b64)
    encrypted_body = encrypted_flow_data[:-16]
    auth_tag = encrypted_flow_data[-16:]
    cipher = Cipher(algorithms.AES(aes_key), modes.GCM(iv, auth_tag), backend=default_backend())
    decryptor = cipher.decryptor()
    return (decryptor.update(encrypted_body) + decryptor.finalize()).decode("utf-8")

def _aes_gcm_encrypt(response: str, aes_key: bytes, initial_vector_b64: str) -> str:
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

def _extract_ids(payload: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Returns (user_id, session_id, wa_id) from top-level or payload['data']."""
    user_id = payload.get("user_id")
    session_id = payload.get("session_id")
    wa_id = payload.get("wa_id")
    if not (user_id and session_id and wa_id):
        data = payload.get("data") or {}
        user_id = user_id or data.get("user_id")
        session_id = session_id or data.get("session_id")
        wa_id = wa_id or data.get("wa_id")
    return user_id, session_id, wa_id

def _coerce_product_id(val) -> str:
    """Normalize product id from string/object/None → string id."""
    try:
        if isinstance(val, dict):
            # Handle dropdown selection objects
            return str(val.get("id") or val.get("value") or val.get("product_id") or "").strip()
        if val is None:
            return ""
        return str(val).strip()
    except Exception:
        return ""

def _resolve_user_full_name(payload: Dict[str, Any]) -> str:
    """Find a friendly display name from context; fallback to masked wa_id."""
    user_id, session_id, wa_id = _extract_ids(payload)

    data = payload.get("data") or {}
    direct_name = payload.get("user_full_name") or data.get("user_full_name")
    if direct_name and isinstance(direct_name, str) and direct_name.strip():
        return direct_name.strip()

    try:
        ctx_mgr = current_app.extensions.get("ctx_mgr")
        if ctx_mgr and (user_id or wa_id):
            sid = session_id or (user_id or wa_id or "default")
            uid = user_id or (wa_id or "anon")
            ctx = ctx_mgr.get_context(uid, sid)

            try:
                user_blob = (ctx.session or {}).get("user") or {}
                for k in ("full_name", "name", "first_name", "display_name"):
                    val = user_blob.get(k)
                    if isinstance(val, str) and val.strip():
                        return val.strip()
            except Exception:
                pass

            try:
                prof = getattr(ctx, "user_profile", None)
                for k in ("full_name", "name", "first_name", "display_name"):
                    val = getattr(prof, k, None) if prof else None
                    if isinstance(val, str) and val.strip():
                        return val.strip()
            except Exception:
                pass

            for bucket in ("profile", "whatsapp", "meta", "customer"):
                try:
                    b = (ctx.session or {}).get(bucket) or {}
                    for k in ("full_name", "name", "first_name"):
                        val = b.get(k)
                        if isinstance(val, str) and val.strip():
                            return val.strip()
                except Exception:
                    pass
    except Exception as e:
        log.debug(f"Name resolution via context failed: {e}")

    if wa_id:
        return f"user {str(wa_id)[-4:]}"
    return "there"

def get_dummy_products() -> List[Dict[str, Any]]:
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

    user_full_name = _resolve_user_full_name(payload)
    log.info(f"Onboarding flow - Action: {action}, Screen: {screen}, Resolved name: {user_full_name!r}")

    if action.upper() == "INIT":
        initial = dict(ONBOARDING_DATA)
        initial["user_full_name"] = user_full_name
        return {"version": version, "screen": "ONBOARDING", "data": initial}

    if action.upper() == "DATA_EXCHANGE":
        log.info(f"Onboarding data exchange: {data}")

        society_value = data.get("society") or data.get("selected_society")

        if society_value == "other":
            onboarding_data_with_custom = dict(ONBOARDING_DATA)
            onboarding_data_with_custom["show_custom_society"] = True
            onboarding_data_with_custom["user_full_name"] = user_full_name
            return {"version": version, "screen": "ONBOARDING", "data": onboarding_data_with_custom}

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
            merged["user_full_name"] = user_full_name
            return {"version": version, "screen": "ONBOARDING", "data": merged}

        log.info(f"Onboarding completed successfully with data: {data}")
        return {"version": version, "screen": "COMPLETE", "data": {"user_full_name": user_full_name}}

    default_data = dict(ONBOARDING_DATA)
    default_data["user_full_name"] = user_full_name
    return {"version": version, "screen": "ONBOARDING", "data": default_data}

# ─────────────────────────────────────────────────────────────
# Product recommendation flow (FIXED for navigation)
# ─────────────────────────────────────────────────────────────
def handle_product_recommendation_flow(payload: Dict[str, Any], version: str = "3.0") -> Dict[str, Any]:
    action = (payload.get("action") or "").upper()
    data = payload.get("data") or {}
    processing_id = data.get("processing_id") or payload.get("processing_id")

    if action == "INIT":
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
                "processing_id": processing_id,
            },
        }

    # IMPORTANT: handle DETAILS via DATA_EXCHANGE
    if action == "DATA_EXCHANGE":
        raw_pid = (data.get("product_id") or data.get("selected_product_id"))
        product_id = _coerce_product_id(raw_pid)
        if product_id:
            products = get_dummy_products()
            prod = get_product_by_id(product_id, products)
            if not prod:
                return {
                    "version": version,
                    "screen": "PRODUCT_DETAILS",
                    "data": {
                        "product_id": product_id,
                        "product_details": "Product not found. Please go back and try again.",
                        "processing_id": processing_id,
                    },
                }

            features = prod.get("features", []) or []
            features_text = "\n".join(f"• {f}" for f in features) if features else "• Standard features"
            discount = prod.get("discount") or ""
            discount_text = f"\n{discount}" if discount else ""
            details_text = (
                f"{prod.get('title','Product')}\n{prod.get('subtitle','')}\n\n"
                f"Price: {prod.get('price','N/A')}\nBrand: {prod.get('brand','N/A')}\n"
                f"Rating: {prod.get('rating','N/A')}/5.0\nStatus: {prod.get('availability','N/A')}\n\n"
                f"Features:\n{features_text}{discount_text}"
            ).strip() or "Details coming soon."

            return {
                "version": version,
                "screen": "PRODUCT_DETAILS",
                "data": {
                    "product_id": product_id,
                    "product_details": details_text,
                    "processing_id": processing_id,
                },
            }

        # no product id → re-render list
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
                "processing_id": processing_id,
            },
        }

    # (Optional) keep supporting NAVIGATE for devices that do call the endpoint:
    if action == "NAVIGATE" and (payload.get("screen") == "PRODUCT_DETAILS" or (payload.get("next") or {}).get("name") == "PRODUCT_DETAILS"):
        nav = payload.get("payload") or data or {}
        payload["action"] = "DATA_EXCHANGE"
        payload["data"] = nav
        return handle_product_recommendation_flow(payload, version)

    # fallback
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
            "processing_id": processing_id,
        },
    }

# ─────────────────────────────────────────────────────────────
# Product recommendations async (for real results)
# ─────────────────────────────────────────────────────────────
async def handle_product_recommendations_flow(payload: Dict[str, Any], version: str = "7.2") -> Dict[str, Any]:
    """Async version for real product recommendations from BackgroundProcessor"""
    action = payload.get("action", "")
    screen = payload.get("screen", "")
    data = payload.get("data", {}) or {}
    
    log.info(f"Product recommendations async - Action: {action}, Screen: {screen}")
    
    processing_id = data.get("processing_id") or payload.get("processing_id")
    if not processing_id:
        log.warning("No processing_id for async flow, falling back to sync handler")
        return handle_product_recommendation_flow(payload, version)
    
    background_processor = current_app.extensions.get("background_processor")
    if not background_processor:
        log.warning("No background processor available, using sync handler")
        return handle_product_recommendation_flow(payload, version)
    
    # For INIT action, try to get real products
    if action.upper() == "INIT":
        try:
            products = await background_processor.get_products_for_flow(processing_id)
            if products:
                log.info(f"Got {len(products)} real products from background processor")
            else:
                products = get_dummy_products()
                log.info("Using dummy products as fallback")
        except Exception as e:
            log.error(f"Failed to get products: {e}")
            products = get_dummy_products()
        
        product_options = [{"id": p["id"], "title": p.get("title", p.get("name", "Product"))} for p in products]
        return {
            "version": version,
            "screen": "PRODUCT_LIST",
            "data": {
                "products": products,
                "product_options": product_options,
                "header_text": "Your Personalized Recommendations",
                "footer_text": f"Found {len(products)} products for you",
                "processing_id": processing_id,
            },
        }
    
    # For NAVIGATE action, handle it the same way as sync
    if action.upper() == "NAVIGATE":
        # Get real products if available
        try:
            products = await background_processor.get_products_for_flow(processing_id)
            if not products:
                products = get_dummy_products()
        except Exception:
            products = get_dummy_products()
        
        # Use sync navigation logic with real products
        temp_payload = dict(payload)
        temp_data = temp_payload.get("data", {})
        temp_data["_products_cache"] = products  # Pass products through
        temp_payload["data"] = temp_data
        
        result = handle_product_recommendation_flow(temp_payload, version)
        
        # If navigating to PRODUCT_DETAILS, use real product data
        if result.get("screen") == "PRODUCT_DETAILS":
            product_id = result.get("data", {}).get("product_id")
            if product_id:
                product = get_product_by_id(product_id, products)
                if product:
                    # Rebuild details with real product data
                    features = product.get("features", [])
                    features_text = "\n".join(f"• {ft}" for ft in features) if features else "• Standard features"
                    
                    discount = product.get("discount", "")
                    discount_text = f"\nSpecial Offer: {discount}" if discount else ""
                    
                    details_text = (
                        f"{product.get('title', product.get('name', 'Product'))}\n"
                        f"{product.get('subtitle', '')}\n"
                        f"\n"
                        f"Price: {product.get('price', 'Contact for price')}\n"
                        f"Brand: {product.get('brand', 'Premium Brand')}\n"
                        f"Rating: {product.get('rating', 'N/A')}/5.0\n"
                        f"Availability: {product.get('availability', 'Check availability')}\n"
                        f"\n"
                        f"Key Features:\n{features_text}"
                        f"{discount_text}"
                    ).strip()
                    
                    result["data"]["product_details"] = details_text
        
        return result
    
    # For other actions, delegate to sync handler
    return handle_product_recommendation_flow(payload, version)

# ─────────────────────────────────────────────────────────────
# Unified Flow request handler
# ─────────────────────────────────────────────────────────────
@bp.post("/flow/onboarding")
async def onboarding_flow():
    return await _handle_flow_request("onboarding")

@bp.post("/flow/products")
async def product_flow():
    return await _handle_flow_request("products")

@bp.post("/flow/product_recommendations")
async def product_recommendations_flow():
    return await _handle_flow_request("product_recommendations")

async def _handle_flow_request(flow_type: str):
    try:
        raw = request.get_json(silent=True)
        if not raw or raw == {}:
            log.info(f"Health check received for {flow_type}")
            return "", 200

        log.info(f"Received {flow_type} request with keys: {list(raw.keys())}")

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
                log.info(f"Decrypted payload action: {payload.get('action')}, screen: {payload.get('screen')}")
            except Exception as exc:
                log.exception(f"Decryption failed: {exc}")
                return jsonify({"error_type": "DECRYPTION_FAILED"}), 421
        else:
            if is_encrypted and not _private_key:
                log.warning("Encrypted flow data received but no private key is configured.")
            payload = raw

        action = payload.get("action", "")
        version = str(payload.get("version") or "3.0") 
        log.info(f"Processing {flow_type} - action: {action}, version: {version}")

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

        log.info(f"Returning response for {flow_type}: screen={resp_obj.get('screen')}")

        if is_encrypted and aes_key:
            response_json = json.dumps(resp_obj)
            encrypted_response = _aes_gcm_encrypt(response_json, aes_key, raw["initial_vector"])
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