#!/usr/bin/env python3
"""
Generate a WhatsApp Flow token via the Graph API.

Prerequisites:
pip install requests
"""

import os
import requests

ACCESS_TOKEN      = "EAAqetBnMsGABPMo2QcGOZAkBvYzjpkb1OjjtZAY51MFB2jicneqYFtZCSzrzoZCgEukyBTbZCZBJksmeZCduinDaB0g0wZAbQTkr5lqpcZAHiZCVTrsDoWWqFdzSJG4xQhqZB3inWxlCKzYptO2D9jnwzUC8022r4Jwi0GZBEgZCHap8ZBpeojw4hCjx7An2KssirpkcAh3dTJrpa"        # <-- set in your shell or replace with the literal string
PHONE_NUMBER_ID   = "712165475312328"                    # <-- your phone-number ID
FLOW_ID           = "1295195665539253"                   # <-- your Flow ID
GRAPH_VERSION     = "v19.0"

def generate_flow_token(access_token: str, phone_id: str, flow_id: str) -> str:
    url  = f"https://graph.facebook.com/{GRAPH_VERSION}/{phone_id}/flows"
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        data    ={"flow_id": flow_id},
        timeout =10,
    )
    resp.raise_for_status()
    return resp.json()["flow_token"]

if __name__ == "__main__":
    token = generate_flow_token(ACCESS_TOKEN, PHONE_NUMBER_ID, FLOW_ID)
    print("Flow token:", token)
