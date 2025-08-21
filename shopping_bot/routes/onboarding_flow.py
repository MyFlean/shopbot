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

# FIX 4: support both FLOW_PRIVATE_KEY and FLOW_PRIVATE_KEY_PATH (and keep default path)
_key_path = Path(
    os.getenv("FLOW_PRIVATE_KEY") or
    os.getenv("FLOW_PRIVATE_KEY_PATH", "/secrets/Flow_Private_Key")
)
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
        {"id": "male", "title": "👨 Male"},
        {"id": "female", "title": "👩 Female"},
        {"id": "other", "title": "🌈 Other"},
    ],
    "age_groups": [
        {"id": "18_25", "title": "🎓 Young Explorer (18-25)"},
        {"id": "26_35", "title": "🚀 Rising Star (26-35)"},
        {"id": "36_45", "title": "💼 Prime Time (36-45)"},
        {"id": "46_55", "title": "🏆 Experienced Pro (46-55)"},
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

def _extract_flow_token(payload: Dict[str, Any]) -> Optional[str]:
    """Extract flow_token from payload, data, or nested structures."""
    flow_token = payload.get("flow_token")
    if flow_token:
        return flow_token
    data = payload.get("data", {}) or {}
    flow_token = data.get("flow_token")
    if flow_token:
        return flow_token
    payload_data = payload.get("payload", {}) or {}
    flow_token = payload_data.get("flow_token")
    if flow_token:
        return flow_token
    return None

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

async def _save_user_data_to_redis(flow_token: str, user_data: Dict[str, Any]):
    """Save user data to Redis using flow_token as key."""
    if not flow_token:
        log.warning("❌ NO_FLOW_TOKEN | Cannot save user data to Redis")
        return False
    
    try:
        background_processor = current_app.extensions.get("background_processor")
        if not background_processor:
            log.error("❌ NO_BACKGROUND_PROCESSOR | Cannot save to Redis")
            return False
        
        existing_data = await background_processor.get_processing_result(flow_token) or {}
        if "user_data" not in existing_data:
            existing_data["user_data"] = {}
        existing_data["user_data"].update(user_data)
        existing_data["last_updated"] = None  # add timestamp if needed
        
        result_key = f"processing:{flow_token}:result"
        background_processor.ctx_mgr._set_json(result_key, existing_data, ttl=background_processor.processing_ttl)
        
        log.info(f"✅ REDIS_SAVE | flow_token={flow_token} | data_keys={list(user_data.keys())}")
        return True
        
    except Exception as e:
        log.error(f"❌ REDIS_SAVE_ERROR | flow_token={flow_token} | error={e}", exc_info=True)
        return False

async def _trigger_webhook(flow_token: str, status: str, additional_data: Dict[str, Any] = None):
    """Trigger webhook notification for flow events."""
    webhook_url = os.getenv("FRONTEND_WEBHOOK_URL")
    if not webhook_url:
        log.warning("❌ NO_WEBHOOK_URL | Webhook not configured")
        return False
    
    payload = {
        "flowtoken": flow_token,
        "status": status
    }
    if additional_data:
        payload.update(additional_data)
    
    log.info(f"🔔 WEBHOOK_TRIGGER | flow_token={flow_token} | url={webhook_url} | payload={payload}")
    
    try:
        import aiohttp
        ssl_verify = os.getenv("WEBHOOK_SSL_VERIFY", "true").lower() != "false"
        connector = None if ssl_verify else aiohttp.TCPConnector(ssl=False)
        
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(
                webhook_url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
                headers={"Content-Type": "application/json"}
            ) as response:
                response_text = await response.text()
                if response.status == 200:
                    log.info(f"✅ WEBHOOK_SUCCESS | flow_token={flow_token} | status={status} | response={response_text[:200]}")
                    return True
                else:
                    log.warning(f"❌ WEBHOOK_FAILED | flow_token={flow_token} | status_code={response.status} | response={response_text[:200]}")
                    return False
                    
    except Exception as e:
        log.error(f"❌ WEBHOOK_ERROR | flow_token={flow_token} | error={e}", exc_info=True)
        return False

def _log_user_activity(activity_type: str, payload: Dict[str, Any], additional_data: Dict[str, Any] = None):
    """Log user activity with flow_token and user identification."""
    user_id, session_id, wa_id = _extract_ids(payload)
    flow_token = _extract_flow_token(payload)
    user_name = _resolve_user_full_name(payload)
    
    activity_log = {
        "activity_type": activity_type,
        "flow_token": flow_token,
        "user_id": user_id,
        "session_id": session_id,
        "wa_id": wa_id,
        "user_name": user_name,
        "timestamp": None,  # add timestamp if needed
    }
    if additional_data:
        activity_log.update(additional_data)
    
    log.info(f"🔄 USER_ACTIVITY | {activity_type} | flow_token={flow_token} | user={user_name} | data={additional_data}")
    return activity_log

# ─────────────────────────────────────────────────────────────
# Onboarding flow (async) with Redis integration
# ─────────────────────────────────────────────────────────────
async def handle_onboarding_flow(payload: Dict[str, Any], version: str = "7.2") -> Dict[str, Any]:
    action = payload.get("action", "").upper()
    screen = payload.get("screen", "")
    data = payload.get("data", {}) or {}
    flow_token = _extract_flow_token(payload)

    user_full_name = _resolve_user_full_name(payload)
    log.info(f"📝 ONBOARDING | action={action} screen={screen} user={user_full_name} flow_token={flow_token}")

    if action == "INIT":
        _log_user_activity("flow_started", payload, {"flow_type": "onboarding"})
        if flow_token:
            user_context = {
                "flow_type": "onboarding",
                "user_full_name": user_full_name,
                "user_id": payload.get("user_id"),
                "wa_id": payload.get("wa_id"),
                "session_id": payload.get("session_id"),
                "flow_started": True
            }
            await _save_user_data_to_redis(flow_token, user_context)
        
        initial = dict(ONBOARDING_DATA)
        initial["user_full_name"] = user_full_name
        return {"version": version, "screen": "ONBOARDING", "data": initial}

    if action == "DATA_EXCHANGE":
        action_type = data.get("action_type", "")
        if action_type == "submit_profile":
            log.info(f"📝 PROFILE_SUBMIT | {data}")
            _log_user_activity("profile_submitted", payload, {
                "society": data.get("society"),
                "custom_society": data.get("custom_society"),
                "gender": data.get("gender"),
                "age_group": data.get("age_group")
            })

            if flow_token:
                profile_data = {
                    "society": data.get("society"),
                    "custom_society": data.get("custom_society"),
                    "gender": data.get("gender"),
                    "age_group": data.get("age_group"),
                    "profile_submitted": True
                }
                await _save_user_data_to_redis(flow_token, profile_data)

            society_value = data.get("society") or data.get("selected_society")

            # FIX 3: only re-show custom field when 'other' is chosen AND it's empty
            if society_value == "other" and not (data.get("custom_society") or "").strip():
                onboarding_data_with_custom = dict(ONBOARDING_DATA)
                onboarding_data_with_custom["show_custom_society"] = True
                onboarding_data_with_custom["user_full_name"] = user_full_name
                return {"version": version, "screen": "ONBOARDING", "data": onboarding_data_with_custom}

            errors: Dict[str, str] = {}
            if not society_value:
                errors["society"] = "Please select your society"
            elif society_value == "other" and not (data.get("custom_society") or "").strip():
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

            log.info(f"✅ PROFILE_VALIDATED | {data}")
            _log_user_activity("profile_validated", payload, {"profile_data": data})
            if flow_token:
                await _save_user_data_to_redis(flow_token, {"profile_validated": True})
            return {"version": version, "screen": "COMPLETE", "data": {"user_full_name": user_full_name}}
        
        elif action_type == "complete_flow":
            log.info(f"🎉 FLOW_COMPLETED | user={user_full_name}")
            _log_user_activity("flow_completed", payload, {"flow_type": "onboarding"})
            if flow_token:
                completion_data = {"flow_completed": True, "completion_timestamp": None}
                await _save_user_data_to_redis(flow_token, completion_data)
                await _trigger_webhook(flow_token, "Flow_Closed", {
                    "flow_type": "onboarding",
                    "user_name": user_full_name
                })
            log.info(f"🔚 TERMINATING_FLOW | flow_token={flow_token}")
            return {
                "version": version,
                "screen": "SUCCESS",
                "data": {
                    "extension_message_response": {
                        "params": {
                            "flow_token": flow_token or "",
                            "completion_status": "success"
                        }
                    }
                }
            }
        else:
            log.warning(f"❌ UNKNOWN_ACTION_TYPE | action_type={action_type}")
            _log_user_activity("unknown_action", payload, {"action_type": action_type})
            default_data = dict(ONBOARDING_DATA)
            default_data["user_full_name"] = user_full_name
            return {"version": version, "screen": "ONBOARDING", "data": default_data}

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

def _coerce_product_id(val) -> str:
    """Normalize product id from string/object/None → string id."""
    try:
        if isinstance(val, dict):
            return str(val.get("id") or val.get("value") or val.get("product_id") or "").strip()
        if val is None:
            return ""
        return str(val).strip()
    except Exception:
        return ""

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

        # FIX 2: default version per flow (onboarding → 7.2, others → 3.0)
        version = str(payload.get("version") or ("7.2" if flow_type == "onboarding" else "3.0"))

        log.info(f"🎬 FLOW_START | {flow_type} action={action} version={version}")

        if action.lower() == "ping":
            resp_obj = {"version": version, "data": {"status": "active"}}
        else:
            if flow_type == "onboarding":
                resp_obj = await handle_onboarding_flow(payload, version)
            elif flow_type in ["products", "product_recommendations"]:
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

# FIX 1: removed stray/invalid code block that used to live here

# ─────────────────────────────────────────────────────────────
# Product recommendations async (ONLY implementation)
# ─────────────────────────────────────────────────────────────
async def handle_product_recommendations_flow(payload: Dict[str, Any], version: str = "3.0") -> Dict[str, Any]:
    action = payload.get("action", "").upper()
    data = payload.get("data", {}) or {}
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
            
            products = []
            for i, product in enumerate(raw_products):
                price = product.get("price", "N/A")
                if isinstance(price, (int, float)):
                    price = f"₹{price}"
                elif not isinstance(price, str):
                    price = "Price on request"
                
                image = product.get("image") or product.get("image_url") or "https://via.placeholder.com/150x150?text=Product"
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
                    "brand": product.get("brand") or "",
                    "rating": product.get("rating"),
                    "availability": product.get("availability", "In Stock"),
                    "discount": product.get("discount") or "",
                    "image": image,
                    "features": features
                }
                products.append(transformed_product)
                log.info(f"🔄 TRANSFORM_{i} | {product.get('title')} → {price}")
            
            product_options = [{"id": p["id"], "title": p["title"]} for p in products]
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
    
    if action == "DATA_EXCHANGE":
        try:
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
            title = selected_product.get("title", "Product")
            subtitle = selected_product.get("subtitle", "")
            price = selected_product.get("price", "N/A")
            if isinstance(price, (int, float)):
                price = f"₹{price}"
            brand = selected_product.get("brand", "N/A")
            rating = selected_product.get("rating", "N/A")
            availability = selected_product.get("availability", "Check availability")
            
            features = selected_product.get("features", [])
            if isinstance(features, list) and features:
                features_text = "\n".join(f"• {f}" for f in features)
            else:
                features_text = "• Standard features"
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
    
    if action == "NAVIGATE":
        log.info(f"🔄 NAVIGATE_CONVERT | converting to DATA_EXCHANGE")
        nav_payload = payload.get("payload") or data or {}
        new_payload = {
            "action": "DATA_EXCHANGE",
            "data": nav_payload,
            "flow_token": payload.get("flow_token"),
            "version": version
        }
        return await handle_product_recommendations_flow(new_payload, version)

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
