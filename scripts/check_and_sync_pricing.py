#!/usr/bin/env python3
"""
Check database pricing status and run sync if needed
"""

import os
import sys
import asyncio
import httpx

# Staging backend URL
STAGING_URL = "https://gatewayz-staging.up.railway.app"
ADMIN_KEY = "gw_live_wTfpLJ5VB28qMXpOAhr7Uw"

print("="*60)
print("Pricing Database Check & Sync")
print("="*60)
print()

async def check_and_sync():
    """Check pricing status and run sync"""

    # Step 1: Check if we can trigger a pricing sync
    print("Step 1: Triggering pricing sync via admin API...")
    print("-"*60)

    async with httpx.AsyncClient(timeout=180.0) as client:
        try:
            # Try to trigger a sync for OpenRouter
            sync_response = await client.post(
                f"{STAGING_URL}/admin/pricing/sync",
                headers={
                    "Authorization": f"Bearer {ADMIN_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "providers": ["openrouter"],
                    "force_refresh": True
                }
            )

            print(f"Sync API Status: {sync_response.status_code}")

            if sync_response.status_code == 200:
                result = sync_response.json()
                print("✓ Pricing sync initiated successfully!")
                print()
                print(f"Job ID: {result.get('job_id', 'N/A')}")
                print(f"Status: {result.get('status', 'N/A')}")
                print()

                # Wait for sync to complete (if synchronous)
                if result.get('status') == 'completed':
                    print("Sync completed immediately")
                    print(f"Models updated: {result.get('models_updated', 0)}")
                    print(f"Models skipped: {result.get('models_skipped', 0)}")
                    print()
                elif result.get('job_id'):
                    print("Sync running in background...")
                    print("Waiting 10 seconds for sync to complete...")
                    await asyncio.sleep(10)

            elif sync_response.status_code == 404:
                print("❌ Pricing sync endpoint not found")
                print("This endpoint may not be available on staging")
                print()
            else:
                print(f"⚠️  Sync request returned {sync_response.status_code}")
                print(f"Response: {sync_response.text[:500]}")
                print()

        except Exception as e:
            print(f"⚠️  Could not trigger sync via API: {e}")
            print()

    # Step 2: Test if pricing is now working
    print()
    print("Step 2: Testing pricing after sync...")
    print("-"*60)

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
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

                print(f"✓ Chat completion successful")
                print(f"  Tokens: {usage.get('prompt_tokens', 0)} + {usage.get('completion_tokens', 0)}")
                print(f"  Cost: ${cost:.8f}")
                print()

                # Calculate expected cost
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)
                expected_cost = (prompt_tokens * 0.00000015) + (completion_tokens * 0.0000006)

                print("Cost Analysis:")
                print(f"  Expected: ${expected_cost:.8f}")
                print(f"  Actual:   ${cost:.8f}")
                print(f"  Diff:     ${abs(expected_cost - cost):.8f}")
                print()

                if cost == 0:
                    print("❌ STILL CHARGING $0!")
                    print()
                    print("The pricing sync may not have worked.")
                    print("Next steps:")
                    print("1. Check if pricing sync tables exist in database")
                    print("2. Manually populate pricing for openai/gpt-4o-mini")
                    print("3. Check backend logs for pricing fetch errors")
                    return False
                elif abs(expected_cost - cost) < 0.00000001:
                    print("✅ ✅ ✅ PRICING FIXED! ✅ ✅ ✅")
                    print("Backend is now charging correct amounts!")
                    return True
                else:
                    diff_percent = abs((expected_cost - cost) / expected_cost * 100) if expected_cost > 0 else 0
                    if diff_percent < 1:
                        print("✓ Pricing is correct (within 1% tolerance)")
                        return True
                    else:
                        print(f"⚠️  Cost differs by {diff_percent:.2f}%")
                        return False
            else:
                print(f"❌ Chat request failed: {response.status_code}")
                print(f"Response: {response.text[:500]}")
                return False

        except Exception as e:
            print(f"❌ Error testing pricing: {e}")
            return False

async def main():
    success = await check_and_sync()

    print()
    print("="*60)
    if success:
        print("✅ Pricing is working correctly!")
        print("Credits will be accurately deducted from user accounts.")
    else:
        print("❌ Pricing issue persists")
        print("Manual intervention required.")
    print("="*60)

    return 0 if success else 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
