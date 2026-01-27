#!/usr/bin/env python3
"""
Find suitable test models with pricing data
"""

from supabase import create_client, Client

STAGING_URL = "https://ynleroehyrmaafkgjgmr.supabase.co"
STAGING_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlubGVyb2VoeXJtYWFma2dqZ21yIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1OTY4Nzc3OSwiZXhwIjoyMDc1MjYzNzc5fQ.kIehmSJC9EX86rkhCbhzX6ZHiTfQO7k6ZM2wU4e6JNs"

supabase: Client = create_client(STAGING_URL, STAGING_KEY)

# Get models with OpenRouter provider (provider_id 1)
print("=== Looking for OpenRouter models ===")
models = supabase.table('models').select('id, model_id, model_name, provider_id').eq('provider_id', 1).limit(10).execute()

if models.data:
    print(f"Found {len(models.data)} OpenRouter models")
    for m in models.data[:5]:
        print(f"  - {m['model_id']}")
else:
    print("No OpenRouter models found")

# Get any models with non-zero pricing
print("\n=== Looking for models with non-zero pricing ===")
# First get pricing records with non-zero prices
pricing_records = supabase.table('model_pricing').select('model_id, price_per_input_token, price_per_output_token').gt('price_per_input_token', 0).limit(10).execute()

if pricing_records.data:
    print(f"Found {len(pricing_records.data)} models with non-zero pricing")
    model_ids = [p['model_id'] for p in pricing_records.data]

    # Get the actual model details
    models_with_pricing = supabase.table('models').select('id, model_id, model_name, provider_id').in_('id', model_ids).execute()

    for m in models_with_pricing.data:
        pricing = next((p for p in pricing_records.data if p['model_id'] == m['id']), None)
        if pricing:
            print(f"  - {m['model_id']}: in={pricing['price_per_input_token']}, out={pricing['price_per_output_token']}")
else:
    print("No models with non-zero pricing found")
    # Fall back to any models with pricing
    print("\n=== Looking for any models with pricing ===")
    all_pricing = supabase.table('model_pricing').select('model_id').limit(10).execute()
    if all_pricing.data:
        model_ids = [p['model_id'] for p in all_pricing.data]
        models_any_pricing = supabase.table('models').select('id, model_id, model_name').in_('id', model_ids).execute()
        for m in models_any_pricing.data[:5]:
            print(f"  - {m['model_id']}")
