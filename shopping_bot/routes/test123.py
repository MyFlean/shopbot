# import requests

# # Configuration
# NEW_PHONE_NUMBER_ID = "733146656538009"  # From WhatsApp Manager
# ACCESS_TOKEN = "EAAqetBnMsGABPESo4UjQC4Jv07FWK0bC2bWw9gutRZCtH51W6R6bE5nvJSPyJUBVRQ3M5ak3AmjifcZA6AeuZCKGNJCKAeAp7PbEuWi0iaZBNcfGWOgV4KVKvK5s8KUY4hPKDUfYmfCHyNyJIxdU0hUrrplDXS4AxQjvXuaSrA7yEosJpBZBM4we8iaQPJG9A1TLcI4OW5FCeI3hM0IU62sNn2nCpGlLaTGyo59x2"  # Your Meta access token

# # Read your existing public key
# with open('public.pem', 'r') as f:
#     public_key = f.read().strip()

# # Upload to new number
# url = f"https://graph.facebook.com/v18.0/{NEW_PHONE_NUMBER_ID}/whatsapp_business_encryption"
# response = requests.post(
#     url,
#     headers={"Authorization": f"Bearer {ACCESS_TOKEN}"},
#     json={"business_public_key": public_key}
# )

# print("Status:", response.status_code)
# print("Response:", response.json())