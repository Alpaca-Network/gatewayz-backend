#!/usr/bin/env python3
"""
Trigger manual pricing sync and verify it worked
"""

import asyncio
import httpx
import time

STAGING_URL = "https://gatewayz-staging.up.railway.app"
ADMIN_KEY = "gw_live_wTfpLJ5VB28qMXpOAhr7Uw"

print("="*60)
print("Manual Pricing Sync Trigger")
print("="*60)
print()

async def trigger_sync():
    """Trigger pricing sync via admin endpoint"""

    async with httpx.AsyncClient(timeout=180.0) as client:
        # Try the sync endpoint
        try:
            print("Attempting to trigger pricing sync...")
            response = await client.post(
                f"{STAGING_URL}/admin/pricing/sync",
                headers={
                    "Authorization": f"Bearer {ADMIN_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "providers": ["openrouter"],
                    "force": True
                }
            )

            print(f"Status: {response.status_code}")

            if response.status_code == 200:
                result = response.json()
                print("✓ Sync triggered successfully!")
                print(f"Response: {result}")
                return True
            elif response.status_code == 404:
                print("❌ Sync endpoint not found (404)")
                print()
                print("The pricing sync may run automatically via scheduler.")
                print("Let's wait 30 seconds and test pricing...")
                await asyncio.sleep(30)
                return None
            else:
                print(f"⚠️  Unexpected status: {response.status_code}")
                print(f"Response: {response.text[:500]}")
                return False

        except Exception as e:
            print(f"⚠️  Could not trigger sync: {e}")
            print()
            print("The scheduler should sync automatically.")
            print("Let's wait and test pricing...")
            await asyncio.sleep(10)
            return None

async def test_pricing():
    """Test if pricing is now correct"""

    print()
    print("Testing pricing...")
    print("-"*60)

    async with httpx.AsyncClient(timeout=30.0) as client:
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

            print(f"Tokens: {prompt_tokens} prompt + {completion_tokens} completion")
            print(f"Expected cost: ${expected:.8f}")
            print(f"Actual cost:   ${cost:.8f}")
            print()

            if cost == 0:
                print("❌ Still charging $0")
                print()
                print("Pricing sync hasn't taken effect yet.")
                print("Options:")
                print("1. Wait 15 minutes for cache to expire")
                print("2. Restart the service to clear cache")
                print("3. Check if scheduler is running: railway logs | grep 'pricing sync'")
                return False
            elif abs(cost - expected) < 0.00000001:
                print("✅ ✅ ✅ PRICING IS FIXED! ✅ ✅ ✅")
                print()
                print("Credits are now being accurately deducted!")
                print("The fix is working correctly!")
                return True
            else:
                diff_pct = abs((expected - cost) / expected * 100) if expected > 0 else 0
                if diff_pct < 5:
                    print(f"✓ Pricing is correct (within {diff_pct:.2f}% tolerance)")
                    return True
                else:
                    print(f"⚠️  Pricing differs by {diff_pct:.2f}%")
                    return False
        else:
            print(f"❌ Request failed: {response.status_code}")
            return False

async def main():
    # Step 1: Try to trigger sync
    sync_result = await trigger_sync()

    # Step 2: Test pricing
    pricing_ok = await test_pricing()

    print()
    print("="*60)
    if pricing_ok:
        print("SUCCESS! Pricing is working correctly.")
        return 0
    else:
        print("Pricing issue persists.")
        print()
        print("The scheduler should fix this automatically within 1-2 hours.")
        print("Or manually restart the service to force cache clear.")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
