from flask import Blueprint, request, jsonify
import logging
import json
import base64
import os
from cryptography.hazmat.primitives import serialization, padding as sympad, hashes
from cryptography.hazmat.primitives.asymmetric import padding as asympad
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from pathlib import Path

bp = Blueprint("onboarding_flow", __name__)
log = logging.getLogger(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)

# ───────────────────────────────────────────
# Load RSA private key - GCP mounts secrets at /secrets/{SECRET_NAME}
_key_path = Path(os.getenv("FLOW_PRIVATE_KEY", "/secrets/Flow_Private_Key"))
if not _key_path.exists():
    # Fallback to check if it's a local development environment
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

def _b64_decode(data: str) -> bytes:
    """Decode base64 with proper padding - handles WhatsApp's format."""
    # Remove all whitespace
    data = ''.join(data.split())
    # Add padding if needed
    missing_padding = len(data) % 4
    if missing_padding:
        data += '=' * (4 - missing_padding)
    return base64.b64decode(data)

def _rsa_decrypt(b64_cipher: str) -> bytes:
    """Decrypt RSA using OAEP with SHA256."""
    cipher_bytes = _b64_decode(b64_cipher)
    try:
        # WhatsApp uses OAEP with SHA256
        return _private_key.decrypt(
            cipher_bytes,
            asympad.OAEP(
                mgf=asympad.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )
    except Exception as e:
        log.error(f"RSA decryption with SHA256 failed: {e}")
        # Fallback to SHA1 if SHA256 fails (for compatibility)
        try:
            return _private_key.decrypt(
                cipher_bytes,
                asympad.OAEP(
                    mgf=asympad.MGF1(algorithm=hashes.SHA1()),
                    algorithm=hashes.SHA1(),
                    label=None
                )
            )
        except Exception as e2:
            log.error(f"RSA decryption with SHA1 also failed: {e2}")
            raise

def _aes_decrypt(b64_cipher: str, aes_key: bytes, b64_iv: str) -> bytes:
    """Decrypt AES-CBC with PKCS7 padding."""
    cipher_bytes = _b64_decode(b64_cipher)
    iv_bytes = _b64_decode(b64_iv)
    
    cipher = Cipher(
        algorithms.AES(aes_key), 
        modes.CBC(iv_bytes),
        backend=default_backend()
    )
    decryptor = cipher.decryptor()
    padded = decryptor.update(cipher_bytes) + decryptor.finalize()
    
    unpadder = sympad.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()

def _aes_encrypt(plain: bytes, aes_key: bytes, b64_iv: str) -> str:
    """Encrypt AES-CBC with PKCS7 padding."""
    iv_bytes = _b64_decode(b64_iv)
    
    padder = sympad.PKCS7(128).padder()
    padded = padder.update(plain) + padder.finalize()
    
    cipher = Cipher(
        algorithms.AES(aes_key), 
        modes.CBC(iv_bytes),
        backend=default_backend()
    )
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    
    return base64.b64encode(encrypted).decode('utf-8')

# Static dropdown data for the flow
FLOW_DATA = {
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
}

@bp.post("/flow/onboarding")
def onboarding_flow():
    """Handle WhatsApp Flow requests including health checks."""
    try:
        # Get request data
        raw = request.get_json(silent=True)
        
        # Health check - empty request body
        if not raw or raw == {}:
            log.info("Health check received - returning empty 200 response")
            return "", 200
        
        log.info(f"Received request with keys: {list(raw.keys())}")
        
        # Check if data is encrypted
        is_encrypted = "encrypted_flow_data" in raw
        
        if is_encrypted:
            log.info("Processing encrypted request")
            try:
                # Decrypt AES key using RSA
                encrypted_aes_key = raw.get("encrypted_aes_key", "")
                log.info(f"Encrypted AES key length: {len(encrypted_aes_key)}")
                
                aes_key = _rsa_decrypt(encrypted_aes_key)
                log.info(f"AES key decrypted successfully, length: {len(aes_key)} bytes")
                
                # Decrypt flow data using AES
                encrypted_flow_data = raw.get("encrypted_flow_data", "")
                initial_vector = raw.get("initial_vector", "")
                
                decrypted_bytes = _aes_decrypt(encrypted_flow_data, aes_key, initial_vector)
                payload = json.loads(decrypted_bytes.decode('utf-8'))
                log.info(f"Decrypted payload: {json.dumps(payload, indent=2)}")
                
            except Exception as exc:
                log.exception(f"Decryption failed: {exc}")
                # Return proper error code for WhatsApp
                return jsonify({"error_type": "DECRYPTION_FAILED"}), 421
        else:
            # Unencrypted request (shouldn't happen in production)
            log.warning("Received unencrypted flow data")
            payload = raw
            aes_key = None
            is_encrypted = False
        
        # Process the flow action
        action = payload.get("action", "").upper()
        version = payload.get("version", "3.0")
        log.info(f"Processing action: {action}, version: {version}")
        
        # Handle different actions
        if action in ["PING", "ping"]:
            # WhatsApp health check ping
            resp_obj = {
                "version": version,
                "data": {
                    "status": "active"
                }
            }
            
        elif action in ["INIT", "init"]:
            # Initialize flow with data
            resp_obj = {
                "version": version,
                "screen": "ONBOARDING",
                "data": FLOW_DATA
            }
            
        elif action in ["DATA_EXCHANGE", "data_exchange"]:
            # Handle form data submission
            screen = payload.get("screen", "")
            data = payload.get("data", {})
            
            log.info(f"Data exchange for screen: {screen}, data: {data}")
            
            # Validate submitted data
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
                resp_obj = {
                    "version": version,
                    "screen": "ONBOARDING",
                    "data": {
                        **FLOW_DATA,
                        "error": errors
                    }
                }
            else:
                # Data is valid, complete the flow
                log.info(f"Onboarding completed successfully with data: {data}")
                
                # Send success response that closes the flow
                resp_obj = {
                    "version": version,
                    "data": {
                        "status": "completed",
                        "message": "Thank you for completing the onboarding!"
                    }
                }
                
        else:
            log.warning(f"Unknown action received: {action}")
            return jsonify({"error_type": "UNKNOWN_ACTION"}), 422
        
        # Encrypt response if request was encrypted
        if is_encrypted and aes_key:
            log.info("Encrypting response")
            response_json = json.dumps(resp_obj)
            encrypted_response = _aes_encrypt(
                response_json.encode('utf-8'), 
                aes_key, 
                raw["initial_vector"]
            )
            return jsonify({"encrypted_flow_data": encrypted_response}), 200
        else:
            # Return unencrypted response (for health checks)
            return jsonify(resp_obj), 200
            
    except Exception as e:
        log.exception(f"Unexpected error in flow endpoint: {e}")
        return jsonify({"error_type": "INTERNAL_ERROR"}), 500

# Additional health check endpoint for debugging
@bp.get("/flow/health")
def health_check():
    """Simple health check endpoint for testing."""
    return jsonify({"status": "healthy", "endpoint": "onboarding_flow"}), 200