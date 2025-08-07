#!/usr/bin/env python3
"""
Test script to verify WhatsApp Flow encryption/decryption
"""

import json
import base64
import os
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from cryptography.hazmat.backends import default_backend

def test_encryption():
    # Load keys
    with open('private.pem', 'rb') as f:
        private_key = serialization.load_pem_private_key(
            f.read(), password=None, backend=default_backend()
        )
    
    with open('public.pem', 'rb') as f:
        public_key = serialization.load_pem_public_key(
            f.read(), backend=default_backend()
        )
    
    # Generate test AES key and IV
    aes_key = os.urandom(32)  # 256-bit key
    iv = os.urandom(16)  # 128-bit IV
    
    # Test data
    test_payload = {
        "action": "ping",
        "version": "3.0",
        "screen": "ONBOARDING",
        "data": {}
    }
    
    print("Original payload:", test_payload)
    
    # Encrypt AES key with RSA public key
    encrypted_aes_key = public_key.encrypt(
        aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None
        )
    )
    encrypted_aes_key_b64 = base64.b64encode(encrypted_aes_key).decode('utf-8')
    print(f"\nEncrypted AES key (base64): {encrypted_aes_key_b64[:50]}...")
    
    # Encrypt payload with AES
    payload_json = json.dumps(test_payload).encode('utf-8')
    
    # Add PKCS7 padding
    padder = PKCS7(128).padder()
    padded_data = padder.update(payload_json) + padder.finalize()
    
    # Encrypt
    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    encrypted_payload = encryptor.update(padded_data) + encryptor.finalize()
    encrypted_payload_b64 = base64.b64encode(encrypted_payload).decode('utf-8')
    
    print(f"Encrypted payload (base64): {encrypted_payload_b64[:50]}...")
    
    # Now decrypt to verify
    print("\n--- Testing Decryption ---")
    
    # Decrypt AES key
    decrypted_aes_key = private_key.decrypt(
        base64.b64decode(encrypted_aes_key_b64),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None
        )
    )
    
    print(f"AES key decrypted successfully: {decrypted_aes_key == aes_key}")
    
    # Decrypt payload
    cipher = Cipher(algorithms.AES(decrypted_aes_key), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    decrypted_padded = decryptor.update(base64.b64decode(encrypted_payload_b64)) + decryptor.finalize()
    
    # Remove padding
    unpadder = PKCS7(128).unpadder()
    decrypted_payload = unpadder.update(decrypted_padded) + unpadder.finalize()
    
    decrypted_json = json.loads(decrypted_payload.decode('utf-8'))
    print(f"Payload decrypted successfully: {decrypted_json == test_payload}")
    print("Decrypted payload:", decrypted_json)
    
    # Create test request format
    test_request = {
        "encrypted_aes_key": encrypted_aes_key_b64,
        "encrypted_flow_data": encrypted_payload_b64,
        "initial_vector": base64.b64encode(iv).decode('utf-8')
    }
    
    print("\n--- Sample Request Format ---")
    print(json.dumps(test_request, indent=2))
    
    return test_request

if __name__ == "__main__":
    test_encryption()