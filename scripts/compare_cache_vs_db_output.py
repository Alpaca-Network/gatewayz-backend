#!/usr/bin/env python3
"""
Compare cache-based catalog output vs database-based catalog output.
Used to validate Phase 1 implementation - ensures DB-first gives identical results.

Usage:
    python scripts/compare_cache_vs_db_output.py
    python scripts/compare_cache_vs_db_output.py --provider openrouter
    python scripts/compare_cache_vs_db_output.py --limit 10
"""

import argparse
import json
import sys
from typing import Any

# Add src to path
sys.path.insert(0, "src")

from db.models_catalog_db import (
    get_all_models_for_catalog,
    get_models_by_gateway_for_catalog,
    transform_db_models_batch,
)
from services.models import get_cached_models


def normalize_model_for_comparison(model: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize a model for comparison by removing fields that may differ
    between cache and DB due to timing, metadata, etc.
    """
    # Create a copy
    normalized = model.copy()

    # Remove fields that are expected to differ
    fields_to_ignore = [
        "created_at",
        "updated_at",
        "last_health_check_at",
        "average_response_time_ms",  # May vary
        "health_status",  # May change between fetches
    ]

    for field in fields_to_ignore:
        normalized.pop(field, None)

    # Normalize pricing format (handle string vs number differences)
    if "pricing" in normalized and normalized["pricing"]:
        pricing = normalized["pricing"]
        if isinstance(pricing, dict):
            for key in ["prompt", "completion", "image", "request"]:
                if key in pricing and pricing[key] is not None:
                    # Convert to float for comparison
                    try:
                        pricing[key] = float(pricing[key])
                    except (ValueError, TypeError):
                        pass

    return normalized


def compare_models(
    cache_models: list[dict[str, Any]], db_models: list[dict[str, Any]]
) -> dict[str, Any]:
    """
    Compare two lists of models and return detailed comparison results.

    Returns:
        Dict with comparison statistics and differences
    """
    # Create ID-based lookups
    cache_by_id = {m.get("id"): normalize_model_for_comparison(m) for m in cache_models}
    db_by_id = {m.get("id"): normalize_model_for_comparison(m) for m in db_models}

    # Find differences
    cache_only_ids = set(cache_by_id.keys()) - set(db_by_id.keys())
    db_only_ids = set(db_by_id.keys()) - set(cache_by_id.keys())
    common_ids = set(cache_by_id.keys()) & set(db_by_id.keys())

    # Compare common models
    field_differences = []
    for model_id in common_ids:
        cache_model = cache_by_id[model_id]
        db_model = db_by_id[model_id]

        # Compare each field
        all_fields = set(cache_model.keys()) | set(db_model.keys())
        for field in all_fields:
            cache_value = cache_model.get(field)
            db_value = db_model.get(field)

            # Skip None vs missing field (equivalent)
            if cache_value is None and field not in db_model:
                continue
            if db_value is None and field not in cache_model:
                continue

            # Compare values
            if cache_value != db_value:
                field_differences.append(
                    {
                        "model_id": model_id,
                        "field": field,
                        "cache_value": cache_value,
                        "db_value": db_value,
                    }
                )

    return {
        "cache_count": len(cache_models),
        "db_count": len(db_models),
        "common_count": len(common_ids),
        "cache_only_count": len(cache_only_ids),
        "db_only_count": len(db_only_ids),
        "cache_only_ids": sorted(list(cache_only_ids))[:10],  # Show first 10
        "db_only_ids": sorted(list(db_only_ids))[:10],  # Show first 10
        "field_differences_count": len(field_differences),
        "field_differences": field_differences[:20],  # Show first 20
        "match_percentage": (
            len(common_ids) / max(len(cache_by_id), len(db_by_id)) * 100
            if cache_by_id or db_by_id
            else 0
        ),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Compare cache vs database catalog output"
    )
    parser.add_argument(
        "--provider",
        help="Specific provider to test (e.g., openrouter, anthropic)",
        default=None,
    )
    parser.add_argument(
        "--limit", type=int, help="Limit number of models to compare", default=None
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show detailed differences"
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output JSON file path",
        default=None,
    )

    args = parser.parse_args()

    print("=" * 80)
    print("Cache vs Database Catalog Output Comparison")
    print("=" * 80)
    print()

    # Fetch from cache (current behavior)
    print(f"📦 Fetching from cache (current behavior)...")
    if args.provider:
        cache_models = get_cached_models(args.provider)
        print(f"   Provider: {args.provider}")
    else:
        cache_models = get_cached_models("all")
        print(f"   Provider: all (aggregated)")

    if args.limit:
        cache_models = cache_models[: args.limit]

    print(f"   Found: {len(cache_models)} models from cache")
    print()

    # Fetch from database (new behavior)
    print(f"🗄️  Fetching from database (new behavior)...")
    if args.provider:
        db_models_raw = get_models_by_gateway_for_catalog(args.provider)
    else:
        db_models_raw = get_all_models_for_catalog()

    # Transform to API format
    db_models = transform_db_models_batch(db_models_raw)

    if args.limit:
        db_models = db_models[: args.limit]

    print(f"   Found: {len(db_models)} models from database")
    print()

    # Compare
    print("🔍 Comparing outputs...")
    comparison = compare_models(cache_models, db_models)

    # Display results
    print()
    print("=" * 80)
    print("COMPARISON RESULTS")
    print("=" * 80)
    print()
    print(f"Cache models:     {comparison['cache_count']}")
    print(f"Database models:  {comparison['db_count']}")
    print(f"Common models:    {comparison['common_count']}")
    print(
        f"Match percentage: {comparison['match_percentage']:.1f}%"
    )
    print()

    if comparison["cache_only_count"] > 0:
        print(f"⚠️  Models only in cache: {comparison['cache_only_count']}")
        if args.verbose:
            print(f"   IDs: {', '.join(comparison['cache_only_ids'][:5])}...")
        print()

    if comparison["db_only_count"] > 0:
        print(f"⚠️  Models only in database: {comparison['db_only_count']}")
        if args.verbose:
            print(f"   IDs: {', '.join(comparison['db_only_ids'][:5])}...")
        print()

    if comparison["field_differences_count"] > 0:
        print(f"⚠️  Field differences: {comparison['field_differences_count']}")
        if args.verbose:
            print()
            print("Sample differences:")
            for diff in comparison["field_differences"][:10]:
                print(f"   Model: {diff['model_id']}")
                print(f"   Field: {diff['field']}")
                print(f"   Cache: {diff['cache_value']}")
                print(f"   DB:    {diff['db_value']}")
                print()
    else:
        print("✅ No field differences found!")
        print()

    # Determine success
    success = (
        comparison["match_percentage"] > 95  # At least 95% match
        and comparison["field_differences_count"]
        < comparison["common_count"] * 0.01  # Less than 1% field diffs
    )

    if success:
        print("=" * 80)
        print("✅ SUCCESS: Database output matches cache output!")
        print("=" * 80)
    else:
        print("=" * 80)
        print("❌ DIFFERENCES FOUND: Review output above")
        print("=" * 80)
        print()
        print("Possible reasons:")
        print("- Database not fully synced with providers")
        print("- Schema differences between cache and DB")
        print("- Transformation errors")
        print()
        print("To fix:")
        print("1. Run: POST /admin/model-sync/all")
        print("2. Wait for sync to complete")
        print("3. Re-run this script")

    # Save to JSON if requested
    if args.output:
        with open(args.output, "w") as f:
            json.dump(
                {
                    "comparison": comparison,
                    "success": success,
                    "provider": args.provider or "all",
                    "limit": args.limit,
                },
                f,
                indent=2,
            )
        print()
        print(f"📄 Results saved to: {args.output}")

    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
