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

# ─────────────────────────────────────────────────────────────
# Onboarding flow (sync)
# ─────────────────────────────────────────────────────────────
def handle_onboarding_flow(payload: Dict[str, Any], version: str = "7.2") -> Dict[str, Any]:
    action = payload.get("action", "")
    screen = payload.get("screen", "")
    data = payload.get("data", {}) or {}

    user_full_name = _resolve_user_full_name(payload)
    log.info(f"📝 ONBOARDING | action={action} screen={screen} user={user_full_name}")

    if action.upper() == "INIT":
        initial = dict(ONBOARDING_DATA)
        initial["user_full_name"] = user_full_name
        return {"version": version, "screen": "ONBOARDING", "data": initial}

    if action.upper() == "DATA_EXCHANGE":
        log.info(f"📝 ONBOARDING_DATA | {data}")

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

        log.info(f"✅ ONBOARDING_COMPLETE | {data}")
        return {"version": version, "screen": "COMPLETE", "data": {"user_full_name": user_full_name}}

    default_data = dict(ONBOARDING_DATA)
    default_data["user_full_name"] = user_full_name
    return {"version": version, "screen": "ONBOARDING", "data": default_data}

# ─────────────────────────────────────────────────────────────
# Product recommendations async (ONLY implementation - no sync fallback)
# ─────────────────────────────────────────────────────────────
async def handle_product_recommendations_flow(payload: Dict[str, Any], version: str = "3.0") -> Dict[str, Any]:
    """
    ASYNC ONLY product recommendations handler with Redis data.
    Removed all sync/dummy fallbacks.
    """
    action = payload.get("action", "").upper()
    data = payload.get("data", {}) or {}
    
    # Extract processing_id from flow_token (primary) or data (fallback)
    processing_id = payload.get("flow_token") or data.get("processing_id")
    
    log.info(f"🚀 FLOW_ASYNC | action={action} processing_id={processing_id}")
    log.info(f"📦 META_PAYLOAD_FULL | {json.dumps(payload, indent=2)}")
    
    if not processing_id:
        log.warning("❌ NO_PROCESSING_ID | returning empty state")
        return {
            "version": version,
            "screen": "PRODUCT_LIST",
            "data": {
                "products": [],
                "product_options": [],
                "header_text": "No recommendations available",
                "footer_text": "Please try again",
                "processing_id": None
            }
        }
    
    background_processor = current_app.extensions.get("background_processor")
    if not background_processor:
        log.error("❌ NO_BACKGROUND_PROCESSOR | service unavailable")
        return {
            "version": version,
            "screen": "PRODUCT_LIST", 
            "data": {
                "products": [],
                "product_options": [],
                "header_text": "Service unavailable",
                "footer_text": "Please try again later",
                "processing_id": None
            }
        }
    
    if action == "INIT":
        try:
            log.info(f"🔍 REDIS_LOOKUP | processing_id={processing_id}")
            
            # Get full Redis result
            redis_result = await background_processor.get_processing_result(processing_id)
            
            if not redis_result:
                log.warning(f"❌ REDIS_NOT_FOUND | processing_id={processing_id}")
                return {
                    "version": version,
                    "screen": "PRODUCT_LIST",
                    "data": {
                        "products": [],
                        "product_options": [],
                        "header_text": "Results not found",
                        "footer_text": "Please try a new search",
                        "processing_id": None
                    }
                }
            
            log.info(f"✅ REDIS_FOUND | keys={list(redis_result.keys())}")
            
            flow_data = redis_result.get("flow_data", {})
            raw_products = flow_data.get("products", [])
            
            log.info(f"📊 RAW_PRODUCTS | count={len(raw_products)}")
            if raw_products:
                log.info(f"📊 FIRST_PRODUCT | {json.dumps(raw_products[0], indent=2)}")
            
            # Transform Redis products to Meta format
            products = []
            for i, product in enumerate(raw_products):
                # Coerce price to string with ₹
                price = product.get("price", "N/A")
                if isinstance(price, (int, float)):
                    price = f"₹{price}"
                elif not isinstance(price, str):
                    price = "Price on request"
                
                # Map image field
                image = product.get("image") or product.get("image_url") or "https://via.placeholder.com/150x150?text=Product"
                
                # Cap features to 5
                features = product.get("features", [])
                if isinstance(features, list):
                    features = features[:5]
                else:
                    features = []
                
                transformed_product = {
                    "id": product.get("id", f"prod_{i}"),
                    "title": product.get("title", "Product"),
                    "subtitle": product.get("subtitle", ""),
                    "price": price,
                    "brand": product.get("brand", ""),
                    "rating": product.get("rating"),
                    "availability": product.get("availability", "In Stock"),
                    "discount": product.get("discount", ""),
                    "image": image,
                    "features": features
                }
                products.append(transformed_product)
                
                log.info(f"🔄 TRANSFORM_{i} | {product.get('title')} → {price}")
            
            # Build product_options
            product_options = [{"id": p["id"], "title": p["title"]} for p in products]
            
            # Get header/footer from Redis
            header_text = flow_data.get("header_text", "Your Product Recommendations")
            footer_text = flow_data.get("footer_text", f"Found {len(products)} options")
            
            response_payload = {
                "version": version,
                "screen": "PRODUCT_LIST",
                "data": {
                    "products": products,
                    "product_options": product_options,
                    "header_text": header_text,
                    "footer_text": footer_text,
                    "processing_id": None
                }
            }
            
            log.info(f"📤 META_RESPONSE | screen=PRODUCT_LIST products_count={len(products)}")
            log.info(f"📤 META_RESPONSE_FULL | {json.dumps(response_payload, indent=2)}")
            
            return response_payload
            
        except Exception as e:
            log.error(f"❌ INIT_ERROR | {e}", exc_info=True)
            return {
                "version": version,
                "screen": "PRODUCT_LIST",
                "data": {
                    "products": [],
                    "product_options": [],
                    "header_text": "Error loading recommendations",
                    "footer_text": "Please try again",
                    "processing_id": None
                }
            }
    
    # Handle DATA_EXCHANGE (product selection)
    if action == "DATA_EXCHANGE":
        try:
            # Get product_id from payload
            raw_pid = data.get("product_id") or data.get("selected_product_id")
            product_id = _coerce_product_id(raw_pid)
            
            log.info(f"🎯 PRODUCT_SELECT | raw_pid={raw_pid} product_id={product_id}")
            
            if not product_id:
                log.warning("❌ INVALID_PRODUCT_ID | returning error")
                return {
                    "version": version,
                    "screen": "PRODUCT_LIST",
                    "data": {
                        "products": [],
                        "product_options": [],
                        "header_text": "Invalid selection",
                        "footer_text": "Please select a product",
                        "processing_id": None
                    }
                }
            
            # Get Redis data and find the product
            redis_result = await background_processor.get_processing_result(processing_id)
            if not redis_result:
                log.warning(f"❌ REDIS_NOT_FOUND_DETAILS | processing_id={processing_id}")
                return {
                    "version": version,
                    "screen": "PRODUCT_DETAILS",
                    "data": {
                        "product_id": product_id,
                        "product_details": "Product details not available. Please go back and try again.",
                        "processing_id": None
                    }
                }
            
            flow_data = redis_result.get("flow_data", {})
            products = flow_data.get("products", [])
            
            # Find the selected product
            selected_product = None
            for product in products:
                if product.get("id") == product_id:
                    selected_product = product
                    break
            
            if not selected_product:
                log.warning(f"❌ PRODUCT_NOT_FOUND | product_id={product_id}")
                return {
                    "version": version,
                    "screen": "PRODUCT_DETAILS",
                    "data": {
                        "product_id": product_id,
                        "product_details": "Product not found. Please go back and try again.",
                        "processing_id": None
                    }
                }
            
            log.info(f"✅ PRODUCT_FOUND | {selected_product.get('title')}")
            
            # Build product details text
            title = selected_product.get("title", "Product")
            subtitle = selected_product.get("subtitle", "")
            
            # Handle price
            price = selected_product.get("price", "N/A")
            if isinstance(price, (int, float)):
                price = f"₹{price}"
            
            brand = selected_product.get("brand", "N/A")
            rating = selected_product.get("rating", "N/A")
            availability = selected_product.get("availability", "Check availability")
            
            # Format features
            features = selected_product.get("features", [])
            if isinstance(features, list) and features:
                features_text = "\n".join(f"• {f}" for f in features)
            else:
                features_text = "• Standard features"
            
            # Handle discount
            discount = selected_product.get("discount", "")
            discount_text = f"\nSpecial Offer: {discount}" if discount else ""
            
            details_text = (
                f"{title}\n{subtitle}\n\n"
                f"Price: {price}\n"
                f"Brand: {brand}\n"
                f"Rating: {rating}/5.0\n"
                f"Status: {availability}\n\n"
                f"Features:\n{features_text}"
                f"{discount_text}"
            ).strip()
            
            response_payload = {
                "version": version,
                "screen": "PRODUCT_DETAILS",
                "data": {
                    "product_id": product_id,
                    "product_details": details_text,
                    "processing_id": None
                }
            }
            
            log.info(f"📤 DETAILS_RESPONSE | {title} → {len(details_text)} chars")
            log.info(f"📤 DETAILS_RESPONSE_FULL | {json.dumps(response_payload, indent=2)}")
            
            return response_payload
            
        except Exception as e:
            log.error(f"❌ DATA_EXCHANGE_ERROR | {e}", exc_info=True)
            return {
                "version": version,
                "screen": "PRODUCT_DETAILS",
                "data": {
                    "product_id": raw_pid or "unknown",
                    "product_details": "Error loading product details. Please try again.",
                    "processing_id": None
                }
            }
    
    # Handle NAVIGATE (legacy support)
    if action == "NAVIGATE":
        log.info(f"🔄 NAVIGATE_CONVERT | converting to DATA_EXCHANGE")
        # Convert NAVIGATE to DATA_EXCHANGE and recurse
        nav_payload = payload.get("payload") or data or {}
        new_payload = {
            "action": "DATA_EXCHANGE",
            "data": nav_payload,
            "flow_token": payload.get("flow_token"),
            "version": version
        }
        return await handle_product_recommendations_flow(new_payload, version)
    
    # Fallback for unknown actions
    log.warning(f"❌ UNKNOWN_ACTION | action={action}")
    return {
        "version": version,
        "screen": "PRODUCT_LIST",
        "data": {
            "products": [],
            "product_options": [],
            "header_text": "Unknown action",
            "footer_text": "Please try again",
            "processing_id": None
        }
    }

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
            log.info(f"💚 HEALTH_CHECK | {flow_type}")
            return "", 200

        log.info(f"📨 RAW_REQUEST | {flow_type} keys={list(raw.keys())}")

        is_encrypted = "encrypted_flow_data" in raw
        aes_key = None
        if is_encrypted and _private_key:
            log.info("🔐 DECRYPT_START | processing encrypted request")
            try:
                encrypted_aes_key = raw.get("encrypted_aes_key", "")
                aes_key = _rsa_decrypt(encrypted_aes_key)
                encrypted_flow_data = raw.get("encrypted_flow_data", "")
                initial_vector = raw.get("initial_vector", "")
                decrypted_json = _aes_gcm_decrypt(encrypted_flow_data, aes_key, initial_vector)
                payload = json.loads(decrypted_json)
                log.info(f"🔓 DECRYPT_SUCCESS | action={payload.get('action')} screen={payload.get('screen')}")
            except Exception as exc:
                log.error(f"❌ DECRYPT_FAILED | {exc}", exc_info=True)
                return jsonify({"error_type": "DECRYPTION_FAILED"}), 421
        else:
            if is_encrypted and not _private_key:
                log.warning("🔑 NO_PRIVATE_KEY | encrypted data received but no key configured")
            payload = raw

        action = payload.get("action", "")
        version = str(payload.get("version") or "3.0") 
        log.info(f"🎬 FLOW_START | {flow_type} action={action} version={version}")

        if action.lower() == "ping":
            resp_obj = {"version": version, "data": {"status": "active"}}
        else:
            if flow_type == "onboarding":
                resp_obj = handle_onboarding_flow(payload, version)
            elif flow_type in ["products", "product_recommendations"]:
                # Both routes use async handler - NO SYNC FALLBACK
                resp_obj = await handle_product_recommendations_flow(payload, version)
            else:
                log.error(f"❌ UNKNOWN_FLOW_TYPE | {flow_type}")
                return jsonify({"error_type": "UNKNOWN_FLOW_TYPE"}), 422

        screen = resp_obj.get('screen', 'unknown')
        log.info(f"✅ FLOW_COMPLETE | {flow_type} → screen={screen}")

        if is_encrypted and aes_key:
            response_json = json.dumps(resp_obj)
            encrypted_response = _aes_gcm_encrypt(response_json, aes_key, raw["initial_vector"])
            log.info(f"🔐 ENCRYPT_RESPONSE | size={len(encrypted_response)} bytes")
            return encrypted_response, 200, {"Content-Type": "text/plain"}
        else:
            log.info(f"📤 PLAIN_RESPONSE | size={len(json.dumps(resp_obj))} bytes")
            return jsonify(resp_obj), 200

    except Exception as e:
        log.error(f"💥 FLOW_HANDLER_ERROR | {flow_type} failed: {e}", exc_info=True)
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