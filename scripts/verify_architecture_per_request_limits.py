#!/usr/bin/env python3
"""
Verify if architecture and per_request_limits columns can be safely removed
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.config.supabase_config import get_supabase_client


def main():
    print("=" * 80)
    print("VERIFICATION: Can architecture and per_request_limits be removed?")
    print("=" * 80)
    print()

    try:
        supabase = get_supabase_client()

        # Query 1: Count non-null values
        print("📊 Checking column usage...")
        result = supabase.rpc('execute_sql', {
            'query': """
                SELECT
                    COUNT(*) as total_models,
                    COUNT(architecture) as non_null_architecture,
                    COUNT(per_request_limits) as non_null_per_request_limits
                FROM models;
            """
        }).execute()

        if result.data:
            stats = result.data[0] if isinstance(result.data, list) else result.data
            total = stats.get('total_models', 0)
            arch_count = stats.get('non_null_architecture', 0)
            limits_count = stats.get('non_null_per_request_limits', 0)

            print(f"Total models: {total:,}")
            print(f"Non-null architecture: {arch_count:,} ({arch_count/total*100:.1f}%)")
            print(f"Non-null per_request_limits: {limits_count:,} ({limits_count/total*100:.1f}%)")
            print()

        # Direct query approach
        print("📊 Fetching sample data...")
        models = supabase.table('models').select(
            'model_name, architecture, per_request_limits, metadata'
        ).limit(10).execute()

        print(f"\nSample of {len(models.data)} models:")
        print("-" * 80)

        arch_not_null = 0
        limits_not_null = 0
        arch_in_metadata = 0

        for model in models.data:
            if model.get('architecture'):
                arch_not_null += 1
                print(f"✓ {model['model_name']}: has architecture")
            if model.get('per_request_limits'):
                limits_not_null += 1
                print(f"✓ {model['model_name']}: has per_request_limits")

            metadata = model.get('metadata', {})
            if isinstance(metadata, dict) and metadata.get('architecture'):
                arch_in_metadata += 1

        print()
        print("=" * 80)
        print("ANALYSIS")
        print("=" * 80)
        print()

        # Architecture analysis
        if arch_not_null > 0:
            print("⚠️  ARCHITECTURE column:")
            print(f"   - Found {arch_not_null} models with non-null architecture in sample")
            print(f"   - Found {arch_in_metadata} models with architecture in metadata")
            if arch_in_metadata == arch_not_null:
                print("   ✅ All architecture data is ALSO in metadata")
                print("   → Safe to remove after updating code to read from metadata")
            else:
                print("   ⚠️  Architecture data NOT fully mirrored in metadata")
                print("   → Need to migrate data to metadata first")
        else:
            print("✅ ARCHITECTURE column: All NULL in sample")
            print("   → Likely safe to remove")

        print()

        # Per request limits analysis
        if limits_not_null > 0:
            print(f"⚠️  PER_REQUEST_LIMITS column: {limits_not_null} non-null values found")
            print("   → Review before removing")
        else:
            print("✅ PER_REQUEST_LIMITS column: All NULL in sample")
            print("   → Safe to remove")

        print()
        print("=" * 80)
        print("RECOMMENDATION")
        print("=" * 80)
        print()

        # Final recommendation
        if limits_not_null == 0 and arch_not_null == 0:
            print("✅ SAFE TO REMOVE BOTH COLUMNS")
            print()
            print("Both columns appear to be unused. You can:")
            print("1. Remove from schemas")
            print("2. Create migration to drop columns")
            print("3. Deploy")
        elif limits_not_null == 0 and arch_not_null > 0:
            print("⚠️  PARTIAL REMOVAL POSSIBLE")
            print()
            print("per_request_limits:")
            print("  ✅ Safe to remove immediately")
            print()
            print("architecture:")
            print("  ⚠️  Contains data - migration needed")
            print("  1. Ensure data is in metadata")
            print("  2. Update code to read from metadata")
            print("  3. Then drop column")
        else:
            print("⚠️  REVIEW REQUIRED")
            print()
            print("Both columns contain data. Review usage before removing.")

        print()

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
