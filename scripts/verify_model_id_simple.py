#!/usr/bin/env python3
"""
Simple Verification Script: Check if model_id can be safely removed

This is a simplified version that directly uses the Supabase client
without complex initialization logic.

Usage:
    SUPABASE_URL='https://...' SUPABASE_KEY='...' python3 scripts/verify_model_id_simple.py
"""

import os
import sys
from collections import defaultdict
from typing import Dict, List

from supabase import create_client, Client


def slugify(text: str) -> str:
    """Convert text to slug format (lowercase, spaces to hyphens)"""
    if not text:
        return ""
    return text.lower().replace(" ", "-").replace("_", "-")


def main():
    print("=" * 80)
    print("VERIFICATION: Can model_id column be safely removed?")
    print("=" * 80)
    print()

    # Get credentials from environment
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")

    if not url or not key:
        print("❌ Error: SUPABASE_URL and SUPABASE_KEY environment variables required")
        print()
        print("Usage:")
        print("  export SUPABASE_URL='https://your-project.supabase.co'")
        print("  export SUPABASE_KEY='your-service-role-key'")
        print("  python3 scripts/verify_model_id_simple.py")
        sys.exit(1)

    try:
        # Create Supabase client
        print(f"📊 Connecting to {url}...")
        supabase: Client = create_client(url, key)

        # Fetch all models with provider info
        print("📊 Fetching all models from database...")
        response = supabase.table("models").select(
            "id, model_id, model_name, provider_model_id, providers(slug, name)"
        ).execute()

        models = response.data
        total_models = len(models)

        print(f"✅ Found {total_models:,} models\n")

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
            provider_slug = model.get("providers", {}).get("slug", "unknown") if model.get("providers") else "unknown"

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

        # Print results
        print("=" * 80)
        print("ANALYSIS RESULTS")
        print("=" * 80)
        print()

        exact_count = len(exact_match)
        slugified_count = len(slugified_match)
        different_count = len(different)

        print(f"Total models analyzed: {total_models:,}")
        print(f"  ✅ Exact match (model_id == model_name): {exact_count:,} ({exact_count/total_models*100:.1f}%)")
        print(f"  ⚠️  Slugified match (slugify(model_name) == model_id): {slugified_count:,} ({slugified_count/total_models*100:.1f}%)")
        print(f"  ❌ Different: {different_count:,} ({different_count/total_models*100:.1f}%)")
        print()

        # Show examples of different values
        if different:
            print("=" * 80)
            print("❌ DIFFERENT VALUES (model_id != model_name)")
            print("=" * 80)
            print("These models have genuinely different model_id and model_name:")
            for model in different[:10]:
                provider = model.get("providers", {}).get("slug", "unknown") if model.get("providers") else "unknown"
                print(f"  {provider:20}")
                print(f"    model_id:          '{model['model_id']}'")
                print(f"    model_name:        '{model['model_name']}'")
                print(f"    provider_model_id: '{model['provider_model_id']}'")
                print()
            if len(different) > 10:
                print(f"  ... and {len(different) - 10:,} more")
            print()

        # Multi-provider grouping analysis
        print("=" * 80)
        print("MULTI-PROVIDER GROUPING ANALYSIS")
        print("=" * 80)
        print()

        multi_provider_by_id = {k: v for k, v in models_by_canonical_id.items() if len(v) > 1}
        multi_provider_by_name = {k: v for k, v in models_by_canonical_name.items() if len(v) > 1}

        print(f"Models offered by multiple providers:")
        print(f"  Using model_id for grouping:   {len(multi_provider_by_id):,} unique models")
        print(f"  Using model_name for grouping: {len(multi_provider_by_name):,} unique models")
        print()

        if len(multi_provider_by_id) == len(multi_provider_by_name):
            print("✅ GROUPING IS EQUIVALENT: model_id and model_name group the same way")
            grouping_ok = True
        else:
            print("❌ GROUPING IS DIFFERENT: model_id and model_name group differently!")
            grouping_ok = False

        print()

        # Check for discrepancies
        discrepancies = []
        for model_id_val, models_with_id in multi_provider_by_id.items():
            model_names = set(m["model_name"] for m in models_with_id)
            if len(model_names) > 1:
                discrepancies.append({
                    "model_id": model_id_val,
                    "model_names": model_names,
                    "providers": [m.get("providers", {}).get("slug") if m.get("providers") else "unknown" for m in models_with_id]
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
            grouping_ok = False

        # Final recommendation
        print("=" * 80)
        print("RECOMMENDATION")
        print("=" * 80)
        print()

        if different_count == 0 and grouping_ok:
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
        elif different_count > 0:
            print("❌ NOT SAFE to remove model_id column")
            print()
            print(f"Reasons:")
            print(f"  1. {different_count:,} models ({different_count/total_models*100:.1f}%) have different model_id and model_name")
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

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
