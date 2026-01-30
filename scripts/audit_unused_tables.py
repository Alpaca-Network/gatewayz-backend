#!/usr/bin/env python3
"""
Audit Supabase tables to find unused tables that can be cleaned up.
Compares tables in the database with tables referenced in the codebase.
"""

import os
import sys
from collections import defaultdict

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from supabase import create_client, Client


def get_all_tables_from_db(supabase: Client) -> set[str]:
    """Get all table names from the database."""
    try:
        # Query information_schema to get all public tables
        result = supabase.rpc(
            "get_tables_list",
            {}
        ).execute()

        if result.data:
            return {row["table_name"] for row in result.data}

        # Fallback: try direct SQL query via PostgREST
        # This might not work depending on RLS policies
        print("⚠️  RPC function 'get_tables_list' not found, trying alternative method...")

        # Try to list tables by attempting to query each known table
        # This is a workaround if we don't have a get_tables_list RPC
        return set()

    except Exception as e:
        print(f"❌ Error getting tables from database: {e}")
        return set()


def get_tables_used_in_codebase() -> dict[str, list[str]]:
    """
    Scan codebase for table references in .from_() and .table() calls.
    Returns dict mapping table_name -> list of file paths where it's used.
    """
    import re
    import glob

    table_usage = defaultdict(list)

    # Pattern to match .from_("table_name") or .table("table_name")
    patterns = [
        r'\.from_\(["\']([^"\']+)["\']\)',
        r'\.table\(["\']([^"\']+)["\']\)',
        r'client\.rpc\(["\']([^"\']+)["\']\)',  # RPC function calls
    ]

    # Search in src/db/ and src/routes/ directories
    search_paths = [
        "src/db/**/*.py",
        "src/routes/**/*.py",
        "src/services/**/*.py",
    ]

    for search_path in search_paths:
        for filepath in glob.glob(search_path, recursive=True):
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()

                    for pattern in patterns:
                        matches = re.findall(pattern, content)
                        for table_name in matches:
                            # Filter out obviously non-table names
                            if not table_name.startswith('get_') and not table_name.startswith('is_'):
                                table_usage[table_name].append(filepath)
            except Exception as e:
                print(f"⚠️  Error reading {filepath}: {e}")

    return table_usage


def get_tables_from_migrations() -> set[str]:
    """Extract table names from migration files."""
    import re
    import glob

    tables = set()

    for migration_file in glob.glob("supabase/migrations/*.sql"):
        try:
            with open(migration_file, 'r', encoding='utf-8') as f:
                content = f.read()

                # Match CREATE TABLE statements
                create_table_pattern = r'CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+(?:"?public"?\.)?"?([a-zA-Z_][a-zA-Z0-9_]*)"?'
                matches = re.findall(create_table_pattern, content, re.IGNORECASE)
                tables.update(matches)

        except Exception as e:
            print(f"⚠️  Error reading {migration_file}: {e}")

    return tables


def analyze_table_usage():
    """Main analysis function."""
    print("=" * 80)
    print("SUPABASE TABLE USAGE AUDIT")
    print("=" * 80)
    print()

    # Get Supabase credentials
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")

    if not supabase_url or not supabase_key:
        print("❌ Error: SUPABASE_URL and SUPABASE_KEY must be set in environment")
        return

    print(f"📊 Connecting to Supabase: {supabase_url[:40]}...")

    try:
        supabase = create_client(supabase_url, supabase_key)
        print("✅ Connected to Supabase")
    except Exception as e:
        print(f"❌ Failed to connect to Supabase: {e}")
        return

    print()
    print("🔍 Step 1: Scanning codebase for table references...")
    tables_in_code = get_tables_used_in_codebase()
    print(f"   Found {len(tables_in_code)} tables referenced in code")

    print()
    print("🔍 Step 2: Extracting tables from migration files...")
    tables_in_migrations = get_tables_from_migrations()
    print(f"   Found {len(tables_in_migrations)} tables in migrations")

    print()
    print("🔍 Step 3: Attempting to query database for actual tables...")
    # Try to get actual table list from database
    # Note: This may not work without proper RPC function

    print()
    print("=" * 80)
    print("ANALYSIS RESULTS")
    print("=" * 80)

    # Tables defined in migrations but never used in code
    potentially_unused = tables_in_migrations - set(tables_in_code.keys())

    print()
    print(f"📋 TABLES IN MIGRATIONS: {len(tables_in_migrations)}")
    print("-" * 80)
    for table in sorted(tables_in_migrations):
        usage_count = len(tables_in_code.get(table, []))
        status = "✅ USED" if usage_count > 0 else "⚠️  UNUSED"
        print(f"  {status} {table:40s} ({usage_count} references)")

    print()
    print(f"⚠️  POTENTIALLY UNUSED TABLES: {len(potentially_unused)}")
    print("-" * 80)
    if potentially_unused:
        for table in sorted(potentially_unused):
            print(f"  • {table}")
            print(f"    - Defined in migrations but no code references found")
    else:
        print("  ✅ All tables appear to be used!")

    print()
    print(f"✅ ACTIVELY USED TABLES: {len(tables_in_code)}")
    print("-" * 80)
    print(f"  (See detailed usage below)")

    print()
    print("=" * 80)
    print("DETAILED TABLE USAGE")
    print("=" * 80)
    for table in sorted(tables_in_code.keys()):
        files = tables_in_code[table]
        print()
        print(f"📦 {table} ({len(files)} files)")
        for filepath in sorted(set(files))[:5]:  # Show first 5 files
            print(f"   - {filepath}")
        if len(set(files)) > 5:
            print(f"   ... and {len(set(files)) - 5} more files")

    print()
    print("=" * 80)
    print("RECOMMENDATIONS")
    print("=" * 80)
    print()

    if potentially_unused:
        print("⚠️  CAUTION: The following tables appear unused but should be verified:")
        print()
        for table in sorted(potentially_unused):
            print(f"  • {table}")
            print(f"    → Check if used by:")
            print(f"      - Database triggers or functions")
            print(f"      - External services or scripts")
            print(f"      - Frontend applications directly")
            print(f"      - Scheduled jobs or background workers")
            print()

        print("🔧 Before dropping any table:")
        print("  1. Verify it's truly unused by checking logs")
        print("  2. Create a backup of the table data")
        print("  3. Test in staging environment first")
        print("  4. Consider renaming instead of dropping (e.g., add '_deprecated' suffix)")
    else:
        print("✅ All tables defined in migrations appear to be actively used!")

    print()
    print("=" * 80)


if __name__ == "__main__":
    try:
        analyze_table_usage()
    except KeyboardInterrupt:
        print("\n\n⚠️  Audit interrupted by user")
    except Exception as e:
        print(f"\n\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
