import json
import re

# Load your JSON (replace 'flow.json' with your file)
with open("flow.json", "r") as f:
    data = f.read()

# Regex: replace any base64 data URL in "src": "..." with empty string
cleaned = re.sub(r'"src"\s*:\s*".*?"', '"src": ""', data)

# Save cleaned JSON
with open("flow_cleaned.json", "w") as f:
    f.write(cleaned)

print("Base64 URLs removed successfully.")
