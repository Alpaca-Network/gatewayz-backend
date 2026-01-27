#!/usr/bin/env python3
"""
Manual Pricing Sync - Try all available endpoints
"""

import asyncio
import httpx

STAGING_URL = "https://gatewayz-staging.up.railway.app"
ADMIN_KEY = "gw_live_wTfpLJ5VB28qMXpOAhr7Uw"

ENDPOINTS = [
    {
        "name": "Admin Scheduler Trigger",
        "method": "POST",
        "url": f"{STAGING_URL}/admin/pricing/scheduler/trigger",
        "body": None
    },
    {
        "name": "Pricing Sync Run (OpenRouter only)",
        "method": "POST",
        "url": f"{STAGING_URL}/pricing/sync/run?providers=openrouter",
        "body": None
    },
    {
        "name": "Pricing Sync Run (All providers)",
        "method": "POST",
        "url": f"{STAGING_URL}/pricing/sync/run",
        "body": None
    },
    {
        "name": "Specific Provider Sync",
        "method": "POST",
        "url": f"{STAGING_URL}/pricing/sync/run/openrouter",
        "body": None
    },
    {
        "name": "Pricing Sync Trigger",
        "method": "POST",
        "url": f"{STAGING_URL}/pricing-sync/trigger",
        "body": {"force_refresh": True}
    },
]

print("="*60)
print("Manual Pricing Sync Trigger")
print("="*60)
print()

async def try_endpoint(endpoint):
    """Try a single endpoint"""
    print(f"Trying: {endpoint['name']}")
    print(f"  URL: {endpoint['url']}")

    async with httpx.AsyncClient(timeout=180.0) as client:
        try:
            if endpoint["method"] == "POST":
                response = await client.post(
                    endpoint["url"],
                    headers={
                        "Authorization": f"Bearer {ADMIN_KEY}",
                        "Content-Type": "application/json"
                    },
                    json=endpoint["body"] if endpoint["body"] else {}
                )
            else:
                response = await client.get(
                    endpoint["url"],
                    headers={"Authorization": f"Bearer {ADMIN_KEY}"}
                )

            print(f"  Status: {response.status_code}")

            if response.status_code in [200, 201, 202]:
                print(f"  ✓ SUCCESS!")
                try:
                    data = response.json()
                    print(f"  Response:")
                    import json
                    print("  " + json.dumps(data, indent=2).replace("\n", "\n  "))
                except:
                    print(f"  {response.text[:500]}")
                return True, data
            else:
                print(f"  ✗ Failed: {response.text[:200]}")
                return False, None

        except Exception as e:
            print(f"  ✗ Error: {e}")
            return False, None

async def main():
    print("Attempting to trigger pricing sync...")
    print()

    success = False
    result_data = None

    for i, endpoint in enumerate(ENDPOINTS, 1):
        print(f"[{i}/{len(ENDPOINTS)}] ", end="")
        success, result_data = await try_endpoint(endpoint)
        print()

        if success:
            print("="*60)
            print("✅ Pricing sync triggered successfully!")
            print("="*60)
            print()
            print("Waiting 10 seconds for sync to complete...")
            await asyncio.sleep(10)
            break

    if not success:
        print("="*60)
        print("❌ Could not trigger sync via any endpoint")
        print("="*60)
        print()
        print("Possible reasons:")
        print("1. Endpoints not included in current deployment")
        print("2. Routes not registered in main.py")
        print("3. Additional authentication required")
        print()
        print("Solutions:")
        print("1. Restart service (triggers initial sync after 30s)")
        print("2. Wait for automatic scheduler (1-2 hours)")
        print()
        return 1

    # Test pricing after sync
    print()
    print("Testing if pricing is now correct...")
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
            expected = (prompt_tokens * 0.00000015) + (completion_tokens * 0.0000006)

            print(f"Tokens: {prompt_tokens} + {completion_tokens}")
            print(f"Expected: ${expected:.8f}")
            print(f"Actual:   ${cost:.8f}")
            print()

            if cost == 0:
                print("⚠️  Still charging $0")
                print("Cache may need more time to clear (15 min)")
            elif abs(cost - expected) < 0.00000001:
                print("✅ ✅ ✅ PRICING IS CORRECT! ✅ ✅ ✅")
                print("Credits are being accurately deducted!")
                return 0
            else:
                # Check if it's default pricing
                default_cost = (prompt_tokens + completion_tokens) * 0.00002
                if abs(cost - default_cost) < 0.00000001:
                    print("⚠️  Using default pricing ($0.00002/token)")
                    print("Database pricing not populated yet")
                    print("Wait a few minutes and test again")
                else:
                    diff_pct = abs((expected - cost) / expected * 100) if expected > 0 else 0
                    if diff_pct < 5:
                        print(f"✓ Close enough ({diff_pct:.2f}% difference)")
                        return 0
                    else:
                        print(f"⚠️  Differs by {diff_pct:.2f}%")
        else:
            print(f"❌ Test request failed: {response.status_code}")

    return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
