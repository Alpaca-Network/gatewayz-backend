#!/usr/bin/env python3
"""
Check database pricing for specific model
"""

import asyncio
import httpx

STAGING_URL = "https://gatewayz-staging.up.railway.app"
ADMIN_KEY = "gw_live_wTfpLJ5VB28qMXpOAhr7Uw"

async def check_pricing():
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Check pricing via admin endpoint
        response = await client.get(
            f"{STAGING_URL}/admin/pricing/model/openai%2Fgpt-4o-mini",
            headers={"Authorization": f"Bearer {ADMIN_KEY}"}
        )

        print("="*60)
        print("Database Pricing Check")
        print("="*60)
        print(f"Model: openai/gpt-4o-mini")
        print(f"Status: {response.status_code}")
        print()

        if response.status_code == 200:
            data = response.json()
            print("Response:")
            import json
            print(json.dumps(data, indent=2))
        else:
            print(f"Error: {response.text}")

if __name__ == "__main__":
    asyncio.run(check_pricing())
