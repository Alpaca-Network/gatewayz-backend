#!/usr/bin/env python3
"""Refresh PostgREST schema cache to make new tables visible"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.supabase_config import get_supabase_client

def refresh_schema_cache():
    """Refresh the PostgREST schema cache"""
    supabase = get_supabase_client()

    print("=" * 60)
    print("REFRESHING POSTGREST SCHEMA CACHE")
    print("=" * 60)

    try:
        # Try to call the refresh function if it exists
        result = supabase.rpc('refresh_postgrest_schema_cache').execute()
        print("✅ Schema cache refreshed successfully!")
        print(f"   Result: {result.data}")
    except Exception as e:
        print(f"⚠️  Could not refresh via RPC: {e}")
        print("\nAlternative: Restart your Supabase instance or use:")
        print("   NOTIFY pgrst, 'reload schema';")

    print("=" * 60)

if __name__ == '__main__':
    refresh_schema_cache()
