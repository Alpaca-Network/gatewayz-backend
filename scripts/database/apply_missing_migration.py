#!/usr/bin/env python3
"""
Apply missing database migration for rate_limit_configs and api_key_audit_logs tables.

This script applies the migration from 20251225000000_restore_rate_limit_configs_and_audit_logs.sql
to restore tables that were accidentally dropped.

Usage:
    python scripts/database/apply_missing_migration.py
"""

import os
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from config.supabase_config import get_supabase_client

def check_tables_exist(client) -> dict[str, bool]:
    """Check if the required tables exist."""
    tables_to_check = ["rate_limit_configs", "api_key_audit_logs"]
    results = {}

    for table_name in tables_to_check:
        try:
            # Try to query the table (limit 0 to avoid fetching data)
            client.table(table_name).select("id").limit(0).execute()
            results[table_name] = True
            print(f"✅ Table '{table_name}' exists")
        except Exception as e:
            if "Could not find the table" in str(e) or "PGRST205" in str(e):
                results[table_name] = False
                print(f"❌ Table '{table_name}' is missing")
            else:
                print(f"⚠️  Error checking table '{table_name}': {e}")
                results[table_name] = False

    return results


def apply_migration():
    """Apply the missing migration SQL file."""
    # Read the migration file
    migration_file = Path(__file__).parent.parent.parent / "supabase" / "migrations" / "20251225000000_restore_rate_limit_configs_and_audit_logs.sql"

    if not migration_file.exists():
        print(f"❌ Migration file not found: {migration_file}")
        return False

    print(f"📝 Reading migration file: {migration_file}")

    with open(migration_file, 'r') as f:
        migration_sql = f.read()

    # Get Supabase client
    client = get_supabase_client()

    # Check current state
    print("\n🔍 Checking current database state...")
    table_status = check_tables_exist(client)

    if all(table_status.values()):
        print("\n✅ All tables already exist! No migration needed.")
        return True

    print("\n🚀 Applying migration...")
    print("=" * 60)

    # Execute the migration using Supabase RPC or direct SQL
    # Note: Supabase Python client doesn't have direct SQL execution
    # This needs to be run via Supabase CLI or dashboard

    print("\n⚠️  MANUAL STEP REQUIRED:")
    print("=" * 60)
    print("The Python Supabase client doesn't support direct SQL execution.")
    print("Please apply this migration using one of these methods:")
    print("")
    print("METHOD 1: Supabase CLI (Recommended)")
    print("  1. Install Supabase CLI: https://supabase.com/docs/guides/cli")
    print("  2. Link to your project: supabase link")
    print(f"  3. Apply migration: supabase db push")
    print("")
    print("METHOD 2: Supabase Dashboard")
    print("  1. Go to: https://app.supabase.com/project/_/sql/new")
    print(f"  2. Copy and paste the SQL from: {migration_file}")
    print("  3. Click 'Run' to execute")
    print("")
    print("METHOD 3: psql command line")
    print("  1. Get your connection string from Supabase dashboard")
    print(f"  2. Run: psql <connection-string> -f {migration_file}")
    print("=" * 60)

    return False


def main():
    """Main entry point."""
    print("🔧 Database Migration Checker")
    print("=" * 60)
    print("Checking for missing rate_limit_configs and api_key_audit_logs tables")
    print("")

    try:
        apply_migration()
        print("\n✅ Migration check complete!")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
