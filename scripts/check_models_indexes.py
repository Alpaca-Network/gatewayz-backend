#!/usr/bin/env python3
"""
Check if the models table optimization indexes have been applied to Supabase
"""

import os
import sys

# Try to load from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from supabase import create_client
except ImportError:
    print("❌ supabase-py not installed")
    print("Run: pip install supabase")
    sys.exit(1)

# Get credentials from environment
url = os.getenv('SUPABASE_URL')
key = os.getenv('SUPABASE_KEY')

if not url or not key:
    print("❌ SUPABASE_URL or SUPABASE_KEY not set")
    print("\nPlease set these environment variables:")
    print("  export SUPABASE_URL='your-project-url'")
    print("  export SUPABASE_KEY='your-service-role-key'")
    print("\nOr add them to your .env file")
    sys.exit(1)

print("🔍 Checking models table optimization status...\n")
print(f"📡 Connecting to: {url[:40]}...\n")

try:
    # Create client
    client = create_client(url, key)

    # Check if models table exists
    print("1️⃣ Checking if models table exists...")
    try:
        result = client.table('models').select('id').limit(1).execute()
        count_result = client.table('models').select('id', count='exact').limit(1).execute()
        row_count = count_result.count if hasattr(count_result, 'count') else 0
        print(f"   ✅ Models table exists with {row_count:,} rows\n")
    except Exception as e:
        print(f"   ❌ Models table not found: {e}")
        sys.exit(1)

    # Check for pg_trgm extension (needed for optimization indexes)
    print("2️⃣ Checking for pg_trgm extension (required for text search)...")
    try:
        # Try a query that would use trigram if the migration was applied
        # This won't error even if extension is missing, just won't use the index
        result = client.table('models').select('model_id').ilike('model_id', '%gpt%').limit(1).execute()
        print("   ℹ️  Text search query works (but can't confirm if trigram indexes exist)\n")
    except Exception as e:
        print(f"   ⚠️  Query failed: {e}\n")

    # Check for the search_vector column (added by optimization migration)
    print("3️⃣ Checking for search_vector column (added by optimization)...")
    try:
        result = client.table('models').select('id,search_vector').limit(1).execute()
        if result.data and len(result.data) > 0:
            has_search_vector = 'search_vector' in result.data[0]
            if has_search_vector:
                print("   ✅ search_vector column EXISTS - optimization migration is likely applied!\n")
            else:
                print("   ❌ search_vector column NOT FOUND - optimization migration NOT applied\n")
        else:
            # Try to select search_vector specifically
            result2 = client.table('models').select('search_vector').limit(1).execute()
            print("   ✅ search_vector column EXISTS - optimization migration is likely applied!\n")
    except Exception as e:
        error_str = str(e).lower()
        if 'search_vector' in error_str and ('does not exist' in error_str or 'column' in error_str):
            print("   ❌ search_vector column NOT FOUND")
            print("   📋 The optimization migration has NOT been applied yet\n")
        else:
            print(f"   ⚠️  Could not check search_vector: {e}\n")

    # Summary and recommendations
    print("=" * 70)
    print("📊 SUMMARY")
    print("=" * 70)
    print("\nTo apply the optimization migration:")
    print()
    print("  Option 1: Using Supabase CLI")
    print("    supabase db push")
    print()
    print("  Option 2: Using Supabase Dashboard")
    print("    1. Go to: https://supabase.com/dashboard/project/.../sql")
    print("    2. Copy content from: supabase/migrations/20251220070000_optimize_models_indexes.sql")
    print("    3. Paste and run in SQL Editor")
    print()
    print("  Option 3: Direct file application")
    print("    cat supabase/migrations/20251220070000_optimize_models_indexes.sql | \\")
    print("      supabase db execute --db-url \"$DATABASE_URL\"")
    print()
    print("Expected improvements after applying:")
    print("  • 50-80% faster queries on active models")
    print("  • 3-5x faster text search (LIKE/ILIKE)")
    print("  • 2-3x faster sorted results")
    print()

except Exception as e:
    print(f"❌ Error: {e}")
    sys.exit(1)
