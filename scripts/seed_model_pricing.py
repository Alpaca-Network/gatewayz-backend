#!/usr/bin/env python3
"""
Phase 1: Data Seeding - Populate model_pricing Table

Seeds model_pricing table from all pricing sources:
1. manual_pricing.json (186 models, per-1M format)
2. google_models_config.py (12 models, per-1K format)
3. Hardcoded pricing in provider clients

Usage:
    python scripts/seed_model_pricing.py --dry-run
    python scripts/seed_model_pricing.py --execute
"""

import argparse
import json
import sys
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.supabase_config import get_supabase_client


class PricingFormat:
    """Pricing format constants."""
    PER_TOKEN = "per_token"
    PER_1K_TOKENS = "per_1k_tokens"
    PER_1M_TOKENS = "per_1m_tokens"


def normalize_to_per_token(value: str | float | Decimal, format: str) -> Decimal:
    """
    Normalize pricing value to per-token format.

    Args:
        value: Pricing value (string or number)
        format: One of PricingFormat values

    Returns:
        Decimal value in per-token format

    Examples:
        >>> normalize_to_per_token("2.50", PricingFormat.PER_1M_TOKENS)
        Decimal('0.0000025')
        >>> normalize_to_per_token(0.00125, PricingFormat.PER_1K_TOKENS)
        Decimal('0.00000125')
    """
    if value is None or value == "":
        return Decimal("0")

    decimal_value = Decimal(str(value))

    if format == PricingFormat.PER_TOKEN:
        return decimal_value
    elif format == PricingFormat.PER_1K_TOKENS:
        return decimal_value / Decimal("1000")
    elif format == PricingFormat.PER_1M_TOKENS:
        return decimal_value / Decimal("1000000")
    else:
        raise ValueError(f"Unknown pricing format: {format}")


def get_model_db_id(client, model_id: str) -> int | None:
    """
    Get database ID for model_id.

    Args:
        client: Supabase client
        model_id: Model identifier (e.g., "openai/gpt-4o")

    Returns:
        Database ID or None if not found
    """
    try:
        result = (
            client.table("models")
            .select("id")
            .eq("model_id", model_id)
            .limit(1)
            .execute()
        )

        if result.data:
            return result.data[0]["id"]
    except Exception as e:
        print(f"      Error looking up model {model_id}: {e}")

    return None


def seed_from_manual_pricing_json(client, dry_run: bool = True) -> Dict[str, Any]:
    """
    Seed from manual_pricing.json.

    Args:
        client: Supabase client
        dry_run: If True, don't write to database

    Returns:
        Stats dict with counts
    """
    print("\n" + "=" * 80)
    print("SOURCE 1: manual_pricing.json")
    print("=" * 80)

    json_path = Path("src/data/manual_pricing.json")
    if not json_path.exists():
        print(f"❌ File not found: {json_path}")
        return {"total_models": 0, "inserted": 0, "errors": 1}

    with open(json_path) as f:
        data = json.load(f)

    stats = {
        "total_models": 0,
        "inserted": 0,
        "skipped_not_found": 0,
        "skipped_already_exists": 0,
        "skipped_zero_pricing": 0,
        "errors": 0
    }

    for gateway, models in data.items():
        # Skip metadata section
        if gateway == "_metadata":
            continue

        print(f"\n  Gateway: {gateway}")

        for model_id, pricing in models.items():
            stats["total_models"] += 1

            # Construct full model ID with gateway prefix
            full_model_id = f"{gateway}/{model_id}"

            # Get database model ID
            db_model_id = get_model_db_id(client, full_model_id)
            if db_model_id is None:
                print(f"    ⚠️  Model not in database: {full_model_id}")
                stats["skipped_not_found"] += 1
                continue

            # Check if pricing already exists
            try:
                existing = (
                    client.table("model_pricing")
                    .select("id")
                    .eq("model_id", db_model_id)
                    .execute()
                )

                if existing.data:
                    print(f"    ⏭️  Already exists: {model_id}")
                    stats["skipped_already_exists"] += 1
                    continue
            except Exception as e:
                print(f"    ❌ Error checking existing pricing for {model_id}: {e}")
                stats["errors"] += 1
                continue

            # Normalize pricing from per-1M to per-token
            try:
                input_price = normalize_to_per_token(
                    pricing.get("prompt", "0"),
                    PricingFormat.PER_1M_TOKENS
                )
                output_price = normalize_to_per_token(
                    pricing.get("completion", "0"),
                    PricingFormat.PER_1M_TOKENS
                )

                # Skip if both prices are zero
                if input_price == 0 and output_price == 0:
                    print(f"    ⏭️  Zero pricing: {model_id}")
                    stats["skipped_zero_pricing"] += 1
                    continue

                pricing_data = {
                    "model_id": db_model_id,
                    "price_per_input_token": float(input_price),
                    "price_per_output_token": float(output_price),
                    "pricing_source": "manual_migration",
                    "last_updated": datetime.now(timezone.utc).isoformat()
                }

                print(f"    ✅ {full_model_id}")
                print(f"       ${pricing.get('prompt')}/1M → ${input_price}/token (input)")
                print(f"       ${pricing.get('completion')}/1M → ${output_price}/token (output)")

                if not dry_run:
                    client.table("model_pricing").insert(pricing_data).execute()
                else:
                    print(f"       [DRY RUN - would insert]")

                stats["inserted"] += 1

            except Exception as e:
                print(f"    ❌ Error processing {full_model_id}: {e}")
                stats["errors"] += 1

    print("\n" + "-" * 80)
    print(f"SUMMARY: manual_pricing.json")
    print(f"  Total models:          {stats['total_models']}")
    print(f"  Inserted:              {stats['inserted']}")
    print(f"  Skipped (not found):   {stats['skipped_not_found']}")
    print(f"  Skipped (exists):      {stats['skipped_already_exists']}")
    print(f"  Skipped (zero):        {stats['skipped_zero_pricing']}")
    print(f"  Errors:                {stats['errors']}")

    return stats


def seed_from_google_config(client, dry_run: bool = True) -> Dict[str, Any]:
    """
    Seed from google_models_config.py.

    Args:
        client: Supabase client
        dry_run: If True, don't write to database

    Returns:
        Stats dict with counts
    """
    print("\n" + "=" * 80)
    print("SOURCE 2: google_models_config.py")
    print("=" * 80)

    stats = {
        "total_models": 0,
        "inserted": 0,
        "skipped_not_found": 0,
        "skipped_already_exists": 0,
        "skipped_zero_pricing": 0,
        "errors": 0
    }

    try:
        from src.services.google_models_config import get_google_models

        google_models = get_google_models()
        print(f"\n  Found {len(google_models)} Google models")

        for model in google_models:
            stats["total_models"] += 1

            # Extract model info from MultiProviderModel object
            model_id = model.id  # Use attribute, not .get()

            # Get pricing from first provider (google-vertex)
            if not model.providers or len(model.providers) == 0:
                print(f"    ⚠️  Model has no providers: {model_id}")
                stats["skipped_not_found"] += 1
                continue

            provider = model.providers[0]  # First provider is google-vertex
            cost_per_1k_input = provider.cost_per_1k_input
            cost_per_1k_output = provider.cost_per_1k_output

            if not model_id:
                print(f"    ⚠️  Model has no ID: {model}")
                stats["skipped_not_found"] += 1
                continue

            if cost_per_1k_input is None or cost_per_1k_output is None:
                print(f"    ⚠️  Model has no pricing: {model_id}")
                stats["skipped_zero_pricing"] += 1
                continue

            # Try multiple ID variations (database may have google-vertex/ prefix)
            model_id_variants = [
                f"google-vertex/{model_id}",
                f"google/{model_id}",
                f"vertex/{model_id}",
                f"vertex-ai/{model_id}",
                model_id
            ]

            db_model_id = None
            matched_id = None
            for variant in model_id_variants:
                db_model_id = get_model_db_id(client, variant)
                if db_model_id:
                    matched_id = variant
                    break

            if db_model_id is None:
                print(f"    ⚠️  Model not in database: {model_id} (tried {len(model_id_variants)} variants)")
                stats["skipped_not_found"] += 1
                continue

            # Check if exists
            try:
                existing = (
                    client.table("model_pricing")
                    .select("id")
                    .eq("model_id", db_model_id)
                    .execute()
                )

                if existing.data:
                    print(f"    ⏭️  Already exists: {matched_id}")
                    stats["skipped_already_exists"] += 1
                    continue
            except Exception as e:
                print(f"    ❌ Error checking existing: {e}")
                stats["errors"] += 1
                continue

            # Normalize pricing from per-1K to per-token
            try:
                input_price = normalize_to_per_token(
                    cost_per_1k_input,
                    PricingFormat.PER_1K_TOKENS  # ⚠️ Google uses per-1K!
                )
                output_price = normalize_to_per_token(
                    cost_per_1k_output,
                    PricingFormat.PER_1K_TOKENS
                )

                pricing_data = {
                    "model_id": db_model_id,
                    "price_per_input_token": float(input_price),
                    "price_per_output_token": float(output_price),
                    "pricing_source": "google_config_migration",
                    "last_updated": datetime.now(timezone.utc).isoformat()
                }

                print(f"    ✅ {matched_id}")
                print(f"       ${cost_per_1k_input}/1K → ${input_price}/token (input)")
                print(f"       ${cost_per_1k_output}/1K → ${output_price}/token (output)")

                if not dry_run:
                    client.table("model_pricing").insert(pricing_data).execute()
                else:
                    print(f"       [DRY RUN - would insert]")

                stats["inserted"] += 1

            except Exception as e:
                print(f"    ❌ Error processing {model_id}: {e}")
                stats["errors"] += 1

    except Exception as e:
        print(f"❌ Failed to load google_models_config: {e}")
        stats["errors"] += 1

    print("\n" + "-" * 80)
    print(f"SUMMARY: google_models_config.py")
    print(f"  Total models:          {stats['total_models']}")
    print(f"  Inserted:              {stats['inserted']}")
    print(f"  Skipped (not found):   {stats['skipped_not_found']}")
    print(f"  Skipped (exists):      {stats['skipped_already_exists']}")
    print(f"  Skipped (zero):        {stats['skipped_zero_pricing']}")
    print(f"  Errors:                {stats['errors']}")

    return stats


def verify_pricing_coverage(client) -> Dict[str, Any]:
    """
    Verify pricing coverage after seeding.

    Args:
        client: Supabase client

    Returns:
        Coverage stats
    """
    print("\n" + "=" * 80)
    print("VERIFICATION: Pricing Coverage")
    print("=" * 80)

    try:
        # Count total active models
        total_models = (
            client.table("models")
            .select("id", count="exact")
            .eq("is_active", True)
            .execute()
        )

        total_count = total_models.count

        # Count models with pricing
        models_with_pricing = (
            client.table("model_pricing")
            .select("model_id", count="exact")
            .execute()
        )

        with_pricing = models_with_pricing.count

        coverage_pct = (with_pricing / total_count * 100) if total_count > 0 else 0

        print(f"\n  Total active models:        {total_count}")
        print(f"  Models with pricing:        {with_pricing}")
        print(f"  Models without pricing:     {total_count - with_pricing}")
        print(f"  Coverage:                   {coverage_pct:.1f}%")

        if coverage_pct < 90:
            print("\n  ⚠️  WARNING: Pricing coverage below 90%")
            print("     Consider running Phase 2.5 to enable automated sync")
        elif coverage_pct >= 90:
            print("\n  ✅ Excellent coverage (>90%)!")

        return {
            "total_models": total_count,
            "with_pricing": with_pricing,
            "coverage_percent": coverage_pct
        }

    except Exception as e:
        print(f"\n  ❌ Verification failed: {e}")
        return {"error": str(e)}


def main():
    """Run the seeding process."""
    parser = argparse.ArgumentParser(
        description="Seed model_pricing table from all pricing sources"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run (no database writes)"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute seeding (write to database)"
    )
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        print("❌ Error: Must specify --dry-run or --execute")
        parser.print_help()
        return 1

    dry_run = args.dry_run

    print("\n" + "🌱" * 40)
    print("Phase 1: Data Seeding - Populate model_pricing Table")
    print("🌱" * 40)
    print(f"\nMODE: {'DRY RUN (no database writes)' if dry_run else 'EXECUTE (writing to database)'}")

    # Get database client
    try:
        print("\n🔌 Connecting to database...")
        client = get_supabase_client()
        print("✅ Database connected")
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return 1

    # Seed from all sources
    all_stats = {}

    try:
        all_stats["manual_pricing_json"] = seed_from_manual_pricing_json(client, dry_run)
        all_stats["google_config"] = seed_from_google_config(client, dry_run)
        # all_stats["hardcoded"] = seed_from_hardcoded_sources(client, dry_run)  # TODO: Phase 1

        # Verify coverage
        coverage = verify_pricing_coverage(client)
        all_stats["coverage"] = coverage

    except Exception as e:
        print(f"\n❌ Seeding failed: {e}")
        import traceback
        traceback.print_exc()
        return 1

    # Overall summary
    print("\n" + "=" * 80)
    print("OVERALL SUMMARY")
    print("=" * 80)

    total_inserted = (
        all_stats.get("manual_pricing_json", {}).get("inserted", 0) +
        all_stats.get("google_config", {}).get("inserted", 0)
    )
    total_errors = (
        all_stats.get("manual_pricing_json", {}).get("errors", 0) +
        all_stats.get("google_config", {}).get("errors", 0)
    )

    print(f"\n  Mode:                       {'DRY RUN' if dry_run else 'EXECUTED'}")
    print(f"  Total models inserted:      {total_inserted}")
    print(f"  Total errors:               {total_errors}")
    print(f"  Pricing coverage:           {coverage.get('coverage_percent', 0):.1f}%")

    if dry_run:
        print("\n💡 This was a dry run. Use --execute to write to database.")
        print("   Command: python scripts/seed_model_pricing.py --execute")
    else:
        print("\n✅ Database seeding complete!")
        print("\n📊 Next steps:")
        print("   1. Verify pricing in database: PYTHONPATH=. python scripts/test_phase0_pricing_fix.py")
        print("   2. Test chat completion with database pricing")
        print("   3. Proceed to Phase 2 (Service Layer Migration)")

    return 0 if total_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
