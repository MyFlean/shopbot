# shopping_bot/routes/onboarding_flow.py
from flask import Blueprint, request, jsonify
import logging, json, base64, os
from cryptography.hazmat.primitives import serialization, padding as sympad, hashes
from cryptography.hazmat.primitives.asymmetric import padding as asympad
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from pathlib import Path

bp = Blueprint("onboarding_flow", __name__)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────
# 1. Resolve private-key path
_key_path = Path(
    os.getenv("FLOW_PRIVATE_KEY", Path(__file__).resolve().parent / "private.pem")
)
if not _key_path.exists():
    raise RuntimeError(
        f"private.pem not found. "
        f"Set env FLOW_PRIVATE_KEY or place key at {_key_path}"
    )

# 2. Load RSA private key
_private_key = serialization.load_pem_private_key(
    _key_path.read_bytes(), password=None
)
# ──────────────────────────────────────────────────────────
def _rsa_decrypt(b64_cipher: str) -> bytes:
    return _private_key.decrypt(
        base64.b64decode(b64_cipher),
        asympad.OAEP(
            mgf=asympad.MGF1(hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )

def _aes_decrypt(b64_cipher: str, aes_key: bytes, b64_iv: str) -> bytes:
    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(base64.b64decode(b64_iv)))
    decryptor = cipher.decryptor()
    padded = decryptor.update(base64.b64decode(b64_cipher)) + decryptor.finalize()
    unpadder = sympad.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()

def _aes_encrypt(plain: bytes, aes_key: bytes, b64_iv: str) -> str:
    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(base64.b64decode(b64_iv)))
    encryptor = cipher.encryptor()
    padder = sympad.PKCS7(128).padder()
    padded = padder.update(plain) + padder.finalize()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode()


# ──────────────────────────────────────────────────────────
INITIAL_DATA = {
    "societies": [
        {"id": "amrapali_sapphire", "title": "Amrapali Sapphire"},
        {"id": "parsvnath_prestige", "title": "Parsvnath Prestige"},
        {"id": "other", "title": "Other"},
    ],
    "show_custom_society": False,
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

# ──────────────────────────────────────────────────────────
@bp.post("/flow/onboarding")
def onboarding_flow():
    raw = request.get_json(silent=True) or {}

    # 0. Health-check → Meta sends an empty body
    if not raw:
        return jsonify({}), 200

    # 1. Determine whether payload is encrypted
    encrypted = "encrypted_flow_data" in raw
    if encrypted:
        try:
            aes_key = _rsa_decrypt(raw["encrypted_aes_key"])
            decrypted_bytes = _aes_decrypt(
                raw["encrypted_flow_data"], aes_key, raw["initial_vector"]
            )
            payload = json.loads(decrypted_bytes.decode())
        except Exception as exc:
            log.exception("Decrypt failed: %s", exc)
            return jsonify({"error": "decryption_failed"}), 400
    else:
        payload = raw

    # 2. Handle business logic
    action = payload.get("action")
    if action == "init":
        resp_obj = {"data": INITIAL_DATA}

    elif action == "validate":
        data = payload.get("payload", {})
        errors = {}
        if not data.get("society"):
            errors["society"] = "Please select your society."
        elif data["society"] == "other" and not data.get("custom_society", "").strip():
            errors["custom_society"] = "Please enter your society name."
        if not data.get("gender"):
            errors["gender"] = "Please select your gender."
        if not data.get("age_group"):
            errors["age_group"] = "Please select your age group."
        resp_obj = {"errors": errors} if errors else {}

    elif action == "submit":
        log.info("Onboarding submission: %s", payload.get("payload"))
        return "", 204

    else:
        return jsonify({"error": "Unsupported action"}), 400

    # 3. Encrypt response if request was encrypted
    if encrypted:
        encrypted_resp = _aes_encrypt(
            json.dumps(resp_obj).encode(), aes_key, raw["initial_vector"]
        )
        return jsonify({"encrypted_flow_data": encrypted_resp}), 200
    return jsonify(resp_obj), 200
