#!/usr/bin/env python3
"""
Verification Script: Check if model_id can be safely removed

This script analyzes the models table to determine if model_id is redundant
with model_name, making it safe to remove the model_id column.

Usage:
    python3 scripts/verify_model_id_duplication.py

Environment Variables Required:
    SUPABASE_URL - Your Supabase project URL
    SUPABASE_KEY - Your Supabase service role key
"""

import os
import sys
from collections import defaultdict
from typing import Dict, List, Tuple

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.config.supabase_config import get_supabase_client


def slugify(text: str) -> str:
    """Convert text to slug format (lowercase, spaces to hyphens)"""
    if not text:
        return ""
    return text.lower().replace(" ", "-").replace("_", "-")


def analyze_model_id_vs_model_name() -> Dict:
    """
    Analyze if model_id and model_name are duplicates.

    Returns:
        Dictionary with analysis results
    """
    print("=" * 80)
    print("VERIFICATION: Can model_id column be safely removed?")
    print("=" * 80)
    print()

    try:
        supabase = get_supabase_client()

        # Fetch all models with provider info
        print("📊 Fetching all models from database...")
        response = supabase.table("models").select(
            "id, model_id, model_name, provider_model_id, providers(slug, name)"
        ).execute()

        models = response.data
        total_models = len(models)

        print(f"✅ Found {total_models} models\n")

        # Analysis categories
        exact_match = []  # model_id == model_name
        slugified_match = []  # slugify(model_name) == model_id
        different = []  # model_id != model_name (even after slugification)

        # Track multi-provider models
        models_by_canonical_id = defaultdict(list)
        models_by_canonical_name = defaultdict(list)

        # Analyze each model
        for model in models:
            model_id = model.get("model_id", "")
            model_name = model.get("model_name", "")
            provider_slug = model.get("providers", {}).get("slug", "unknown")

            # Check if exact match
            if model_id == model_name:
                exact_match.append(model)
            # Check if slugified match
            elif slugify(model_name) == model_id:
                slugified_match.append(model)
            else:
                different.append(model)

            # Track for multi-provider analysis
            models_by_canonical_id[model_id].append(model)
            models_by_canonical_name[model_name].append(model)

        # Results
        results = {
            "total_models": total_models,
            "exact_match_count": len(exact_match),
            "slugified_match_count": len(slugified_match),
            "different_count": len(different),
            "exact_match": exact_match,
            "slugified_match": slugified_match,
            "different": different,
            "models_by_canonical_id": models_by_canonical_id,
            "models_by_canonical_name": models_by_canonical_name,
        }

        return results

    except Exception as e:
        print(f"❌ Error fetching models: {e}")
        sys.exit(1)


def print_results(results: Dict):
    """Print analysis results"""

    total = results["total_models"]
    exact = results["exact_match_count"]
    slugified = results["slugified_match_count"]
    different = results["different_count"]

    print("=" * 80)
    print("ANALYSIS RESULTS")
    print("=" * 80)
    print()

    # Summary
    print(f"Total models analyzed: {total}")
    print(f"  ✅ Exact match (model_id == model_name): {exact} ({exact/total*100:.1f}%)")
    print(f"  ⚠️  Slugified match (slugify(model_name) == model_id): {slugified} ({slugified/total*100:.1f}%)")
    print(f"  ❌ Different: {different} ({different/total*100:.1f}%)")
    print()

    # Show examples of each category
    if results["exact_match"]:
        print("=" * 80)
        print("✅ EXACT MATCHES (model_id == model_name)")
        print("=" * 80)
        print("First 5 examples:")
        for model in results["exact_match"][:5]:
            provider = model.get("providers", {}).get("slug", "unknown")
            print(f"  {provider:20} model_id='{model['model_id']}' == model_name='{model['model_name']}'")
        if len(results["exact_match"]) > 5:
            print(f"  ... and {len(results['exact_match']) - 5} more")
        print()

    if results["slugified_match"]:
        print("=" * 80)
        print("⚠️  SLUGIFIED MATCHES (slugify(model_name) == model_id)")
        print("=" * 80)
        print("These are functionally equivalent but stored differently:")
        for model in results["slugified_match"][:10]:
            provider = model.get("providers", {}).get("slug", "unknown")
            print(f"  {provider:20} model_id='{model['model_id']}' vs model_name='{model['model_name']}'")
        if len(results["slugified_match"]) > 10:
            print(f"  ... and {len(results['slugified_match']) - 10} more")
        print()

    if results["different"]:
        print("=" * 80)
        print("❌ DIFFERENT VALUES (model_id != model_name)")
        print("=" * 80)
        print("These models have genuinely different model_id and model_name:")
        for model in results["different"][:10]:
            provider = model.get("providers", {}).get("slug", "unknown")
            print(f"  {provider:20}")
            print(f"    model_id:         '{model['model_id']}'")
            print(f"    model_name:       '{model['model_name']}'")
            print(f"    provider_model_id: '{model['provider_model_id']}'")
            print()
        if len(results["different"]) > 10:
            print(f"  ... and {len(results['different']) - 10} more")
        print()


def check_multi_provider_grouping(results: Dict):
    """Check if multi-provider grouping works with model_name"""

    print("=" * 80)
    print("MULTI-PROVIDER GROUPING ANALYSIS")
    print("=" * 80)
    print()

    by_id = results["models_by_canonical_id"]
    by_name = results["models_by_canonical_name"]

    # Find models offered by multiple providers
    multi_provider_by_id = {k: v for k, v in by_id.items() if len(v) > 1}
    multi_provider_by_name = {k: v for k, v in by_name.items() if len(v) > 1}

    print(f"Models offered by multiple providers:")
    print(f"  Using model_id for grouping:   {len(multi_provider_by_id)} unique models")
    print(f"  Using model_name for grouping: {len(multi_provider_by_name)} unique models")
    print()

    # Check if grouping is equivalent
    if len(multi_provider_by_id) == len(multi_provider_by_name):
        print("✅ GROUPING IS EQUIVALENT: model_id and model_name group the same way")
    else:
        print("❌ GROUPING IS DIFFERENT: model_id and model_name group differently!")
        print()
        print("This means model_id and model_name are NOT interchangeable for multi-provider queries!")

    print()

    # Show examples
    print("Examples of multi-provider models:")
    print()

    for i, (model_id_val, models) in enumerate(list(multi_provider_by_id.items())[:5]):
        print(f"{i+1}. model_id='{model_id_val}' ({len(models)} providers):")
        for model in models:
            provider = model.get("providers", {}).get("slug", "unknown")
            print(f"     - {provider:20} model_name='{model['model_name']}'")
        print()

    if len(multi_provider_by_id) > 5:
        print(f"   ... and {len(multi_provider_by_id) - 5} more multi-provider models")

    print()

    # Check for grouping discrepancies
    discrepancies = []

    for model_id_val, models_with_id in multi_provider_by_id.items():
        # Get all model_name values for this model_id
        model_names = set(m["model_name"] for m in models_with_id)

        if len(model_names) > 1:
            # Different model_names for the same model_id
            discrepancies.append({
                "model_id": model_id_val,
                "model_names": model_names,
                "providers": [m.get("providers", {}).get("slug") for m in models_with_id]
            })

    if discrepancies:
        print("=" * 80)
        print("⚠️  CRITICAL: MULTI-PROVIDER GROUPING DISCREPANCIES FOUND")
        print("=" * 80)
        print()
        print("These models have the SAME model_id but DIFFERENT model_names:")
        print("If you remove model_id, these models won't group together anymore!")
        print()

        for disc in discrepancies[:10]:
            print(f"model_id: '{disc['model_id']}'")
            print(f"  model_names: {disc['model_names']}")
            print(f"  providers: {disc['providers']}")
            print()

        return False
    else:
        print("✅ No multi-provider grouping discrepancies found")
        return True


def print_recommendation(results: Dict, grouping_ok: bool):
    """Print final recommendation"""

    total = results["total_models"]
    different = results["different_count"]

    print("=" * 80)
    print("RECOMMENDATION")
    print("=" * 80)
    print()

    if different == 0 and grouping_ok:
        print("✅ SAFE TO REMOVE model_id column")
        print()
        print("Reasons:")
        print("  1. model_id and model_name contain functionally equivalent data")
        print("  2. Multi-provider grouping works the same with both fields")
        print("  3. All queries can safely use model_name instead of model_id")
        print()
        print("Next steps:")
        print("  1. Update failover queries to use model_name instead of model_id")
        print("  2. Update analytics to GROUP BY model_name instead of model_id")
        print("  3. Create migration to drop model_id column")
        print()
    elif different > 0:
        print("❌ NOT SAFE to remove model_id column")
        print()
        print(f"Reasons:")
        print(f"  1. {different} models ({different/total*100:.1f}%) have different model_id and model_name")
        print(f"  2. Removing model_id would lose important canonical grouping information")
        print()
        print("Options:")
        print("  A. Normalize model_name to match model_id for all models first")
        print("  B. Keep both fields and use model_id for canonical grouping")
        print("  C. Investigate why these models have different values")
        print()
    elif not grouping_ok:
        print("❌ NOT SAFE to remove model_id column")
        print()
        print("Reasons:")
        print("  1. Multi-provider grouping differs between model_id and model_name")
        print("  2. Removing model_id would break failover queries")
        print()
        print("Fix:")
        print("  Standardize model_name values to match model_id for multi-provider models")
        print()


def main():
    """Main execution"""

    # Check environment variables
    if not os.getenv("SUPABASE_URL") or not os.getenv("SUPABASE_KEY"):
        print("❌ Error: SUPABASE_URL and SUPABASE_KEY environment variables required")
        print()
        print("Usage:")
        print("  export SUPABASE_URL='your-supabase-url'")
        print("  export SUPABASE_KEY='your-service-role-key'")
        print("  python3 scripts/verify_model_id_duplication.py")
        sys.exit(1)

    # Run analysis
    results = analyze_model_id_vs_model_name()

    # Print results
    print_results(results)

    # Check multi-provider grouping
    grouping_ok = check_multi_provider_grouping(results)

    # Print recommendation
    print_recommendation(results, grouping_ok)


if __name__ == "__main__":
    main()
