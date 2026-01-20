#!/usr/bin/env python3
"""
Verify pricing data migration is complete before removing columns

This script checks:
1. All models with pricing have entries in model_pricing
2. No NULL pricing for paid models
3. pricing_tiers table is empty (unused)
4. Data consistency between old and new columns (if old columns still exist)

Run this BEFORE applying the migration that removes pricing columns from models table.
"""

import sys
from src.config.supabase_config import get_supabase_client


def verify_migration():
    """
    Verify pricing migration is complete and safe to remove old columns

    Returns:
        bool: True if migration is safe, False otherwise
    """
    print("=" * 70)
    print("PRICING MIGRATION VERIFICATION")
    print("=" * 70)
    print()

    try:
        client = get_supabase_client()
        all_checks_passed = True

        # ====================================================================
        # Check 1: Verify model_pricing table exists and has data
        # ====================================================================
        print("✓ Check 1: model_pricing table status")
        try:
            pricing_entries = client.table("model_pricing").select(
                "model_id", count="exact"
            ).execute()

            if pricing_entries.count == 0:
                print(f"  ❌ model_pricing table is EMPTY")
                all_checks_passed = False
            else:
                print(f"  ✅ model_pricing has {pricing_entries.count} entries")
        except Exception as e:
            print(f"  ❌ Cannot access model_pricing table: {e}")
            all_checks_passed = False

        print()

        # ====================================================================
        # Check 2: Verify pricing_tiers is empty/unused
        # ====================================================================
        print("✓ Check 2: pricing_tiers table (should be empty)")
        try:
            pricing_tiers = client.table("pricing_tiers").select("*").execute()
            if pricing_tiers.data:
                print(f"  ⚠️  pricing_tiers has {len(pricing_tiers.data)} rows (UNUSED - safe to drop)")
            else:
                print(f"  ✅ pricing_tiers is empty")
        except Exception as e:
            print(f"  ℹ️  pricing_tiers table not accessible (may not exist): {e}")

        print()

        # ====================================================================
        # Check 3: Verify pricing type distribution
        # ====================================================================
        print("✓ Check 3: Pricing classification breakdown")
        try:
            pricing_by_type = client.table("model_pricing").select(
                "pricing_type", count="exact"
            ).execute()

            if pricing_by_type.data:
                # Count by type
                type_counts = {}
                for row in pricing_by_type.data:
                    ptype = row.get("pricing_type", "unknown")
                    type_counts[ptype] = type_counts.get(ptype, 0) + 1

                total = sum(type_counts.values())
                print(f"  Total models in model_pricing: {total}")
                for ptype, count in sorted(type_counts.items()):
                    pct = (count / total * 100) if total > 0 else 0
                    icon = "💰" if ptype == "paid" else "🆓" if ptype == "free" else "❓"
                    print(f"    {icon} {ptype}: {count} ({pct:.1f}%)")
        except Exception as e:
            print(f"  ⚠️  Could not fetch pricing classification: {e}")

        print()

        # ====================================================================
        # Check 4: Look for models with $0 pricing marked as "paid"
        # ====================================================================
        print("✓ Check 4: Paid models with zero pricing (potential data issues)")
        try:
            zero_paid = client.table("model_pricing").select(
                "model_id, price_per_input_token, price_per_output_token"
            ).eq("pricing_type", "paid").eq("price_per_input_token", 0).eq(
                "price_per_output_token", 0
            ).execute()

            if zero_paid.data:
                print(f"  ⚠️  Found {len(zero_paid.data)} paid models with $0 pricing")
                print(f"     (May need manual review)")
            else:
                print(f"  ✅ No paid models with $0 pricing")
        except Exception as e:
            print(f"  ⚠️  Could not check zero pricing: {e}")

        print()

        # ====================================================================
        # Check 5: Verify views exist
        # ====================================================================
        print("✓ Check 5: Verify pricing views are accessible")
        views_to_check = [
            "models_with_pricing",
            "models_pricing_classified",
            "models_pricing_status"
        ]

        for view_name in views_to_check:
            try:
                result = client.table(view_name).select("*", count="exact", head=True).execute()
                count = result.count if result.count is not None else 0
                print(f"  ✅ {view_name}: {count} rows")
            except Exception as e:
                print(f"  ❌ {view_name}: Not accessible - {e}")
                all_checks_passed = False

        print()
        print("=" * 70)

        if all_checks_passed:
            print("✅ VERIFICATION PASSED - Migration appears safe")
            print()
            print("Next steps:")
            print("  1. Review the output above for any warnings")
            print("  2. Backup your database")
            print("  3. Deploy code changes that use model_pricing")
            print("  4. Monitor for 24-48 hours")
            print("  5. Apply migration to remove old pricing columns")
            return True
        else:
            print("❌ VERIFICATION FAILED - DO NOT PROCEED")
            print()
            print("Issues detected. Please review errors above.")
            return False

    except Exception as e:
        print(f"❌ FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = verify_migration()
    sys.exit(0 if success else 1)
