#!/usr/bin/env python3
"""
Script to upload public key to WhatsApp Business Account
"""

import requests
import sys

def upload_public_key(phone_number_id, access_token, public_key_path):
    """Upload public key to WhatsApp Business Account."""
    
    # Read public key
    with open(public_key_path, 'r') as f:
        public_key = f.read()
    
    # The key should include BEGIN/END lines
    if "BEGIN PUBLIC KEY" not in public_key:
        print("Error: Invalid public key format")
        return False
    
    # Prepare the API endpoint
    url = f"https://graph.facebook.com/v18.0/{phone_number_id}/whatsapp_business_encryption"
    
    # Prepare headers
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    # Prepare payload - key should be sent as a single string with \n for newlines
    payload = {
        "business_public_key": public_key.strip()
    }
    
    print(f"Uploading public key to phone number: {phone_number_id}")
    print(f"API URL: {url}")
    
    # Make the request
    response = requests.post(url, headers=headers, json=payload)
    
    # Check response
    if response.status_code == 200:
        print("✅ Public key uploaded successfully!")
        print(f"Response: {response.json()}")
        return True
    else:
        print(f"❌ Failed to upload public key")
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.text}")
        
        # Common error messages
        if response.status_code == 400:
            print("\nPossible issues:")
            print("- Invalid public key format")
            print("- Key doesn't match RSA 2048-bit format")
            print("- Missing permissions")
        elif response.status_code == 401:
            print("\nAccess token is invalid or expired")
        
        return False

if __name__ == "__main__":
    # Configuration - Replace these with your actual values
    PHONE_NUMBER_ID = "712165475312328"  # From WhatsApp Manager
    ACCESS_TOKEN = "EAAqetBnMsGABPMo2QcGOZAkBvYzjpkb1OjjtZAY51MFB2jicneqYFtZCSzrzoZCgEukyBTbZCZBJksmeZCduinDaB0g0wZAbQTkr5lqpcZAHiZCVTrsDoWWqFdzSJG4xQhqZB3inWxlCKzYptO2D9jnwzUC8022r4Jwi0GZBEgZCHap8ZBpeojw4hCjx7An2KssirpkcAh3dTJrpaek5EIbvZAYW3iRVKWNfGsFB0p4wPPNd2TN"  # From Meta Developer App
    PUBLIC_KEY_PATH = "public.pem"  # Path to your public key file
    

    
    # Upload the key
    success = upload_public_key(PHONE_NUMBER_ID, ACCESS_TOKEN, PUBLIC_KEY_PATH)
    
    if success:
        print("\n✅ Next steps:")
        print("1. Deploy your Flask endpoint with the updated code")
        print("2. Set the endpoint URL in WhatsApp Manager")
        print("3. Run the health check")
    else:
        print("\n❌ Fix the issues above and try again")