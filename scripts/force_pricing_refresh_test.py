#!/usr/bin/env python3
"""
Force pricing refresh by making multiple requests
The 15-minute cache should expire or be refreshed with new pricing
"""

import asyncio
import time
import httpx

STAGING_URL = "https://gatewayz-staging.up.railway.app"
ADMIN_KEY = "gw_live_wTfpLJ5VB28qMXpOAhr7Uw"

print("="*60)
print("Force Pricing Refresh Test")
print("="*60)
print()

async def test_pricing():
    """Make a chat request and check pricing"""

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{STAGING_URL}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {ADMIN_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "openai/gpt-4o-mini",
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 5,
                "stream": False
            }
        )

        if response.status_code == 200:
            data = response.json()
            usage = data.get("usage", {})
            gateway_usage = data.get("gateway_usage", {})
            cost = gateway_usage.get("cost_usd", 0)

            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)

            # Expected cost with correct OpenRouter pricing
            expected = (prompt_tokens * 0.00000015) + (completion_tokens * 0.0000006)

            return {
                "success": True,
                "cost": cost,
                "expected": expected,
                "tokens": f"{prompt_tokens}+{completion_tokens}",
                "correct": abs(cost - expected) < 0.00000001
            }
        else:
            return {
                "success": False,
                "error": f"HTTP {response.status_code}: {response.text[:200]}"
            }

async def main():
    print("Testing pricing immediately after deployment...")
    print()

    result = await test_pricing()

    if result["success"]:
        print(f"✓ Request succeeded")
        print(f"  Tokens: {result['tokens']}")
        print(f"  Cost: ${result['cost']:.8f}")
        print(f"  Expected: ${result['expected']:.8f}")
        print()

        if result["cost"] == 0:
            print("❌ STILL CHARGING $0")
            print()
            print("This means the deployment hasn't taken effect yet, OR")
            print("the pricing cache still has stale $0 values.")
            print()
            print("Wait 15 minutes for cache to expire, or:")
            print("1. Restart the staging service on Railway")
            print("2. The service will reload with fresh pricing")
            return 1
        elif result["correct"]:
            print("✅ ✅ ✅ PRICING IS CORRECT! ✅ ✅ ✅")
            print("Backend is now using accurate live pricing!")
            print("Credits will be properly deducted from users.")
            return 0
        else:
            diff_percent = abs((result["expected"] - result["cost"]) / result["expected"] * 100) if result["expected"] > 0 else 0
            print(f"⚠️  Pricing differs by {diff_percent:.2f}%")
            if diff_percent < 5:
                print("This is within acceptable tolerance.")
                return 0
            else:
                print("This difference is too large.")
                return 1
    else:
        print(f"❌ Request failed: {result['error']}")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())

    if exit_code != 0:
        print()
        print("="*60)
        print("Next Steps:")
        print("="*60)
        print("1. Restart staging service on Railway to clear cache")
        print("2. Check Railway deployment logs to verify latest commit")
        print("3. Wait 15 minutes for pricing cache TTL to expire")
        print()

    exit(exit_code)
