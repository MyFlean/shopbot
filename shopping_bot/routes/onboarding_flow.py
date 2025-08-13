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

# bp = Blueprint("flow_handler", __name__)
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

# # Static dropdown data for onboarding flow
# ONBOARDING_DATA = {
#     "societies": [
#         {"id": "amrapali_sapphire", "title": "Amrapali Sapphireeeeeeeeeeeeee"},
#         {"id": "parsvnath_prestige", "title": "Parsvnath Prestigeeeeeeeeeee"},
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
#     "show_custom_society": False
# }

# def get_product_recommendations():
#     """Get product recommendations - replace with your actual function."""
#     # Replace this with your actual product recommendation logic
#     products = [
#         {
#             "id": "prod_90459",
#             "title": "ASUS TUF Gaming A15",
#             "subtitle": "AMD Ryzen 5 7535HS, RTX 3050",
#             "price": "$699",
#             "brand": "ASUS",
#             "rating": 4.2,
#             "availability": "In Stock",
#             "discount": "Save $100",
#             "image": "https://via.placeholder.com/150x150/4CAF50/FFFFFF?text=ASUS",
#             "features": ["15.6\" 144Hz Display", "16GB RAM", "512GB SSD", "RTX 3050 Graphics"]
#         },
#         {
#             "id": "prod_97199", 
#             "title": "Acer Nitro 5",
#             "subtitle": "Intel i5-12500H, RTX 3050 Ti",
#             "price": "$799",
#             "brand": "Acer",
#             "rating": 4.1,
#             "availability": "In Stock",
#             "discount": "",
#             "image": "https://via.placeholder.com/150x150/FF5722/FFFFFF?text=Acer",
#             "features": ["15.6\" 144Hz IPS", "16GB RAM", "512GB NVMe", "RTX 3050 Ti"]
#         },
#         {
#             "id": "prod_84521",
#             "title": "HP Pavilion Gaming",
#             "subtitle": "AMD Ryzen 7 5800H, GTX 1650",
#             "price": "$649",
#             "brand": "HP",
#             "rating": 3.9,
#             "availability": "In Stock", 
#             "discount": "Save $50",
#             "image": "https://via.placeholder.com/150x150/9C27B0/FFFFFF?text=HP",
#             "features": ["15.6\" FHD Display", "8GB RAM", "256GB SSD", "GTX 1650 Graphics"]
#         }
#     ]
#     return products

# def get_product_by_id(product_id):
#     """Get specific product details by ID."""
#     products = get_product_recommendations()
#     for product in products:
#         if product["id"] == product_id:
#             return product
#     return None

# def handle_onboarding_flow(payload, version="7.2"):
#     """Handle onboarding flow actions."""
#     action = payload.get("action", "")
#     screen = payload.get("screen", "")
#     data = payload.get("data", {})
    
#     log.info(f"Onboarding flow - Action: {action}, Screen: {screen}")
    
#     if action.upper() == "INIT":
#         # Initialize onboarding flow
#         return {
#             "version": version,
#             "screen": "ONBOARDING",
#             "data": ONBOARDING_DATA
#         }
    
#     elif action.upper() == "DATA_EXCHANGE":
#         # Handle onboarding form submission
#         log.info(f"Onboarding data exchange: {data}")
        
#         # Check if "other" society was selected to show custom input
#         if data.get("selected_society") == "other":
#             onboarding_data_with_custom = ONBOARDING_DATA.copy()
#             onboarding_data_with_custom["show_custom_society"] = True
#             return {
#                 "version": version,
#                 "screen": "ONBOARDING", 
#                 "data": onboarding_data_with_custom
#             }
        
#         # Validate complete form submission
#         errors = {}
        
#         if not data.get("society"):
#             errors["society"] = "Please select your society"
#         elif data.get("society") == "other" and not data.get("custom_society", "").strip():
#             errors["custom_society"] = "Please enter your society name"
        
#         if not data.get("gender"):
#             errors["gender"] = "Please select your gender"
        
#         if not data.get("age_group"):
#             errors["age_group"] = "Please select your age group"
        
#         if errors:
#             # Return errors to display on the same screen
#             return {
#                 "version": version,
#                 "screen": "ONBOARDING",
#                 "data": {
#                     **ONBOARDING_DATA,
#                     "error": errors
#                 }
#             }
#         else:
#             # Data is valid, complete the flow
#             log.info(f"Onboarding completed successfully with data: {data}")
#             return {
#                 "version": version,
#                 "screen": "COMPLETE",
#                 "data": {}
#             }

# def handle_product_recommendation_flow(payload, version="7.2"):
#     """Handle product recommendation flow actions."""
#     action = payload.get("action", "")
#     screen = payload.get("screen", "")
#     data = payload.get("data", {})
    
#     log.info(f"Product flow - Action: {action}, Screen: {screen}")
    
#     if action.upper() == "INIT":
#         # Initialize product recommendation flow
#         products = get_product_recommendations()
#         return {
#             "version": version,
#             "screen": "PRODUCT_LIST",
#             "data": {
#                 "products": products,
#                 "header_text": "Best Products for You",
#                 "footer_text": "Select a product to view details"
#             }
#         }
    
#     elif action.upper() == "DATA_EXCHANGE":
#         if screen == "PRODUCT_DETAILS" or data.get("screen") == "PRODUCT_DETAILS":
#             # Handle product selection from dropdown
#             selected_product_id = data.get("selected_product_id")
#             log.info(f"Selected product ID: {selected_product_id}")
            
#             if selected_product_id:
#                 product = get_product_by_id(selected_product_id)
#                 if product:
#                     return {
#                         "version": version,
#                         "screen": "PRODUCT_DETAILS",
#                         "data": {
#                             "product": product
#                         }
#                     }
            
#             # If no product found, return to list
#             products = get_product_recommendations()
#             return {
#                 "version": version,
#                 "screen": "PRODUCT_LIST",
#                 "data": {
#                     "products": products,
#                     "header_text": "Best Products for You",
#                     "footer_text": "Select a product to view details"
#                 }
#             }
        
#         elif screen == "SUCCESS" or data.get("screen") == "SUCCESS":
#             # Handle product actions (buy, save, etc.)
#             action_type = data.get("action")
#             product_id = data.get("product_id")
            
#             if action_type == "buy":
#                 message = f"Great choice! We'll send you the purchase link shortly."
#             elif action_type == "save":
#                 message = f"Product has been saved to your wishlist!"
#             else:
#                 message = "Thank you for using our product recommendation service!"
            
#             return {
#                 "version": version,
#                 "screen": "SUCCESS",
#                 "data": {
#                     "message": message
#                 }
#             }
    
#     # Default fallback
#     products = get_product_recommendations()
#     return {
#         "version": version,
#         "screen": "PRODUCT_LIST",
#         "data": {
#             "products": products,
#             "header_text": "Best Products for You",
#             "footer_text": "Select a product to view details"
#         }
#     }

# @bp.post("/flow/onboarding")
# def onboarding_flow():
#     """Handle onboarding WhatsApp Flow requests."""
#     return handle_flow_request("onboarding")

# @bp.post("/flow/products")
# def product_flow():
#     """Handle product recommendation WhatsApp Flow requests."""
#     return handle_flow_request("products")

# def handle_flow_request(flow_type):
#     """Unified flow request handler for both onboarding and products."""
#     try:
#         # Get request data
#         raw = request.get_json(silent=True)
        
#         # Health check - empty request body
#         if not raw or raw == {}:
#             log.info(f"Health check received for {flow_type} - returning empty 200 response")
#             return "", 200
        
#         log.info(f"Received {flow_type} request with keys: {list(raw.keys())}")
        
#         # Check if data is encrypted
#         is_encrypted = "encrypted_flow_data" in raw
#         aes_key = None
        
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
#                 return jsonify({"error_type": "DECRYPTION_FAILED"}), 421
#         else:
#             # Unencrypted request (shouldn't happen in production)
#             log.warning("Received unencrypted flow data")
#             payload = raw
        
#         # Process the flow action
#         action = payload.get("action", "")
#         version = payload.get("version", "7.2")
        
#         log.info(f"Processing {flow_type} - action: {action}, version: {version}")
        
#         # Handle different actions
#         if action.lower() == "ping":
#             # WhatsApp health check ping
#             resp_obj = {
#                 "version": version,
#                 "data": {
#                     "status": "active"
#                 }
#             }
#         else:
#             # Route to appropriate flow handler
#             if flow_type == "onboarding":
#                 resp_obj = handle_onboarding_flow(payload, version)
#             elif flow_type == "products":
#                 resp_obj = handle_product_recommendation_flow(payload, version)
#             else:
#                 log.warning(f"Unknown flow type: {flow_type}")
#                 return jsonify({"error_type": "UNKNOWN_FLOW_TYPE"}), 422
        
#         log.info(f"Response object: {json.dumps(resp_obj, indent=2)}")
        
#         # Encrypt response if request was encrypted
#         if is_encrypted and aes_key:
#             log.info("Encrypting response")
#             response_json = json.dumps(resp_obj)
#             encrypted_response = _aes_gcm_encrypt(response_json, aes_key, raw["initial_vector"])
#             return encrypted_response, 200, {'Content-Type': 'text/plain'}
#         else:
#             # Return unencrypted response (for health checks)
#             return jsonify(resp_obj), 200
            
#     except Exception as e:
#         log.exception(f"Unexpected error in {flow_type} flow endpoint: {e}")
#         return jsonify({"error_type": "INTERNAL_ERROR"}), 500

# # Health check endpoints for debugging
# @bp.get("/flow/health")
# def health_check():
#     """Simple health check endpoint for testing."""
#     return jsonify({"status": "healthy", "endpoints": ["onboarding", "products"]}), 200

# @bp.get("/flow/onboarding/health")
# def onboarding_health():
#     """Health check for onboarding flow."""
#     return jsonify({"status": "healthy", "flow": "onboarding"}), 200

# @bp.get("/flow/products/health") 
# def products_health():
#     """Health check for products flow."""
#     return jsonify({"status": "healthy", "flow": "products"}), 200



"""
flow_handler.py  •  Drop-in replacement

– Supports encrypted WhatsApp Flows (AES-GCM + RSA) as before.
– On INIT of the product-recommendation flow it now returns:
    • products          → full objects (if you need them later)
    • product_options   → [{id,title}, …] for the Dropdown (mandatory)
"""

from flask import Blueprint, request, jsonify
import logging, json, base64, os
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding as asympad
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from pathlib import Path

bp = Blueprint("flow_handler", __name__)
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ──────────────────── RSA PRIVATE KEY (for encrypted requests) ─────
_key_path = Path(os.getenv("FLOW_PRIVATE_KEY", "/secrets/Flow_Private_Key"))
if not _key_path.exists():
    fallback = Path(__file__).resolve().parent / "private.pem"
    _key_path = fallback if fallback.exists() else _key_path
    if not _key_path.exists():
        raise RuntimeError(
            f"Private key not found at {_key_path}. "
            "Ensure secret is mounted or set FLOW_PRIVATE_KEY env var."
        )

with open(_key_path, "rb") as kf:
    _private_key = serialization.load_pem_private_key(
        kf.read(), password=None, backend=default_backend()
    )

# ────────────────────  Encryption helpers  ─────────────────────────
def _rsa_decrypt(enc_key_b64: str) -> bytes:
    data = base64.b64decode(enc_key_b64)
    return _private_key.decrypt(
        data,
        asympad.OAEP(
            mgf=asympad.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )

def _flip_iv(iv: bytes) -> bytes:
    return bytes(b ^ 0xFF for b in iv)

def _aes_gcm_decrypt(enc_b64: str, key: bytes, iv_b64: str) -> str:
    enc = base64.b64decode(enc_b64)
    iv = base64.b64decode(iv_b64)
    ciphertext, tag = enc[:-16], enc[-16:]
    cipher = Cipher(algorithms.AES(key), modes.GCM(iv, tag), backend=default_backend())
    dec = cipher.decryptor()
    return (dec.update(ciphertext) + dec.finalize()).decode("utf-8")

def _aes_gcm_encrypt(plaintext: str, key: bytes, iv_b64: str) -> str:
    iv = _flip_iv(base64.b64decode(iv_b64))
    cipher = Cipher(algorithms.AES(key), modes.GCM(iv), backend=default_backend())
    enc = cipher.encryptor()
    ciphertext = enc.update(plaintext.encode("utf-8")) + enc.finalize()
    return base64.b64encode(ciphertext + enc.tag).decode("utf-8")

# ────────────────────  Static datasets  ────────────────────────────
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
    "show_custom_society": False,
}

def get_product_recommendations():
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
            "features": ["15.6\" FHD Display", "8GB RAM", "256GB SSD", "GTX 1650"],
        },
    ]

def build_product_options(products):
    return [{"id": p["id"], "title": p["title"]} for p in products]

# ────────────────────  Flow handlers  ───────────────────────────────
def handle_onboarding_flow(payload, version="7.2"):
    action = (payload.get("action") or "").upper()
    data = payload.get("data", {})

    if action == "INIT":
        return {"version": version, "screen": "ONBOARDING", "data": ONBOARDING_DATA}

    if action == "DATA_EXCHANGE":
        if data.get("selected_society") == "other":
            clone = {**ONBOARDING_DATA, "show_custom_society": True}
            return {"version": version, "screen": "ONBOARDING", "data": clone}

        # (validate, then return COMPLETE) – omitted for brevity
        return {"version": version, "screen": "COMPLETE", "data": {}}

    return {"version": version, "screen": "ONBOARDING", "data": ONBOARDING_DATA}

def handle_product_recommendation_flow(payload, version="7.2"):
    action = (payload.get("action") or "").upper()
    products = get_product_recommendations()
    product_options = build_product_options(products)

    init_obj = {
        "version": version,
        "screen": "PRODUCT_LIST",
        "data": {
            "products": products,
            "product_options": product_options,
            "header_text": "Best Products for You",
            "footer_text": "Select a product to view details",
        },
    }

    if action == "INIT":
        return init_obj

    # For this simple demo the flow is static after INIT; just re-serve the same data
    return init_obj

# ────────────────────  Unified endpoint logic  ──────────────────────
def _process_payload(flow_type, payload):
    version = payload.get("version", "7.2")

    if flow_type == "onboarding":
        return handle_onboarding_flow(payload, version)
    if flow_type == "products":
        return handle_product_recommendation_flow(payload, version)
    raise ValueError("Unknown flow type")

def handle_flow_request(flow_type):
    try:
        raw = request.get_json(silent=True)
        if not raw:
            return "", 200  # health ping

        encrypted = "encrypted_flow_data" in raw
        if encrypted:
            aes_key = _rsa_decrypt(raw["encrypted_aes_key"])
            decrypted = _aes_gcm_decrypt(raw["encrypted_flow_data"], aes_key, raw["initial_vector"])
            payload = json.loads(decrypted)
        else:
            payload = raw

        resp_obj = _process_payload(flow_type, payload)

        if encrypted:
            response_json = json.dumps(resp_obj)
            enc_resp = _aes_gcm_encrypt(response_json, aes_key, raw["initial_vector"])
            return enc_resp, 200, {"Content-Type": "text/plain"}

        return jsonify(resp_obj), 200

    except Exception as exc:
        log.exception("Flow handler error")
        return jsonify({"error_type": "INTERNAL_ERROR"}), 500

# ────────────────────  Flask routes  ────────────────────────────────
@bp.post("/flow/onboarding")
def onboarding_flow():
    return handle_flow_request("onboarding")

@bp.post("/flow/products")
def product_flow():
    return handle_flow_request("products")

@bp.get("/flow/health")
def health():
    return jsonify({"status": "healthy", "endpoints": ["onboarding", "products"]}), 200
