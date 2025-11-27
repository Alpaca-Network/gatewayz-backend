#!/usr/bin/env python3
"""
Direct database check bypassing PostgREST API
Uses the Supabase service_role key to check tables
"""

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.config import Config
from supabase import create_client

def check_tables_direct():
    """Check if tables exist using direct SQL queries"""

    print("=" * 70)
    print("DIRECT DATABASE TABLE CHECK")
    print("=" * 70)

    # Create client with service role key (bypasses RLS and PostgREST cache)
    # Try service key first, fall back to regular key
    service_key = getattr(Config, 'SUPABASE_SERVICE_KEY', Config.SUPABASE_KEY)

    supabase = create_client(
        Config.SUPABASE_URL,
        service_key
    )

    print(f"\nConnecting to: {Config.SUPABASE_URL}")
    print(f"Using: {'SERVICE_ROLE' if service_key != Config.SUPABASE_KEY else 'ANON'} key\n")

    tables = ['providers', 'models', 'model_health_history']

    for table_name in tables:
        try:
            # Try to select from table
            response = supabase.table(table_name).select("*", count="exact").limit(0).execute()

            count = response.count if hasattr(response, 'count') else 0
            print(f"✅ Table '{table_name:25}' EXISTS - {count:,} records")

        except Exception as e:
            error_msg = str(e)
            if 'PGRST205' in error_msg or 'not find' in error_msg:
                print(f"❌ Table '{table_name:25}' NOT FOUND")
            else:
                print(f"⚠️  Table '{table_name:25}' ERROR: {e}")

    # Try to get sample providers
    print("\n" + "=" * 70)
    print("SAMPLE PROVIDER DATA")
    print("=" * 70)
    try:
        providers_response = supabase.table("providers").select("id, name, slug").limit(5).execute()
        if providers_response.data:
            for p in providers_response.data:
                print(f"  {p['id']:3} | {p['name']:30} | {p['slug']}")
        else:
            print("  No providers found (table exists but is empty)")
    except Exception as e:
        print(f"  ❌ Cannot access providers table: {e}")

    # Try to get sample models
    print("\n" + "=" * 70)
    print("SAMPLE MODEL DATA")
    print("=" * 70)
    try:
        models_response = supabase.table("models").select("id, model_name, provider_id").limit(5).execute()
        if models_response.data:
            for m in models_response.data:
                print(f"  {m['id']:5} | Provider: {m['provider_id']:3} | {m['model_name']}")
        else:
            print("  No models found (table exists but is empty)")
    except Exception as e:
        print(f"  ❌ Cannot access models table: {e}")

    print("\n" + "=" * 70)
    print("DIAGNOSIS")
    print("=" * 70)

    # Check if SUPABASE_SERVICE_KEY is set
    service_key = getattr(Config, 'SUPABASE_SERVICE_KEY', None)
    if not service_key or service_key == Config.SUPABASE_KEY:
        print("⚠️  WARNING: SUPABASE_SERVICE_KEY not set or same as SUPABASE_KEY")
        print("   You may need the service_role key for full admin access")
    else:
        print("✅ Using separate SUPABASE_SERVICE_KEY")

    print("=" * 70)

if __name__ == '__main__':
    check_tables_direct()
