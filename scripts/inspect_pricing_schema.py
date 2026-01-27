#!/usr/bin/env python3
"""
Inspect the pricing schema to understand the actual table structure
"""

from supabase import create_client, Client

STAGING_URL = "https://ynleroehyrmaafkgjgmr.supabase.co"
STAGING_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlubGVyb2VoeXJtYWFma2dqZ21yIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1OTY4Nzc3OSwiZXhwIjoyMDc1MjYzNzc5fQ.kIehmSJC9EX86rkhCbhzX6ZHiTfQO7k6ZM2wU4e6JNs"

supabase: Client = create_client(STAGING_URL, STAGING_KEY)

print("=== Checking models table ===")
models = supabase.table('models').select('*').limit(5).execute()
if models.data:
    print(f"Found {len(models.data)} models")
    print("Sample model:", models.data[0] if models.data else "None")
    print("\nModel IDs available:")
    for m in models.data:
        print(f"  - {m.get('model_id', m.get('id'))}")
else:
    print("No models found")

print("\n=== Checking model_pricing table ===")
try:
    pricing = supabase.table('model_pricing').select('*').limit(3).execute()
    if pricing.data:
        print(f"Found {len(pricing.data)} pricing records")
        print("Sample pricing record:")
        import json
        print(json.dumps(pricing.data[0], indent=2))
    else:
        print("No pricing records found")
except Exception as e:
    print(f"Error: {e}")

print("\n=== Checking model_pricing_history table ===")
try:
    history = supabase.table('model_pricing_history').select('*').limit(3).execute()
    if history.data:
        print(f"Found {len(history.data)} history records")
        print("Sample history record:")
        import json
        print(json.dumps(history.data[0], indent=2))
    else:
        print("No history records found")
except Exception as e:
    print(f"Error: {e}")

print("\n=== Checking pricing_sync_log table ===")
try:
    logs = supabase.table('pricing_sync_log').select('*').order('sync_started_at', desc=True).limit(3).execute()
    if logs.data:
        print(f"Found {len(logs.data)} sync log records")
        print("Sample sync log:")
        import json
        print(json.dumps(logs.data[0], indent=2))
    else:
        print("No sync logs found")
except Exception as e:
    print(f"Error: {e}")
