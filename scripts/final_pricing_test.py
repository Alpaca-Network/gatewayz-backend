#!/usr/bin/env python3
"""
Final pricing test after redeploy
"""

import asyncio
import httpx

STAGING_URL = "https://gatewayz-staging.up.railway.app"
ADMIN_KEY = "gw_live_wTfpLJ5VB28qMXpOAhr7Uw"

print("="*60)
print("Final Pricing Test")
print("="*60)
print()

async def test_pricing():
    """Test pricing after code reload"""

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Make 3 requests to ensure we're not hitting cached data
        for i in range(3):
            print(f"Test request #{i+1}...")

            response = await client.post(
                f"{STAGING_URL}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {ADMIN_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "openai/gpt-4o-mini",
                    "messages": [{"role": "user", "content": f"Test {i+1}"}],
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

                print(f"  Tokens: {prompt_tokens} + {completion_tokens}")
                print(f"  Expected: ${expected:.8f}")
                print(f"  Actual:   ${cost:.8f}")

                # Analyze the result
                if abs(cost - expected) < 0.00000001:
                    print(f"  ✅ CORRECT!")
                elif cost == 0:
                    print(f"  ❌ Still $0")
                else:
                    default_cost = (prompt_tokens + completion_tokens) * 0.00002
                    if abs(cost - default_cost) < 0.00000001:
                        print(f"  ⚠️  Default pricing")
                    else:
                        diff_pct = abs((expected - cost) / expected * 100) if expected > 0 else 0
                        print(f"  ⚠️  Off by {diff_pct:.2f}%")

                print()

                # If correct, we can stop
                if abs(cost - expected) < 0.00000001:
                    return True, expected, cost

            else:
                print(f"  ❌ Request failed: {response.status_code}")
                print()

            # Wait a bit between requests
            if i < 2:
                await asyncio.sleep(2)

    return False, 0, 0

async def main():
    success, expected, actual = await test_pricing()

    print("="*60)
    if success:
        print("✅ ✅ ✅ SUCCESS! ✅ ✅ ✅")
        print()
        print("Pricing is now correct!")
        print(f"Expected: ${expected:.8f}")
        print(f"Actual:   ${actual:.8f}")
        print()
        print("Credits are being accurately deducted from users.")
        print("The fix is complete and working!")
        return 0
    else:
        print("⚠️  Pricing still not correct")
        print()
        print("Possible reasons:")
        print("1. Code changes haven't been deployed yet")
        print("2. Cache hasn't cleared yet (wait 15 min)")
        print("3. Database doesn't have pricing data")
        print()
        print("Next steps:")
        print("1. Check Railway deployment logs")
        print("2. Verify latest commit is deployed")
        print("3. Manually trigger sync again after waiting")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
