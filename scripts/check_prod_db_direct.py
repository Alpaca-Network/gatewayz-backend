#!/usr/bin/env python3
"""
Direct connection to production database using explicit credentials
"""

import subprocess
import json
from supabase import create_client


def get_railway_var(var_name):
    """Get a specific variable from Railway."""
    try:
        result = subprocess.run(
            ['railway', 'variables'],
            capture_output=True,
            text=True,
            timeout=10
        )

        lines = result.stdout.split('\n')
        for i, line in enumerate(lines):
            if var_name in line and i + 1 < len(lines):
                # Extract value from the table format
                parts = line.split('│')
                if len(parts) >= 3:
                    # The value is in the next line or same line depending on truncation
                    value_parts = []
                    for j in range(i, min(i + 10, len(lines))):
                        if '│' in lines[j]:
                            line_parts = lines[j].split('│')
                            if len(line_parts) >= 3:
                                val = line_parts[2].strip()
                                if val and val != '║':
                                    value_parts.append(val)
                                    if len(val) > 30:  # Full value likely
                                        break

                    full_value = ''.join(value_parts).strip()
                    if full_value:
                        return full_value

    except Exception as e:
        print(f"Error getting {var_name}: {e}")

    return None


def main():
    print("=" * 80)
    print("PRODUCTION DATABASE TABLE AUDIT")
    print("=" * 80)
    print()

    print("🔍 Fetching production credentials from Railway...")

    url = get_railway_var("SUPABASE_URL")
    key = get_railway_var("SUPABASE_KEY")

    if not url or not key:
        print("❌ Could not fetch credentials from Railway")
        print(f"   URL: {'✓' if url else '✗'}")
        print(f"   KEY: {'✓' if key else '✗'}")
        return

    # Clean up the values
    url = url.strip()
    key = key.strip()

    print(f"✅ URL: {url[:50]}...")
    print(f"✅ KEY: {key[:30]}...")
    print()

    try:
        client = create_client(url, key)
        print("✅ Connected to production Supabase")
    except Exception as e:
        print(f"❌ Failed to connect: {e}")
        return

    print()
    print("=" * 80)
    print("TABLE ROW COUNTS")
    print("=" * 80)

    # Tables to check
    tables_to_check = {
        "Empty/Legacy Tables": [
            'admin_users',
            'openrouter_apps',
            'openrouter_models',
            'trial_config',
            'usage_records',
            'pricing_tiers',
            'rate_limits',
        ],
        "Active Core Tables": [
            'users',
            'api_keys_new',
            'chat_completion_requests',
            'activity_log',
            'payments',
            'models',
            'providers',
        ],
        "Other Suspicious": [
            'reconciliation_logs',
            'temporary_email_domains',
        ]
    }

    results = {}

    for category, tables in tables_to_check.items():
        print()
        print(f"📊 {category}")
        print("-" * 80)

        for table in tables:
            try:
                result = client.table(table).select("*", count="exact").limit(1).execute()
                count = result.count if result.count is not None else 0
                results[table] = count

                if count == 0:
                    status = "🗑️  EMPTY"
                elif count < 10:
                    status = "⚠️  Very small"
                elif count < 100:
                    status = "📦 Small"
                else:
                    status = "✅ Active"

                print(f"  {table:35s} → {status:15s} ({count:,} rows)")

            except Exception as e:
                error_msg = str(e)[:60]
                results[table] = f"Error: {error_msg}"
                print(f"  {table:35s} → ❌ {error_msg}")

    print()
    print("=" * 80)
    print("CLEANUP RECOMMENDATIONS")
    print("=" * 80)

    empty_tables = [t for t, c in results.items() if c == 0]

    if empty_tables:
        print()
        print(f"🗑️  SAFE TO DROP ({len(empty_tables)} tables):")
        print("-" * 80)
        for table in empty_tables:
            print(f"  • {table}")

        print()
        print("SQL to drop empty tables:")
        print()
        for table in empty_tables:
            print(f"  DROP TABLE IF EXISTS public.\"{table}\" CASCADE;")

    print()
    print("=" * 80)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
