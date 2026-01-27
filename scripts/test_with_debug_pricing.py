#!/usr/bin/env python3
"""
Test pricing with detailed debugging
"""

import asyncio
import httpx
import time

STAGING_URL = "https://gatewayz-staging.up.railway.app"
ADMIN_KEY = "gw_live_wTfpLJ5VB28qMXpOAhr7Uw"

async def test():
    async with httpx.AsyncClient(timeout=30.0) as client:
        print("="*60)
        print("Testing Pricing with Debug Info")
        print("="*60)
        print()

        # Make a test request
        response = await client.post(
            f"{STAGING_URL}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {ADMIN_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "openai/gpt-4o-mini",
                "messages": [{"role": "user", "content": "Test"}],
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
            expected = (prompt_tokens * 0.00000015) + (completion_tokens * 0.0000006)

            print(f"Response:")
            print(f"  Tokens: {prompt_tokens} prompt + {completion_tokens} completion")
            print(f"  Cost: ${cost:.8f}")
            print()

            print(f"Expected (OpenRouter rates):")
            print(f"  Prompt: $0.00000015/token")
            print(f"  Completion: $0.0000006/token")
            print(f"  Expected cost: ${expected:.8f}")
            print()

            # Check what's happening
            if cost == 0:
                print("❌ Cost is $0 - pricing data is wrong in database")
            elif abs(cost - expected) < 0.00000001:
                print("✅ Cost is CORRECT!")
            else:
                # Check if it's default pricing
                default_cost = (prompt_tokens + completion_tokens) * 0.00002
                if abs(cost - default_cost) < 0.00000001:
                    print(f"⚠️  Using default pricing ($0.00002/token)")
                    print(f"     Actual: ${cost:.8f}")
                    print(f"     Default: ${default_cost:.8f}")
                    print()
                    print("This means:")
                    print("  - Database doesn't have pricing for this model")
                    print("  - OR pricing sync hasn't run yet")
                    print("  - OR pricing data is still cached as empty")
                else:
                    diff_pct = abs((expected - cost) / expected * 100) if expected > 0 else 0
                    print(f"⚠️  Off by {diff_pct:.2f}%")

asyncio.run(test())
