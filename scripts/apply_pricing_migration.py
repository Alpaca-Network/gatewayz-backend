#!/usr/bin/env python3
"""
Apply pricing sync migration directly using psycopg2
"""

import os
import sys

try:
    import psycopg2
except ImportError:
    print("Error: psycopg2 not installed")
    print("Install with: pip install psycopg2-binary")
    sys.exit(1)

# Database connection details
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://ynleroehyrmaafkgjgmr.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

# Construct database URL
# Format: postgresql://postgres.[project-ref]:[password]@aws-0-us-west-1.pooler.supabase.com:6543/postgres
PROJECT_REF = "ynleroehyrmaafkgjgmr"

print("⚠️  This script requires the database password")
print()
print("Get your password from:")
print(f"  https://supabase.com/dashboard/project/{PROJECT_REF}/settings/database")
print()

password = input("Enter database password: ").strip()

if not password:
    print("Error: Password required")
    sys.exit(1)

DB_URL = f"postgresql://postgres.{PROJECT_REF}:{password}@aws-0-us-west-1.pooler.supabase.com:6543/postgres"

# Read migration file
MIGRATION_FILE = "supabase/migrations/20260126000001_add_pricing_sync_tables.sql"

print(f"\nReading migration: {MIGRATION_FILE}")

with open(MIGRATION_FILE, 'r') as f:
    migration_sql = f.read()

print(f"Migration size: {len(migration_sql)} bytes")
print("\nConnecting to database...")

try:
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    print("✅ Connected!")
    print("\nExecuting migration...")

    # Execute the migration
    cur.execute(migration_sql)
    conn.commit()

    print("✅ Migration applied successfully!")

    # Verify tables exist
    print("\nVerifying tables...")

    tables = [
        'model_pricing_history',
        'pricing_sync_log',
        'pricing_sync_lock',
        'pricing_sync_jobs'
    ]

    for table in tables:
        cur.execute(f"SELECT to_regclass('public.{table}')")
        result = cur.fetchone()[0]
        if result:
            print(f"  ✅ {table}")
        else:
            print(f"  ❌ {table} (not found)")

    cur.close()
    conn.close()

    print("\n🎉 Done!")

except Exception as e:
    print(f"\n❌ Error: {e}")
    sys.exit(1)
