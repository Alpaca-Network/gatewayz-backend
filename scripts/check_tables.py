#!/usr/bin/env python3
"""Quick script to check if model tables exist in database"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.supabase_config import get_supabase_client

def check_tables():
    """Check if model-related tables exist"""
    supabase = get_supabase_client()

    tables_to_check = ['providers', 'models', 'model_health_history']

    print("=" * 60)
    print("CHECKING DATABASE TABLES")
    print("=" * 60)

    for table_name in tables_to_check:
        try:
            # Try to query the table
            response = supabase.table(table_name).select("*").limit(1).execute()

            # Get count
            count_response = supabase.table(table_name).select("*", count="exact").limit(0).execute()
            count = count_response.count if hasattr(count_response, 'count') else 'Unknown'

            print(f"✅ Table '{table_name}' EXISTS - {count} records")

        except Exception as e:
            print(f"❌ Table '{table_name}' NOT FOUND or ERROR: {e}")

    print("=" * 60)

    # Show sample data from providers table
    print("\nSAMPLE DATA FROM 'providers' TABLE:")
    print("-" * 60)
    try:
        providers = supabase.table("providers").select("id, name, slug, is_active").limit(5).execute()
        if providers.data:
            for p in providers.data:
                print(f"  ID: {p['id']:3} | {p['name']:25} | slug: {p['slug']:20} | active: {p['is_active']}")
        else:
            print("  (No providers found)")
    except Exception as e:
        print(f"  Error: {e}")

    # Show sample data from models table
    print("\nSAMPLE DATA FROM 'models' TABLE:")
    print("-" * 60)
    try:
        models = supabase.table("models").select("id, model_name, provider_id, is_active").limit(5).execute()
        if models.data:
            for m in models.data:
                print(f"  ID: {m['id']:5} | Provider ID: {m['provider_id']:3} | {m['model_name']:50} | active: {m['is_active']}")
        else:
            print("  (No models found)")
    except Exception as e:
        print(f"  Error: {e}")

    print("=" * 60)

if __name__ == '__main__':
    check_tables()
