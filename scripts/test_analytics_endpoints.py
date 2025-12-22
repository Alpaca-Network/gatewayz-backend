import asyncio
import httpx
import json
import uuid
from datetime import datetime, timezone

BASE_URL = "http://localhost:8000"
# Replace with a valid token if your local env requires it
AUTH_HEADER = {"Authorization": "Bearer gw_live_wTfpLJ5VB28qMXpOAhr7Uw"}

async def test_analytics_endpoints():
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        print(f"Testing Analytics Endpoints at {BASE_URL}...\n")

        # 0. Fetch Valid IDs
        print("0. Fetching valid Provider and Model IDs...")
        provider_id = 1
        model_id = 1
        
        try:
            # 1. Try to fetch providers
            resp = await client.get("/providers/")
            if resp.status_code == 200:
                providers = resp.json()
                if providers:
                    provider_id = providers[0]['id']
                    print(f"   ✅ Found Provider ID: {provider_id} ({providers[0].get('name')})")
            
            # 2. Try to fetch models (from DB catalog route if available)
            resp = await client.get("/catalog/models-db/")
            if resp.status_code == 200:
                models = resp.json()
                if models:
                    model = models[0]
                    model_id = model['id']
                    # Ensure we use the provider associated with this model if possible
                    if 'provider_id' in model:
                        provider_id = model['provider_id']
                    print(f"   ✅ Found Model ID: {model_id} ({model.get('model_name')}) linked to Provider ID: {provider_id}")
            else:
                 # Fallback to public catalog if internal one fails
                 print("   ℹ️  Internal catalog unreachable, trying public catalog...")
                 # (Logic for public catalog would be here, but usually doesn't expose internal IDs easily)
                 pass

        except Exception as e:
            print(f"   ⚠️ Warning: Could not fetch metadata: {e}")
        
        print(f"   Using Provider ID: {provider_id}, Model ID: {model_id}")

        # 1. Create a Time-Series Record
        request_id = str(uuid.uuid4())
        ts_data = {
            "request_id": request_id,
            "provider_id": provider_id,
            "model_id": model_id,

            "timestamp": datetime.now(timezone.utc).isoformat(),
            "latency_ms": 250,
            "input_tokens": 120,
            "output_tokens": 50,
            "status": "success",
            "cost_usd": 0.00045,
            "metadata": {"source": "test_script"}
        }
        
        print(f"1. POST /v1/analytics/data/time-series")
        try:
            resp = await client.post("/v1/analytics/data/time-series", json=ts_data, headers=AUTH_HEADER)
            if resp.status_code in (200, 201):
                print(f"   ✅ Success: {resp.json()['id']}")
            else:
                print(f"   ❌ Failed: {resp.status_code} - {resp.text}")
        except Exception as e:
             print(f"   ❌ Error: {e}")

        # 2. Get Time-Series Records
        print(f"\n2. GET /v1/analytics/data/time-series")
        try:
            resp = await client.get("/v1/analytics/data/time-series", params={"limit": 5}, headers=AUTH_HEADER)
            if resp.status_code == 200:
                data = resp.json()
                print(f"   ✅ Success: Retrieved {len(data)} records")
            else:
                print(f"   ❌ Failed: {resp.status_code} - {resp.text}")
        except Exception as e:
             print(f"   ❌ Error: {e}")

        # 3. Create/Upsert Rollup
        rollup_data = {
            "bucket": datetime.now(timezone.utc).replace(second=0, microsecond=0).isoformat(),
            "model_id": 101,
            "provider_id": 1,
            "request_count": 15,
            "sum_input_tokens": 1500,
            "sum_output_tokens": 800,
            "sum_total_tokens": 2300,
            "avg_latency_ms": 210.5,
            "p95_latency_ms": 400.0,
            "p99_latency_ms": 800.0,
            "avg_tokens_per_second": 35.5
        }
        
        print(f"\n3. POST /v1/analytics/data/rollup")
        try:
            resp = await client.post("/v1/analytics/data/rollup", json=rollup_data, headers=AUTH_HEADER)
            if resp.status_code in (200, 201):
                print(f"   ✅ Success: {resp.json()}")
            else:
                print(f"   ❌ Failed: {resp.status_code} - {resp.text}")
        except Exception as e:
             print(f"   ❌ Error: {e}")

        # 4. Get Rollup Records
        print(f"\n4. GET /v1/analytics/data/rollup")
        try:
            resp = await client.get("/v1/analytics/data/rollup", params={"limit": 5}, headers=AUTH_HEADER)
            if resp.status_code == 200:
                data = resp.json()
                print(f"   ✅ Success: Retrieved {len(data)} records")
            else:
                print(f"   ❌ Failed: {resp.status_code} - {resp.text}")
        except Exception as e:
             print(f"   ❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_analytics_endpoints())
