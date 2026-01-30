#!/usr/bin/env python3
"""
Simple test to verify live pricing is working on staging
"""

import requests
import json

STAGING_URL = "https://gatewayz-staging.up.railway.app"
ADMIN_KEY = "gw_live_wTfpLJ5VB28qMXpOAhr7Uw"

print("="*60)
print("Testing Live Pricing on Staging")
print("="*60)
print()

# Test chat completion
print("Making chat completion request...")
print(f"Model: openai/gpt-4o-mini")
print()

response = requests.post(
    f"{STAGING_URL}/chat/completions",
    headers={
        "Authorization": f"Bearer {ADMIN_KEY}",
        "Content-Type": "application/json"
    },
    json={
        "model": "openai/gpt-4o-mini",
        "messages": [{"role": "user", "content": "Say hello"}],
        "max_tokens": 10,
        "stream": False
    },
    timeout=30
)

print(f"Status Code: {response.status_code}")
print()

if response.status_code == 200:
    data = response.json()

    usage = data.get("usage", {})
    gateway_usage = data.get("gateway_usage", {})

    print("✓ Chat completion successful!")
    print()
    print("Token Usage:")
    print(f"  - Prompt tokens: {usage.get('prompt_tokens', 0)}")
    print(f"  - Completion tokens: {usage.get('completion_tokens', 0)}")
    print()

    if gateway_usage:
        print("Gateway Usage:")
        print(f"  - Cost: ${gateway_usage.get('cost_usd', 0):.8f}")
        print(f"  - Tokens charged: {gateway_usage.get('tokens_charged', 0)}")
        print()

    # Calculate expected cost from OpenRouter pricing
    prompt_tokens = usage.get('prompt_tokens', 0)
    completion_tokens = usage.get('completion_tokens', 0)

    # OpenRouter prices for gpt-4o-mini:
    # Prompt: $0.00000015/token, Completion: $0.0000006/token
    expected_cost = (prompt_tokens * 0.00000015) + (completion_tokens * 0.0000006)
    actual_cost = gateway_usage.get('cost_usd', 0)

    print("Cost Analysis:")
    print(f"  - Expected (OpenRouter): ${expected_cost:.8f}")
    print(f"  - Actual (Backend):      ${actual_cost:.8f}")
    print(f"  - Difference:            ${abs(expected_cost - actual_cost):.8f}")
    print()

    # Check if pricing is correct
    if abs(expected_cost - actual_cost) < 0.00000001:
        print("✓ ✓ ✓ PRICING IS CORRECT! ✓ ✓ ✓")
        print("Backend is using live OpenRouter pricing")
    elif actual_cost == 0:
        print("✗ ✗ ✗ CRITICAL ISSUE! ✗ ✗ ✗")
        print("Cost is $0 - No credits are being deducted!")
        print("This means users are getting free API calls!")
    else:
        percent_diff = abs((expected_cost - actual_cost) / expected_cost * 100) if expected_cost > 0 else 0
        if percent_diff < 1:
            print("✓ Pricing is mostly correct (within 1%)")
        else:
            print(f"⚠ Cost differs by {percent_diff:.2f}%")

    print()
    print("Response preview:")
    content = data.get('choices', [{}])[0].get('message', {}).get('content', '')
    print(f"  '{content}'")

else:
    print("✗ Request failed!")
    print(f"Response: {response.text[:500]}")
