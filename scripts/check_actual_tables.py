#!/usr/bin/env python3
"""
Script to check what tables actually exist and have data
"""
import os
import sys
from pathlib import Path

# Add src to path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from src.config.supabase_config import get_supabase_client


def main():
    """Check actual tables and data"""
    supabase = get_supabase_client()

    print("=" * 80)
    print("CHECKING DATABASE TABLES")
    print("=" * 80)
    print()

    # Check models table
    print("1. Checking 'models' table:")
    try:
        models_result = supabase.table('models').select('*', count='exact').limit(5).execute()
        print(f"   ✓ 'models' table exists")
        print(f"   Total rows: {models_result.count}")
        if models_result.data:
            print(f"   Sample columns: {list(models_result.data[0].keys())}")
            print(f"   First row: {models_result.data[0]}")
        print()
    except Exception as e:
        print(f"   ✗ Error: {e}")
        print()

    # Check models_catalog table
    print("2. Checking 'models_catalog' table:")
    try:
        catalog_result = supabase.table('models_catalog').select('*', count='exact').limit(5).execute()
        print(f"   ✓ 'models_catalog' table exists")
        print(f"   Total rows: {catalog_result.count}")
        if catalog_result.data:
            print(f"   Sample columns: {list(catalog_result.data[0].keys())}")
            print(f"   First row: {catalog_result.data[0]}")
        print()
    except Exception as e:
        print(f"   ✗ Error: {e}")
        print()

    # Check providers table
    print("3. Checking 'providers' table:")
    try:
        providers_result = supabase.table('providers').select('*', count='exact').limit(5).execute()
        print(f"   ✓ 'providers' table exists")
        print(f"   Total rows: {providers_result.count}")
        if providers_result.data:
            print(f"   Sample columns: {list(providers_result.data[0].keys())}")
            print(f"   First row: {providers_result.data[0]}")
        print()
    except Exception as e:
        print(f"   ✗ Error: {e}")
        print()


if __name__ == "__main__":
    main()
