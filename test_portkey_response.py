#!/usr/bin/env python3
"""
Quick script to test what Portkey API returns
"""
import os
from dotenv import load_dotenv
import httpx
import json

load_dotenv()

PORTKEY_API_KEY = os.environ.get("PORTKEY_API_KEY")

if not PORTKEY_API_KEY:
    print("ERROR: PORTKEY_API_KEY not configured")
    exit(1)

headers = {
    "x-portkey-api-key": PORTKEY_API_KEY,
    "Content-Type": "application/json"
}

print("Fetching models from Portkey...")
try:
    response = httpx.get("https://api.portkey.ai/v1/models", headers=headers, timeout=20.0)
    response.raise_for_status()

    payload = response.json()
    models = payload.get("data", [])

    print(f"\nTotal models returned: {len(models)}")

    # Check for @provider/ prefixes
    prefixes_to_check = {
        "@google/": [],
        "@cerebras/": [],
        "@nebius/": [],
        "@xai/": [],
        "@novita/": [],
        "google/": [],
        "cerebras/": [],
        "nebius/": [],
        "xai/": [],
        "novita/": [],
        "gemini": [],
        "grok": [],
    }

    for model in models:
        model_id = model.get("id", "") or model.get("slug", "")
        for prefix in prefixes_to_check:
            if prefix.lower() in model_id.lower():
                prefixes_to_check[prefix].append(model_id)

    print("\nPattern matching results:")
    print("-" * 70)
    for prefix, matching_models in prefixes_to_check.items():
        count = len(matching_models)
        print(f"{prefix:20} : {count:4} models")
        if count > 0 and count <= 3:
            for m in matching_models:
                print(f"  - {m}")
        elif count > 3:
            print(f"  - {matching_models[0]}")
            print(f"  - {matching_models[1]}")
            print(f"  - ... ({count - 2} more)")

    print("\n" + "=" * 70)
    print("Sample model IDs (first 10):")
    print("-" * 70)
    for i, model in enumerate(models[:10]):
        model_id = model.get("id", "") or model.get("slug", "")
        print(f"{i+1}. {model_id}")

except httpx.HTTPStatusError as e:
    print(f"HTTP ERROR: {e.response.status_code}")
    print(f"Response: {e.response.text}")
except Exception as e:
    print(f"ERROR: {e}")
