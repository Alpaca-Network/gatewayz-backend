#!/usr/bin/env python3
"""
Check if the models table optimization indexes have been applied
"""

import os
import sys
from supabase import create_client

# Get Supabase credentials from environment
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ Error: SUPABASE_URL and SUPABASE_KEY environment variables must be set")
    print("\nSet them with:")
    print("  export SUPABASE_URL='your-url'")
    print("  export SUPABASE_KEY='your-key'")
    sys.exit(1)

# Create Supabase client
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Query to check for the optimization indexes
check_indexes_query = """
SELECT
    indexname,
    indexdef
FROM pg_indexes
WHERE tablename = 'models'
    AND schemaname = 'public'
ORDER BY indexname;
"""

print("🔍 Checking models table indexes...\n")

try:
    # Execute the query using RPC to run raw SQL
    response = supabase.rpc('exec_sql', {
        'query': check_indexes_query
    }).execute()

    if response.data:
        existing_indexes = {row['indexname'] for row in response.data}

        # Indexes we expect from the optimization migration
        expected_indexes = {
            'idx_models_active_name',
            'idx_models_active_provider',
            'idx_models_active_modality',
            'idx_models_catalog_covering',
            'idx_models_search_covering',
            'idx_models_model_id_trgm',
            'idx_models_model_name_trgm',
            'idx_models_by_price',
            'idx_models_by_context',
            'idx_models_provider_join',
            'idx_models_search_vector',
        }

        applied_indexes = existing_indexes & expected_indexes
        missing_indexes = expected_indexes - existing_indexes

        print(f"✅ Found {len(applied_indexes)} optimization indexes:")
        for idx in sorted(applied_indexes):
            print(f"   - {idx}")

        if missing_indexes:
            print(f"\n❌ Missing {len(missing_indexes)} optimization indexes:")
            for idx in sorted(missing_indexes):
                print(f"   - {idx}")
            print("\n📋 The optimization migration has NOT been fully applied.")
            print("   Run: supabase db push")
        else:
            print("\n✅ All optimization indexes are applied!")

        print(f"\n📊 Total indexes on models table: {len(existing_indexes)}")

    else:
        print("❌ Could not retrieve index information")

except Exception as e:
    print(f"❌ Error checking indexes: {e}")
    print("\nTrying alternative method...")

    # Alternative: Check if pg_trgm extension exists (indicator of optimization)
    try:
        ext_query = """
        SELECT extname, extversion
        FROM pg_extension
        WHERE extname = 'pg_trgm';
        """

        response = supabase.rpc('exec_sql', {'query': ext_query}).execute()

        if response.data and len(response.data) > 0:
            print("✅ pg_trgm extension is installed (partial indicator)")
        else:
            print("❌ pg_trgm extension NOT installed")
            print("   The optimization migration has likely NOT been applied")

    except Exception as e2:
        print(f"❌ Could not check extensions: {e2}")
        print("\nPlease run the migration manually:")
        print("  supabase db push")
