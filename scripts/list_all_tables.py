#!/usr/bin/env python3
"""List all tables in the local Supabase database"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.supabase_config import get_supabase_client

def list_all_tables():
    supabase = get_supabase_client()

    print("=" * 70)
    print("ALL TABLES IN DATABASE (via PostgREST API)")
    print("=" * 70)

    # Get some known tables to test connection
    known_tables = ['users', 'api_keys', 'payments', 'plans', 'chat_history',
                    'providers', 'models', 'pricing_tiers', 'latest_models']

    existing = []
    missing = []

    for table_name in known_tables:
        try:
            response = supabase.table(table_name).select("*").limit(0).execute()
            existing.append(table_name)
        except Exception as e:
            missing.append((table_name, str(e)))

    print(f"\n✅ EXISTING TABLES ({len(existing)}):")
    for table in existing:
        print(f"   - {table}")

    print(f"\n❌ MISSING TABLES ({len(missing)}):")
    for table, error in missing:
        if 'PGRST205' in error:
            print(f"   - {table} (not found in schema)")
        else:
            print(f"   - {table} ({error[:50]})")

    print("=" * 70)

if __name__ == '__main__':
    list_all_tables()
