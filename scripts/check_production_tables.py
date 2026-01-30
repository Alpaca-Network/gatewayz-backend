#!/usr/bin/env python3
"""
Check production database tables for row counts and usage
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config.supabase_config import get_supabase_client


def check_table_row_count(client, table_name):
    """Get row count for a table."""
    try:
        result = client.table(table_name).select("*", count="exact").limit(1).execute()
        return result.count if result.count is not None else 0
    except Exception as e:
        return f"Error: {str(e)[:50]}"


def main():
    print("=" * 80)
    print("PRODUCTION DATABASE TABLE ROW COUNTS")
    print("=" * 80)
    print()

    try:
        client = get_supabase_client()
        print("✅ Connected to production Supabase")
    except Exception as e:
        print(f"❌ Failed to connect: {e}")
        return

    # Tables identified as potentially unused
    suspicious_tables = [
        'openrouter_apps',
        'trial_config',
        'admin_users',
        'pricing_tiers',
        'reconciliation_logs',
        'temporary_email_domains',
    ]

    # Legacy tables to check
    legacy_tables = [
        'openrouter_models',
        'usage_records',
        'rate_limits',
    ]

    # All tables to verify
    all_check_tables = suspicious_tables + legacy_tables

    print()
    print("🔍 Checking potentially unused tables...")
    print("-" * 80)

    empty_tables = []
    small_tables = []
    active_tables = []

    for table in sorted(all_check_tables):
        count = check_table_row_count(client, table)

        if isinstance(count, str):  # Error occurred
            status = f"❌ {count}"
        elif count == 0:
            status = f"🗑️  EMPTY (0 rows)"
            empty_tables.append(table)
        elif count < 10:
            status = f"⚠️  Very small ({count} rows)"
            small_tables.append((table, count))
        elif count < 100:
            status = f"📦 Small ({count} rows)"
            small_tables.append((table, count))
        else:
            status = f"✅ Active ({count} rows)"
            active_tables.append((table, count))

        print(f"  {table:35s} → {status}")

    print()
    print("=" * 80)
    print("RECOMMENDATIONS FOR CLEANUP")
    print("=" * 80)

    if empty_tables:
        print()
        print(f"🗑️  SAFE TO DROP - Empty tables ({len(empty_tables)}):")
        print("-" * 80)
        for table in empty_tables:
            print(f"  • {table}")
            print(f"    ✅ Can be dropped immediately (backup schema first)")

    if small_tables:
        print()
        print(f"⚠️  REVIEW NEEDED - Tables with few rows ({len(small_tables)}):")
        print("-" * 80)
        for table, count in small_tables:
            print(f"  • {table} ({count} rows)")
            print(f"    → Review content before dropping")

    if active_tables:
        print()
        print(f"⚠️  CAUTION - Tables with data ({len(active_tables)}):")
        print("-" * 80)
        for table, count in active_tables:
            print(f"  • {table} ({count} rows)")
            print(f"    → Investigate before dropping (may be used by other services)")

    print()
    print("=" * 80)
    print("NEXT STEPS")
    print("=" * 80)
    print()
    print("1. For EMPTY tables:")
    print("   → Can drop immediately with: DROP TABLE IF EXISTS <table>;")
    print()
    print("2. For tables with FEW rows:")
    print("   → Export data first: supabase db dump")
    print("   → Rename to <table>_deprecated and monitor for 30 days")
    print()
    print("3. For tables with MANY rows:")
    print("   → Investigate thoroughly before any action")
    print("   → Check application logs for access patterns")
    print()

    # Check some key active tables for comparison
    print()
    print("📊 KEY ACTIVE TABLES (for comparison):")
    print("-" * 80)
    key_tables = ['users', 'api_keys_new', 'chat_completion_requests', 'activity_log', 'payments']
    for table in key_tables:
        count = check_table_row_count(client, table)
        print(f"  {table:35s} → {count} rows")

    print()
    print("=" * 80)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
