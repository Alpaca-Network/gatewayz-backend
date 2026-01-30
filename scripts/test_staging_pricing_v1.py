#!/usr/bin/env python3
"""
Test live pricing on staging with /v1/chat/completions endpoint
"""

import requests
import json
import time

STAGING_URL = "https://gatewayz-staging.up.railway.app"
ADMIN_KEY = "gw_live_wTfpLJ5VB28qMXpOAhr7Uw"

# Try both endpoints
ENDPOINTS = [
    "/v1/chat/completions",
    "/chat/completions",
]

print("="*60)
print("Testing Live Pricing on Staging")
print("="*60)
print()

for endpoint in ENDPOINTS:
    print(f"\nTrying endpoint: {endpoint}")
    print("-" * 60)

    try:
        response = requests.post(
            f"{STAGING_URL}{endpoint}",
            headers={
                "Authorization": f"Bearer {ADMIN_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "openai/gpt-4o-mini",
                "messages": [{"role": "user", "content": "Say 'test' only"}],
                "max_tokens": 10,
                "stream": False
            },
            timeout=30
        )

        print(f"Status Code: {response.status_code}")

        if response.status_code == 200:
            data = response.json()

            usage = data.get("usage", {})
            gateway_usage = data.get("gateway_usage", {})

            print("\n✓ ✓ ✓ SUCCESS! ✓ ✓ ✓")
            print()
            print("Token Usage:")
            print(f"  - Prompt tokens: {usage.get('prompt_tokens', 0)}")
            print(f"  - Completion tokens: {usage.get('completion_tokens', 0)}")
            print()

            if gateway_usage:
                cost = gateway_usage.get('cost_usd', 0)
                print("Gateway Usage:")
                print(f"  - Cost: ${cost:.8f}")
                print(f"  - Tokens charged: {gateway_usage.get('tokens_charged', 0)}")
                print()

                # Calculate expected cost
                prompt_tokens = usage.get('prompt_tokens', 0)
                completion_tokens = usage.get('completion_tokens', 0)

                # Live OpenRouter pricing (fetched 2026-01-26):
                # Prompt: $0.00000015/token, Completion: $0.0000006/token
                expected_cost = (prompt_tokens * 0.00000015) + (completion_tokens * 0.0000006)
                actual_cost = cost

                print("="*60)
                print("PRICING VERIFICATION")
                print("="*60)
                print(f"Expected (OpenRouter live): ${expected_cost:.8f}")
                print(f"Actual (Backend):           ${actual_cost:.8f}")
                print(f"Difference:                 ${abs(expected_cost - actual_cost):.8f}")
                print()

                # Determine if pricing is correct
                diff_percent = abs((expected_cost - actual_cost) / expected_cost * 100) if expected_cost > 0 else 0

                if actual_cost == 0:
                    print("❌ ❌ ❌ CRITICAL ISSUE! ❌ ❌ ❌")
                    print("Cost is $0 - No credits being deducted!")
                    print("Users are getting FREE API calls!")
                    print()
                    print("ACTION REQUIRED:")
                    print("1. Check pricing database sync")
                    print("2. Verify live pricing fetch is working")
                    print("3. Check calculate_cost() function")
                elif abs(expected_cost - actual_cost) < 0.00000001:
                    print("✅ ✅ ✅ PRICING IS CORRECT! ✅ ✅ ✅")
                    print("Backend is using accurate live pricing from OpenRouter")
                    print("Credit deduction is working properly!")
                elif diff_percent < 1:
                    print("✓ Pricing is correct (within 1% tolerance)")
                    print(f"Difference: {diff_percent:.4f}%")
                else:
                    print("⚠️ WARNING: Cost differs significantly")
                    print(f"Difference: {diff_percent:.2f}%")
                    print()
                    print("This could indicate:")
                    print("- Outdated cached pricing")
                    print("- Incorrect pricing normalization")
                    print("- Wrong provider pricing source")
            else:
                print("⚠️ No gateway_usage in response")

            print()
            print("Response content:")
            content = data.get('choices', [{}])[0].get('message', {}).get('content', '')
            print(f"  '{content}'")

            break  # Success, no need to try other endpoints

        else:
            print(f"✗ Failed: {response.text[:200]}")

    except Exception as e:
        print(f"✗ Error: {e}")

print()
print("="*60)
