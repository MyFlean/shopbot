#!/usr/bin/env python3
"""
Meta WhatsApp Business Encryption API - Sign Public Key
Updated with correct endpoint paths
"""

import requests
import json
import sys
from pathlib import Path

# Your credentials
ACCESS_TOKEN = "EAAKYatDCyzIBPK4HoR037aoFADy4ikUQG3zBQ1AYiSWaf09FNGr1Ao6bxVNSfSZCKsG8AXABqSs7dV0OnSrDeNZCbSmnNZAH5HiCm74ccB4ONuEwjn5sbEB8BQ9WyJZBZBnYoYI27uqcwLJHWGzTYutL45hNU1aHvHpeIb1sIBfuKRYW8YC3niTHIwhhOxgZDZD"

API_VERSION = "v21.0"
BASE_URL = f"https://graph.facebook.com/{API_VERSION}"
PHONE_NUMBER_ID = "791841894005252"

# Your public key content
PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAsEAdf+uUYYR2WaOTJKNo
d0yXgg+UxCY5A5uLPeRW6HwaqTFWbVsnWqnslJG/RZnl9vJ+jdLobi0Of/x3g8TJ
ulMefWSlCrC1618YbuY1wAAguGTOatm27aAiCVJV2sk5i1Cg2ik7QcUYTLaIQbGc
yFHbMrGP+5qkWH8Bj9P8jyDzImZqBwSoUxXR757/vCCsEU/wqcIVJ1EacKdi9g6s
tLFMRJOjIekc3OQjhJP6j00bAgtiGhOqkU+Lx+HTHmMk1ekBXZLbn2DE8tc61Fl/
v5+TbBVmeZQ5bDnWzKH8rLy1cqb3+RACGsfOEEXRGrVoaX8Z1JCPYcVq4OwCvpMD
rQIDAQAB
-----END PUBLIC KEY-----"""



base = "https://graph.facebook.com/v21.0"
headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}

# Set (sign) the key
resp = requests.post(
    f"{base}/{PHONE_NUMBER_ID}/whatsapp_business_encryption",
    headers=headers,
    data={"business_public_key": PUBLIC_KEY},  # form-encoded
)
print("SET:", resp.status_code, resp.text)

# Verify
check = requests.get(
    f"{base}/{PHONE_NUMBER_ID}/whatsapp_business_encryption", headers=headers
)
print("GET:", check.status_code, check.text)
