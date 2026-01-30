#!/usr/bin/env python3
"""
Quick table audit - analyzes code usage vs migrations
Does NOT require database connection - works offline
"""

import re
import glob
from collections import defaultdict


def get_tables_used_in_codebase():
    """Scan codebase for table references."""
    table_usage = defaultdict(list)

    patterns = [
        r'\.from_\(["\']([^"\']+)["\']\)',
        r'\.table\(["\']([^"\']+)["\']\)',
    ]

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
                            # Filter out RPC functions
                            if not table_name.startswith(('get_', 'is_', 'check_', 'increment', 'refresh_', 'search_')):
                                table_usage[table_name].append(filepath)
            except Exception as e:
                print(f"⚠️  Error reading {filepath}: {e}")

    return table_usage


def get_tables_from_migrations():
    """Extract table names from migration files."""
    tables = set()

    for migration_file in glob.glob("supabase/migrations/*.sql"):
        try:
            with open(migration_file, 'r', encoding='utf-8') as f:
                content = f.read()

                # Match CREATE TABLE statements
                pattern = r'CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+(?:"?public"?\.)?"?([a-zA-Z_][a-zA-Z0-9_]*)"?'
                matches = re.findall(pattern, content, re.IGNORECASE)
                tables.update(matches)

        except Exception as e:
            print(f"⚠️  Error reading {migration_file}: {e}")

    return tables


def main():
    print("=" * 80)
    print("SUPABASE TABLE AUDIT - CODE vs MIGRATIONS")
    print("=" * 80)
    print()

    print("🔍 Scanning codebase for table references...")
    tables_in_code = get_tables_used_in_codebase()
    print(f"   ✅ Found {len(tables_in_code)} tables referenced in code")

    print()
    print("🔍 Extracting tables from migration files...")
    tables_in_migrations = get_tables_from_migrations()
    print(f"   ✅ Found {len(tables_in_migrations)} tables in migrations")

    print()
    print("=" * 80)
    print("ANALYSIS RESULTS")
    print("=" * 80)

    # Tables defined in migrations but never used in code
    potentially_unused = tables_in_migrations - set(tables_in_code.keys())
    actively_used = tables_in_migrations & set(tables_in_code.keys())

    print()
    print(f"📊 SUMMARY")
    print("-" * 80)
    print(f"  Total tables in migrations:     {len(tables_in_migrations)}")
    print(f"  Tables used in code:            {len(actively_used)}")
    print(f"  Tables NOT referenced in code:  {len(potentially_unused)}")

    if potentially_unused:
        print()
        print(f"⚠️  TABLES WITHOUT CODE REFERENCES ({len(potentially_unused)})")
        print("-" * 80)

        # Categorize by likely purpose
        legacy_tables = []
        view_tables = []
        config_tables = []
        unknown_tables = []

        for table in sorted(potentially_unused):
            if 'openrouter' in table.lower() or 'legacy' in table.lower():
                legacy_tables.append(table)
            elif table.startswith('v_') or table.endswith('_view'):
                view_tables.append(table)
            elif 'config' in table.lower() or 'settings' in table.lower():
                config_tables.append(table)
            else:
                unknown_tables.append(table)

        if legacy_tables:
            print()
            print("  🗄️  LEGACY/DEPRECATED TABLES (safe to drop if confirmed unused):")
            for table in legacy_tables:
                print(f"     • {table}")

        if view_tables:
            print()
            print("  👁️  VIEWS/MATERIALIZED TABLES (may be queried directly by frontend):")
            for table in view_tables:
                print(f"     • {table}")

        if config_tables:
            print()
            print("  ⚙️  CONFIGURATION TABLES (may be read without explicit code):")
            for table in config_tables:
                print(f"     • {table}")

        if unknown_tables:
            print()
            print("  ❓ OTHER TABLES (needs investigation):")
            for table in unknown_tables:
                print(f"     • {table}")

    print()
    print(f"✅ ACTIVELY USED TABLES ({len(actively_used)})")
    print("-" * 80)

    # Group by usage frequency
    usage_counts = [(table, len(tables_in_code[table])) for table in actively_used]
    usage_counts.sort(key=lambda x: x[1], reverse=True)

    print()
    print("  Top 15 most referenced tables:")
    for table, count in usage_counts[:15]:
        print(f"     {table:35s} → {count:3d} references")

    print()
    print("=" * 80)
    print("DETAILED USAGE OF POTENTIALLY UNUSED TABLES")
    print("=" * 80)

    if potentially_unused:
        print()
        print("⚠️  Before dropping these tables, verify they are NOT:")
        print("   1. Used by database triggers/functions")
        print("   2. Accessed directly by frontend applications")
        print("   3. Used by external services or cron jobs")
        print("   4. Required by Supabase realtime subscriptions")
        print("   5. Populated by webhooks (e.g., Stripe)")
        print()
        print("🔧 RECOMMENDED ACTION:")
        print("   1. Check database logs for recent activity on these tables")
        print("   2. Review table row counts (empty tables are safer to drop)")
        print("   3. Rename to '<table>_deprecated' first (soft delete)")
        print("   4. Monitor for 30 days before permanent deletion")
    else:
        print()
        print("✅ All tables in migrations are referenced in code!")
        print("   No cleanup needed at this time.")

    print()
    print("=" * 80)
    print("TABLES USED IN CODE BUT NOT IN MIGRATIONS")
    print("=" * 80)

    code_only_tables = set(tables_in_code.keys()) - tables_in_migrations
    if code_only_tables:
        print()
        print(f"⚠️  Found {len(code_only_tables)} tables referenced in code but NOT in migrations:")
        print("   (These might be views, or migrations we don't have locally)")
        print()
        for table in sorted(code_only_tables):
            print(f"     • {table} → {len(tables_in_code[table])} references")
    else:
        print()
        print("✅ All code references match migration definitions")

    print()
    print("=" * 80)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Audit interrupted by user")
    except Exception as e:
        print(f"\n\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
