#!/usr/bin/env python3
"""
Audit script to identify malformed model names in the database.
Checks for model names containing colons (:) or parentheses () which indicate
compound format instead of clean model names.
"""
import os
import sys
from typing import Dict, List, Tuple

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config.supabase_config import get_supabase_client


def audit_malformed_model_names() -> Tuple[List[Dict], Dict[str, int]]:
    """
    Audit the database for malformed model names.

    Returns:
        Tuple of (malformed_models, stats)
        - malformed_models: List of model records with malformed names
        - stats: Dictionary with statistics about malformed names
    """
    supabase = get_supabase_client()

    print("🔍 Auditing model names for malformed formats...\n")

    # Fetch all models
    response = supabase.table("models").select(
        "model_id, model_name, provider_id, providers(name)"
    ).execute()

    models = response.data
    print(f"📊 Total models in database: {len(models)}\n")

    # Identify malformed names
    malformed_models = []
    malformed_by_provider = {}
    malformed_types = {
        "contains_colon": 0,
        "contains_parentheses": 0,
        "both": 0
    }

    for model in models:
        model_name = model.get("model_name", "")
        if not model_name:
            continue

        has_colon = ":" in model_name
        has_parentheses = "(" in model_name and ")" in model_name

        if has_colon or has_parentheses:
            malformed_models.append(model)

            # Track by provider
            provider_name = model.get("providers", {}).get("name", "Unknown")
            malformed_by_provider[provider_name] = malformed_by_provider.get(provider_name, 0) + 1

            # Track by type
            if has_colon and has_parentheses:
                malformed_types["both"] += 1
            elif has_colon:
                malformed_types["contains_colon"] += 1
            elif has_parentheses:
                malformed_types["contains_parentheses"] += 1

    # Print results
    print("=" * 80)
    print("AUDIT RESULTS")
    print("=" * 80)
    print(f"\n✅ Clean model names: {len(models) - len(malformed_models)}")
    print(f"❌ Malformed model names: {len(malformed_models)}")

    if malformed_models:
        print(f"\n📈 Malformed rate: {len(malformed_models) / len(models) * 100:.2f}%")

        print("\n" + "=" * 80)
        print("MALFORMED TYPES")
        print("=" * 80)
        print(f"Contains colon (:): {malformed_types['contains_colon']}")
        print(f"Contains parentheses (()): {malformed_types['contains_parentheses']}")
        print(f"Contains both: {malformed_types['both']}")

        print("\n" + "=" * 80)
        print("MALFORMED BY PROVIDER")
        print("=" * 80)
        for provider, count in sorted(malformed_by_provider.items(), key=lambda x: x[1], reverse=True):
            print(f"{provider}: {count}")

        print("\n" + "=" * 80)
        print("EXAMPLES OF MALFORMED NAMES (first 20)")
        print("=" * 80)
        for i, model in enumerate(malformed_models[:20]):
            provider_name = model.get("providers", {}).get("name", "Unknown")
            print(f"\n{i + 1}. Provider: {provider_name}")
            print(f"   Model ID: {model['model_id']}")
            print(f"   Model Name: {model['model_name']}")

            # Show what's wrong
            issues = []
            if ":" in model["model_name"]:
                issues.append("contains ':'")
            if "(" in model["model_name"] and ")" in model["model_name"]:
                issues.append("contains '()'")
            print(f"   Issues: {', '.join(issues)}")

        if len(malformed_models) > 20:
            print(f"\n... and {len(malformed_models) - 20} more malformed names")
    else:
        print("\n✨ All model names are clean! No malformed names found.")

    print("\n" + "=" * 80)

    stats = {
        "total_models": len(models),
        "clean_models": len(models) - len(malformed_models),
        "malformed_models": len(malformed_models),
        "malformed_by_provider": malformed_by_provider,
        "malformed_types": malformed_types
    }

    return malformed_models, stats


def main():
    """Main execution function."""
    try:
        malformed_models, stats = audit_malformed_model_names()

        # Save detailed report
        report_file = "docs/MODEL_NAME_AUDIT.md"
        with open(report_file, "w") as f:
            f.write("# Model Name Audit Report\n\n")
            f.write(f"**Generated:** {__import__('datetime').datetime.now().isoformat()}\n\n")

            f.write("## Summary\n\n")
            f.write(f"- Total models: {stats['total_models']}\n")
            f.write(f"- Clean model names: {stats['clean_models']}\n")
            f.write(f"- Malformed model names: {stats['malformed_models']}\n")

            if stats['malformed_models'] > 0:
                f.write(f"- Malformed rate: {stats['malformed_models'] / stats['total_models'] * 100:.2f}%\n\n")

                f.write("## Malformed Types\n\n")
                f.write(f"- Contains colon (:): {stats['malformed_types']['contains_colon']}\n")
                f.write(f"- Contains parentheses (()): {stats['malformed_types']['contains_parentheses']}\n")
                f.write(f"- Contains both: {stats['malformed_types']['both']}\n\n")

                f.write("## Malformed by Provider\n\n")
                f.write("| Provider | Count |\n")
                f.write("|----------|-------|\n")
                for provider, count in sorted(stats['malformed_by_provider'].items(), key=lambda x: x[1], reverse=True):
                    f.write(f"| {provider} | {count} |\n")

                f.write("\n## Examples\n\n")
                for i, model in enumerate(malformed_models[:50]):
                    provider_name = model.get("providers", {}).get("name", "Unknown")
                    f.write(f"### {i + 1}. {provider_name}\n\n")
                    f.write(f"- **Model ID:** `{model['model_id']}`\n")
                    f.write(f"- **Model Name:** `{model['model_name']}`\n")

                    issues = []
                    if ":" in model["model_name"]:
                        issues.append("contains ':'")
                    if "(" in model["model_name"] and ")" in model["model_name"]:
                        issues.append("contains '()'")
                    f.write(f"- **Issues:** {', '.join(issues)}\n\n")

                if len(malformed_models) > 50:
                    f.write(f"\n... and {len(malformed_models) - 50} more malformed names\n")
            else:
                f.write("\n✨ All model names are clean!\n")

            f.write("\n## Recommendations\n\n")
            if stats['malformed_models'] > 0:
                f.write("1. Run cleanup script to fix malformed names\n")
                f.write("2. Add validation to prevent future malformed names\n")
                f.write("3. Consider full flush and resync if many models affected\n")
            else:
                f.write("1. Add validation to prevent future malformed names\n")
                f.write("2. Ensure all provider fetch functions generate clean names\n")

        print(f"\n📄 Detailed report saved to: {report_file}")

        return 0 if stats['malformed_models'] == 0 else 1

    except Exception as e:
        print(f"\n❌ Error during audit: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
