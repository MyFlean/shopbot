# from flask import Blueprint, request, jsonify
# import logging
# import json
# import base64
# import os
# from cryptography.hazmat.primitives import serialization, hashes
# from cryptography.hazmat.primitives.asymmetric import padding as asympad
# from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
# from cryptography.hazmat.backends import default_backend
# from pathlib import Path

# bp = Blueprint("onboarding_flow", __name__)
# log = logging.getLogger(__name__)

# # Configure logging
# logging.basicConfig(level=logging.INFO)

# # ───────────────────────────────────────────
# # Load RSA private key - GCP mounts secrets at /secrets/{SECRET_NAME}
# _key_path = Path(os.getenv("FLOW_PRIVATE_KEY", "/secrets/Flow_Private_Key"))
# if not _key_path.exists():
#     # Fallback for local development
#     alt_path = Path(__file__).resolve().parent / "private.pem"
#     if alt_path.exists():
#         _key_path = alt_path
#     else:
#         raise RuntimeError(f"Private key not found at {_key_path}. Check GCP Secret mount or set FLOW_PRIVATE_KEY env var.")

# with open(_key_path, 'rb') as key_file:
#     _private_key = serialization.load_pem_private_key(
#         key_file.read(), 
#         password=None,
#         backend=default_backend()
#     )

# def _rsa_decrypt(encrypted_aes_key_b64: str) -> bytes:
#     """Decrypt RSA encrypted AES key using OAEP with SHA256."""
#     encrypted_aes_key = base64.b64decode(encrypted_aes_key_b64)
#     try:
#         # WhatsApp uses OAEP with SHA256
#         return _private_key.decrypt(
#             encrypted_aes_key,
#             asympad.OAEP(
#                 mgf=asympad.MGF1(algorithm=hashes.SHA256()),
#                 algorithm=hashes.SHA256(),
#                 label=None
#             )
#         )
#     except Exception as e:
#         log.error(f"RSA decryption failed: {e}")
#         raise

# def flip_iv(iv: bytes) -> bytes:
#     """Flip all bits in IV for GCM mode response encryption."""
#     return bytes(byte ^ 0xFF for byte in iv)

# def _aes_gcm_decrypt(encrypted_flow_data_b64: str, aes_key: bytes, initial_vector_b64: str) -> str:
#     """Decrypt using AES-GCM mode (WhatsApp Flows use GCM, not CBC)."""
#     try:
#         # Decode the encrypted data and IV
#         encrypted_flow_data = base64.b64decode(encrypted_flow_data_b64)
#         iv = base64.b64decode(initial_vector_b64)
        
#         # For GCM mode, the last 16 bytes are the authentication tag
#         encrypted_body = encrypted_flow_data[:-16]
#         auth_tag = encrypted_flow_data[-16:]
        
#         # Create GCM cipher
#         cipher = Cipher(
#             algorithms.AES(aes_key),
#             modes.GCM(iv, auth_tag),
#             backend=default_backend()
#         )
        
#         decryptor = cipher.decryptor()
#         decrypted_data = decryptor.update(encrypted_body) + decryptor.finalize()
        
#         return decrypted_data.decode('utf-8')
        
#     except Exception as e:
#         log.error(f"AES-GCM decryption error: {e}")
#         raise

# def _aes_gcm_encrypt(response: str, aes_key: bytes, initial_vector_b64: str) -> str:
#     """Encrypt response using AES-GCM mode with flipped IV."""
#     try:
#         # Decode IV and flip it for response
#         iv = base64.b64decode(initial_vector_b64)
#         flipped_iv = flip_iv(iv)
        
#         # Create GCM cipher with flipped IV
#         cipher = Cipher(
#             algorithms.AES(aes_key),
#             modes.GCM(flipped_iv),
#             backend=default_backend()
#         )
        
#         encryptor = cipher.encryptor()
#         encrypted = encryptor.update(response.encode('utf-8')) + encryptor.finalize()
        
#         # Combine encrypted data with auth tag
#         encrypted_response = encrypted + encryptor.tag
        
#         # Return base64 encoded
#         return base64.b64encode(encrypted_response).decode('utf-8')
        
#     except Exception as e:
#         log.error(f"AES-GCM encryption error: {e}")
#         raise

# # Static dropdown data for the flow
# FLOW_DATA = {
#     "societies": [
#         {"id": "amrapali_sapphire", "title": "Amrapali Sapphire"},
#         {"id": "parsvnath_prestige", "title": "Parsvnath Prestige"},
#         {"id": "other", "title": "Other"},
#     ],
#     "genders": [
#         {"id": "male", "title": "Male"},
#         {"id": "female", "title": "Female"},
#         {"id": "other", "title": "Other"},
#         {"id": "prefer_not_to_say", "title": "Prefer not to say"},
#     ],
#     "age_groups": [
#         {"id": "18_24", "title": "18-24 years"},
#         {"id": "25_34", "title": "25-34 years"},
#         {"id": "35_44", "title": "35-44 years"},
#         {"id": "45_54", "title": "45-54 years"},
#         {"id": "55_64", "title": "55-64 years"},
#         {"id": "65_plus", "title": "65+ years"},
#     ],
# }

# @bp.post("/flow/onboarding")
# def onboarding_flow():
#     """Handle WhatsApp Flow requests including health checks."""
#     try:
#         # Get request data
#         raw = request.get_json(silent=True)
        
#         # Health check - empty request body
#         if not raw or raw == {}:
#             log.info("Health check received - returning empty 200 response")
#             return "", 200
        
#         log.info(f"Received request with keys: {list(raw.keys())}")
        
#         # Check if data is encrypted
#         is_encrypted = "encrypted_flow_data" in raw
        
#         if is_encrypted:
#             log.info("Processing encrypted request")
#             try:
#                 # Decrypt AES key using RSA
#                 encrypted_aes_key = raw.get("encrypted_aes_key", "")
#                 log.info(f"Encrypted AES key length: {len(encrypted_aes_key)}")
                
#                 aes_key = _rsa_decrypt(encrypted_aes_key)
#                 log.info(f"AES key decrypted successfully, length: {len(aes_key)} bytes")
                
#                 # Decrypt flow data using AES-GCM
#                 encrypted_flow_data = raw.get("encrypted_flow_data", "")
#                 initial_vector = raw.get("initial_vector", "")
                
#                 decrypted_json = _aes_gcm_decrypt(encrypted_flow_data, aes_key, initial_vector)
#                 payload = json.loads(decrypted_json)
#                 log.info(f"Decrypted payload: {json.dumps(payload, indent=2)}")
                
#             except Exception as exc:
#                 log.exception(f"Decryption failed: {exc}")
#                 # Return proper error code for WhatsApp
#                 return jsonify({"error_type": "DECRYPTION_FAILED"}), 421
#         else:
#             # Unencrypted request (shouldn't happen in production)
#             log.warning("Received unencrypted flow data")
#             payload = raw
#             aes_key = None
#             is_encrypted = False
        
#         # Process the flow action
#         action = payload.get("action", "")
#         version = payload.get("version", "3.0")
#         log.info(f"Processing action: {action}, version: {version}")
        
#         # Handle different actions
#         if action.lower() == "ping":
#             # WhatsApp health check ping
#             resp_obj = {
#                 "version": version,
#                 "data": {
#                     "status": "active"
#                 }
#             }
            
#         elif action.upper() == "INIT":
#             # Initialize flow with data
#             resp_obj = {
#                 "version": version,
#                 "screen": "ONBOARDING",
#                 "data": FLOW_DATA
#             }
            
#         elif action.upper() == "DATA_EXCHANGE":
#             # Handle form data submission
#             screen = payload.get("screen", "")
#             data = payload.get("data", {})
            
#             log.info(f"Data exchange for screen: {screen}, data: {data}")
            
#             # Validate submitted data
#             errors = {}
            
#             if not data.get("society"):
#                 errors["society"] = "Please select your society"
#             elif data.get("society") == "other" and not data.get("custom_society", "").strip():
#                 errors["custom_society"] = "Please enter your society name"
            
#             if not data.get("gender"):
#                 errors["gender"] = "Please select your gender"
            
#             if not data.get("age_group"):
#                 errors["age_group"] = "Please select your age group"
            
#             if errors:
#                 # Return errors to display on the same screen
#                 resp_obj = {
#                     "version": version,
#                     "screen": "ONBOARDING",
#                     "data": {
#                         **FLOW_DATA,
#                         "error": errors
#                     }
#                 }
#             else:
#                 # Data is valid, complete the flow
#                 log.info(f"Onboarding completed successfully with data: {data}")
                
#                 # Send success response that closes the flow
#                 resp_obj = {
#                     "version": version,
#                     "screen": "SUCCESS",
#                     "data": {
#                         "extension_message_response": {
#                             "params": {
#                                 "flow_token": payload.get("flow_token", ""),
#                                 "status": "completed",
#                                 "society": data.get("society"),
#                                 "gender": data.get("gender"),
#                                 "age_group": data.get("age_group")
#                             }
#                         }
#                     }
#                 }
                
#         else:
#             log.warning(f"Unknown action received: {action}")
#             return jsonify({"error_type": "UNKNOWN_ACTION"}), 422
        
#         # Encrypt response if request was encrypted
#         if is_encrypted and aes_key:
#             log.info("Encrypting response")
#             response_json = json.dumps(resp_obj)
#             encrypted_response = _aes_gcm_encrypt(response_json, aes_key, raw["initial_vector"])
#             # WhatsApp expects the encrypted response as plain text, not JSON
#             return encrypted_response, 200, {'Content-Type': 'text/plain'}
#         else:
#             # Return unencrypted response (for health checks)
#             return jsonify(resp_obj), 200
            
#     except Exception as e:
#         log.exception(f"Unexpected error in flow endpoint: {e}")
#         return jsonify({"error_type": "INTERNAL_ERROR"}), 500

# # Additional health check endpoint for debugging
# @bp.get("/flow/health")
# def health_check():
#     """Simple health check endpoint for testing."""
#     return jsonify({"status": "healthy", "endpoint": "onboarding_flow"}), 200



from flask import Blueprint, request, jsonify
import logging
import json
import base64
import os
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding as asympad
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from pathlib import Path

bp = Blueprint("flow_handler", __name__)
log = logging.getLogger(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)

# ───────────────────────────────────────────
# Load RSA private key - GCP mounts secrets at /secrets/{SECRET_NAME}
_key_path = Path(os.getenv("FLOW_PRIVATE_KEY", "/secrets/Flow_Private_Key"))
if not _key_path.exists():
    # Fallback for local development
    alt_path = Path(__file__).resolve().parent / "private.pem"
    if alt_path.exists():
        _key_path = alt_path
    else:
        raise RuntimeError(f"Private key not found at {_key_path}. Check GCP Secret mount or set FLOW_PRIVATE_KEY env var.")

with open(_key_path, 'rb') as key_file:
    _private_key = serialization.load_pem_private_key(
        key_file.read(), 
        password=None,
        backend=default_backend()
    )

def _rsa_decrypt(encrypted_aes_key_b64: str) -> bytes:
    """Decrypt RSA encrypted AES key using OAEP with SHA256."""
    encrypted_aes_key = base64.b64decode(encrypted_aes_key_b64)
    try:
        # WhatsApp uses OAEP with SHA256
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
    """Decrypt using AES-GCM mode (WhatsApp Flows use GCM, not CBC)."""
    try:
        # Decode the encrypted data and IV
        encrypted_flow_data = base64.b64decode(encrypted_flow_data_b64)
        iv = base64.b64decode(initial_vector_b64)
        
        # For GCM mode, the last 16 bytes are the authentication tag
        encrypted_body = encrypted_flow_data[:-16]
        auth_tag = encrypted_flow_data[-16:]
        
        # Create GCM cipher
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
        # Decode IV and flip it for response
        iv = base64.b64decode(initial_vector_b64)
        flipped_iv = flip_iv(iv)
        
        # Create GCM cipher with flipped IV
        cipher = Cipher(
            algorithms.AES(aes_key),
            modes.GCM(flipped_iv),
            backend=default_backend()
        )
        
        encryptor = cipher.encryptor()
        encrypted = encryptor.update(response.encode('utf-8')) + encryptor.finalize()
        
        # Combine encrypted data with auth tag
        encrypted_response = encrypted + encryptor.tag
        
        # Return base64 encoded
        return base64.b64encode(encrypted_response).decode('utf-8')
        
    except Exception as e:
        log.error(f"AES-GCM encryption error: {e}")
        raise

# Static dropdown data for onboarding flow
ONBOARDING_DATA = {
    "societies": [
        {"id": "amrapali_sapphire", "title": "Amrapali Sapphireeeeeeeeeeeeee"},
        {"id": "parsvnath_prestige", "title": "Parsvnath Prestigeeeeeeeeeee"},
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

def get_product_recommendations():
    """Get product recommendations - replace with your actual function."""
    # Replace this with your actual product recommendation logic
    products = [
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
    return products

def get_product_by_id(product_id):
    """Get specific product details by ID."""
    products = get_product_recommendations()
    for product in products:
        if product["id"] == product_id:
            return product
    return None

def handle_onboarding_flow(payload, version="7.2"):
    """Handle onboarding flow actions."""
    action = payload.get("action", "")
    screen = payload.get("screen", "")
    data = payload.get("data", {})
    
    log.info(f"Onboarding flow - Action: {action}, Screen: {screen}")
    
    if action.upper() == "INIT":
        # Initialize onboarding flow
        return {
            "version": version,
            "screen": "ONBOARDING",
            "data": ONBOARDING_DATA
        }
    
    elif action.upper() == "DATA_EXCHANGE":
        # Handle onboarding form submission
        log.info(f"Onboarding data exchange: {data}")
        
        # Check if "other" society was selected to show custom input
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
            # Return errors to display on the same screen
            return {
                "version": version,
                "screen": "ONBOARDING",
                "data": {
                    **ONBOARDING_DATA,
                    "error": errors
                }
            }
        else:
            # Data is valid, complete the flow
            log.info(f"Onboarding completed successfully with data: {data}")
            return {
                "version": version,
                "screen": "COMPLETE",
                "data": {}
            }

def handle_product_recommendation_flow(payload, version="7.2"):
    """Handle product recommendation flow actions."""
    action = payload.get("action", "")
    screen = payload.get("screen", "")
    data = payload.get("data", {})
    
    log.info(f"Product flow - Action: {action}, Screen: {screen}")
    
    if action.upper() == "INIT":
        # Initialize product recommendation flow
        products = get_product_recommendations()
        
        # Create dropdown options from products
        product_options = [
            {"id": product["id"], "title": product["title"]} 
            for product in products
        ]
        
        return {
            "version": version,
            "screen": "PRODUCT_LIST",
            "data": {
                "products": products,
                "product_options": product_options,  # Add this for dropdown
                "header_text": "Best Products for You",
                "footer_text": "Select a product to view details",
                "selected_product_id": ""  # Initialize empty
            }
        }
    
    elif action.upper() == "DATA_EXCHANGE":
        if screen == "PRODUCT_LIST":
            # Handle dropdown selection - update selected product
            selected_product_id = data.get("selected_product_id") or data.get("selected_product")
            log.info(f"Product selected: {selected_product_id}")
            
            products = get_product_recommendations()
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
                    "header_text": "Best Products for You", 
                    "footer_text": "Select a product to view details",
                    "selected_product_id": selected_product_id or ""
                }
            }
        
        elif screen == "PRODUCT_DETAILS" or data.get("screen") == "PRODUCT_DETAILS":
            # This shouldn't happen with current JSON, but keep for safety
            return {
                "version": version,
                "screen": "COMPLETED",
                "data": {
                    "message": "Thank you for viewing our products!"
                }
            }
    
    elif action.upper() == "NAVIGATE":
        # Handle navigation to PRODUCT_DETAILS
        if payload.get("next", {}).get("name") == "PRODUCT_DETAILS":
            product_id = data.get("product_id") or data.get("selected_product_id")
            log.info(f"Navigating to details for product: {product_id}")
            
            if product_id:
                product = get_product_by_id(product_id)
                if product:
                    # Create simple text details that work with your current JSON
                    details_text = f"""
{product['title']}
{product['subtitle']}

Price: {product['price']}
Brand: {product['brand']}
Rating: {product['rating']}/5.0
Status: {product['availability']}

Features:
{chr(10).join(f"• {feature}" for feature in product['features'])}

{product.get('discount', '')}
                    """.strip()
                    
                    return {
                        "version": version,
                        "screen": "PRODUCT_DETAILS",
                        "data": {
                            "product_id": product_id,
                            "product_details": details_text  # Add this field
                        }
                    }
            
            # Fallback if product not found
            return {
                "version": version,
                "screen": "PRODUCT_DETAILS", 
                "data": {
                    "product_id": "unknown",
                    "product_details": "Product details not available."
                }
            }
    
    # Default fallback - return to product list
    products = get_product_recommendations()
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
            "header_text": "Best Products for You",
            "footer_text": "Select a product to view details",
            "selected_product_id": ""
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

def handle_flow_request(flow_type):
    """Unified flow request handler for both onboarding and products."""
    try:
        # Get request data
        raw = request.get_json(silent=True)
        
        # Health check - empty request body
        if not raw or raw == {}:
            log.info(f"Health check received for {flow_type} - returning empty 200 response")
            return "", 200
        
        log.info(f"Received {flow_type} request with keys: {list(raw.keys())}")
        
        # Check if data is encrypted
        is_encrypted = "encrypted_flow_data" in raw
        aes_key = None
        
        if is_encrypted:
            log.info("Processing encrypted request")
            try:
                # Decrypt AES key using RSA
                encrypted_aes_key = raw.get("encrypted_aes_key", "")
                log.info(f"Encrypted AES key length: {len(encrypted_aes_key)}")
                
                aes_key = _rsa_decrypt(encrypted_aes_key)
                log.info(f"AES key decrypted successfully, length: {len(aes_key)} bytes")
                
                # Decrypt flow data using AES-GCM
                encrypted_flow_data = raw.get("encrypted_flow_data", "")
                initial_vector = raw.get("initial_vector", "")
                
                decrypted_json = _aes_gcm_decrypt(encrypted_flow_data, aes_key, initial_vector)
                payload = json.loads(decrypted_json)
                log.info(f"Decrypted payload: {json.dumps(payload, indent=2)}")
                
            except Exception as exc:
                log.exception(f"Decryption failed: {exc}")
                return jsonify({"error_type": "DECRYPTION_FAILED"}), 421
        else:
            # Unencrypted request (shouldn't happen in production)
            log.warning("Received unencrypted flow data")
            payload = raw
        
        # Process the flow action
        action = payload.get("action", "")
        version = payload.get("version", "7.2")
        
        log.info(f"Processing {flow_type} - action: {action}, version: {version}")
        
        # Handle different actions
        if action.lower() == "ping":
            # WhatsApp health check ping
            resp_obj = {
                "version": version,
                "data": {
                    "status": "active"
                }
            }
        else:
            # Route to appropriate flow handler
            if flow_type == "onboarding":
                resp_obj = handle_onboarding_flow(payload, version)
            elif flow_type == "products":
                resp_obj = handle_product_recommendation_flow(payload, version)
            else:
                log.warning(f"Unknown flow type: {flow_type}")
                return jsonify({"error_type": "UNKNOWN_FLOW_TYPE"}), 422
        
        log.info(f"Response object: {json.dumps(resp_obj, indent=2)}")
        
        # Encrypt response if request was encrypted
        if is_encrypted and aes_key:
            log.info("Encrypting response")
            response_json = json.dumps(resp_obj)
            encrypted_response = _aes_gcm_encrypt(response_json, aes_key, raw["initial_vector"])
            return encrypted_response, 200, {'Content-Type': 'text/plain'}
        else:
            # Return unencrypted response (for health checks)
            return jsonify(resp_obj), 200
            
    except Exception as e:
        log.exception(f"Unexpected error in {flow_type} flow endpoint: {e}")
        return jsonify({"error_type": "INTERNAL_ERROR"}), 500

# Health check endpoints for debugging
@bp.get("/flow/health")
def health_check():
    """Simple health check endpoint for testing."""
    return jsonify({"status": "healthy", "endpoints": ["onboarding", "products"]}), 200

@bp.get("/flow/onboarding/health")
def onboarding_health():
    """Health check for onboarding flow."""
    return jsonify({"status": "healthy", "flow": "onboarding"}), 200

@bp.get("/flow/products/health") 
def products_health():
    """Health check for products flow."""
    return jsonify({"status": "healthy", "flow": "products"}), 200