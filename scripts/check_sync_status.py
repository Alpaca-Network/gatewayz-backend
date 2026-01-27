#!/usr/bin/env python3
"""
Check pricing sync status and wait for completion
"""

import asyncio
import httpx
import time

STAGING_URL = "https://gatewayz-staging.up.railway.app"
ADMIN_KEY = "gw_live_wTfpLJ5VB28qMXpOAhr7Uw"
SYNC_ID = "cb2a2c26-f70e-42f4-92a2-65c8798bdd53"

print("="*60)
print("Pricing Sync Status Monitor")
print("="*60)
print(f"Sync ID: {SYNC_ID}")
print()

async def check_sync_status():
    """Poll sync status until complete"""

    async with httpx.AsyncClient(timeout=30.0) as client:
        for attempt in range(30):  # Max 5 minutes (30 * 10 seconds)
            try:
                response = await client.get(
                    f"{STAGING_URL}/admin/pricing/sync/{SYNC_ID}",
                    headers={"Authorization": f"Bearer {ADMIN_KEY}"}
                )

                if response.status_code == 200:
                    data = response.json()
                    status = data.get("status", "unknown")

                    print(f"[Attempt {attempt + 1}] Status: {status}")

                    if status == "completed":
                        print()
                        print("✅ Sync completed successfully!")
                        print()
                        print("Results:")
                        print(f"  - Providers synced: {data.get('providers_synced', 0)}")
                        print(f"  - Models updated: {data.get('models_updated', 0)}")
                        print(f"  - Models skipped: {data.get('models_skipped', 0)}")
                        print(f"  - Errors: {data.get('total_errors', 0)}")
                        print(f"  - Duration: {data.get('duration_seconds', 0):.2f}s")
                        print()
                        return True, data

                    elif status == "failed":
                        print()
                        print("❌ Sync failed!")
                        print(f"Error: {data.get('error_message', 'Unknown error')}")
                        return False, data

                    elif status in ["queued", "running"]:
                        print(f"  ... still {status}, waiting 10s ...")
                        await asyncio.sleep(10)

                    else:
                        print(f"  Unknown status: {status}")
                        await asyncio.sleep(10)

                elif response.status_code == 404:
                    print(f"  Sync job not found (404)")
                    print(f"  This is normal - job may have completed and been cleaned up")
                    return None, None

                else:
                    print(f"  API error: {response.status_code}")
                    await asyncio.sleep(10)

            except Exception as e:
                print(f"  Error checking status: {e}")
                await asyncio.sleep(10)

        print()
        print("⏱️  Timeout waiting for sync to complete")
        print("  Sync may still be running in background")
        return None, None

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

            if abs(cost - expected) < 0.00000001:
                print("✅ ✅ ✅ PRICING IS CORRECT! ✅ ✅ ✅")
                print()
                print("Credits are now being accurately deducted!")
                print("The fix is complete and working!")
                return True
            elif cost == 0:
                print("❌ Still charging $0")
                print("Wait for cache to clear (15 min)")
            else:
                default_cost = (prompt_tokens + completion_tokens) * 0.00002
                if abs(cost - default_cost) < 0.00000001:
                    print("⚠️  Still using default pricing")
                    print("Database may not have been populated yet")
                    print("Check sync results above")
                else:
                    diff_pct = abs((expected - cost) / expected * 100) if expected > 0 else 0
                    if diff_pct < 5:
                        print(f"✓ Pricing is correct (within {diff_pct:.2f}%)")
                        return True
                    else:
                        print(f"⚠️  Pricing differs by {diff_pct:.2f}%")

        return False

async def main():
    # Check sync status
    success, result = await check_sync_status()

    # Test pricing
    pricing_ok = await test_pricing()

    print()
    print("="*60)
    if pricing_ok:
        print("SUCCESS! Pricing is working correctly.")
        return 0
    else:
        print("Pricing needs more time or investigation.")
        print()
        if success:
            print("Sync completed but pricing not reflected yet.")
            print("This could mean:")
            print("1. Cache needs to expire (wait 15 min)")
            print("2. Service needs restart to clear cache")
            print("3. Check sync results for errors")
        else:
            print("Sync status unclear.")
            print("Check logs: railway logs | grep 'pricing sync'")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
