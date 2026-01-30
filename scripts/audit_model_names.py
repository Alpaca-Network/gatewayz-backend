#!/usr/bin/env python3
"""
Audit script to analyze model_name formatting across all providers
Identifies patterns like "Company : Model (Type)" and provides statistics
"""
import os
import sys
import re
from pathlib import Path
from collections import defaultdict

# Add src to path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from src.config.supabase_config import get_supabase_client


def analyze_model_name_pattern(name: str) -> dict:
    """Analyze a model name and extract components"""

    # Pattern: "Company : Model Name (Type)"
    pattern1 = r'^([^:]+)\s*:\s*(.+?)(?:\s*\(([^)]+)\))?$'
    # Pattern: "Company/Model"
    pattern2 = r'^([^/]+)/(.+)$'
    # Pattern: Just model name
    pattern3 = r'^[^:/]+$'

    match1 = re.match(pattern1, name)
    if match1:
        company, model, type_info = match1.groups()
        return {
            "pattern": "company:model(type)",
            "company": company.strip(),
            "model": model.strip(),
            "type": type_info.strip() if type_info else None,
            "original": name
        }

    match2 = re.match(pattern2, name)
    if match2:
        company, model = match2.groups()
        return {
            "pattern": "company/model",
            "company": company.strip(),
            "model": model.strip(),
            "type": None,
            "original": name
        }

    return {
        "pattern": "simple",
        "company": None,
        "model": name.strip(),
        "type": None,
        "original": name
    }


def main():
    """Audit model names across all providers"""
    supabase = get_supabase_client()

    print("=" * 80)
    print("MODEL NAME FORMATTING AUDIT")
    print("=" * 80)
    print()

    # Get all models with provider info
    print("Fetching all models from database...")
    all_models = []
    page_size = 1000
    offset = 0

    while True:
        result = supabase.table('models').select(
            'id,model_name,model_id,provider_id,providers(name,slug)'
        ).range(offset, offset + page_size - 1).execute()

        if not result.data:
            break
        all_models.extend(result.data)
        if len(result.data) < page_size:
            break
        offset += page_size

    print(f"Total models: {len(all_models)}")
    print()

    # Analyze patterns
    pattern_counts = defaultdict(int)
    provider_patterns = defaultdict(lambda: defaultdict(int))
    samples = defaultdict(list)

    for model in all_models:
        name = model['model_name']
        provider = model.get('providers', {})
        provider_name = provider.get('name', 'Unknown') if provider else 'Unknown'
        provider_slug = provider.get('slug', 'unknown') if provider else 'unknown'

        analysis = analyze_model_name_pattern(name)
        pattern = analysis['pattern']

        pattern_counts[pattern] += 1
        provider_patterns[provider_slug][pattern] += 1

        # Store samples (max 3 per pattern)
        if len(samples[pattern]) < 3:
            samples[pattern].append({
                'name': name,
                'provider': provider_name,
                'analysis': analysis
            })

    # Report findings
    print("=" * 80)
    print("PATTERN DISTRIBUTION")
    print("=" * 80)
    print()

    for pattern, count in sorted(pattern_counts.items(), key=lambda x: x[1], reverse=True):
        percentage = (count / len(all_models)) * 100
        print(f"{pattern:30} {count:6} models ({percentage:5.1f}%)")

    print()
    print("=" * 80)
    print("PATTERN SAMPLES")
    print("=" * 80)
    print()

    for pattern in sorted(pattern_counts.keys()):
        print(f"\n{pattern.upper()}:")
        print("-" * 80)
        for sample in samples[pattern]:
            print(f"  Provider: {sample['provider']}")
            print(f"  Original: {sample['name']}")
            if sample['analysis']['company']:
                print(f"  Company:  {sample['analysis']['company']}")
            print(f"  Model:    {sample['analysis']['model']}")
            if sample['analysis']['type']:
                print(f"  Type:     {sample['analysis']['type']}")
            print()

    # Provider-specific analysis
    print("=" * 80)
    print("PATTERNS BY PROVIDER")
    print("=" * 80)
    print()

    for provider_slug in sorted(provider_patterns.keys()):
        patterns = provider_patterns[provider_slug]
        total = sum(patterns.values())
        print(f"\n{provider_slug} ({total} models):")
        for pattern, count in sorted(patterns.items(), key=lambda x: x[1], reverse=True):
            percentage = (count / total) * 100
            print(f"  {pattern:30} {count:6} ({percentage:5.1f}%)")

    # Generate recommendations
    print()
    print("=" * 80)
    print("RECOMMENDATIONS")
    print("=" * 80)
    print()

    if pattern_counts.get("company:model(type)", 0) > 0:
        print("1. ✗ Found models with 'Company : Model (Type)' format")
        print("   Recommendation: Extract only the model portion")
        print()

    if pattern_counts.get("company/model", 0) > 0:
        print("2. ✗ Found models with 'Company/Model' format")
        print("   Recommendation: Consider if company prefix should be removed")
        print()

    print("3. ℹ Suggested standardization:")
    print("   - model_name: Only the model name (e.g., 'Llama 3.3 70B')")
    print("   - model_id: Full identifier with provider (e.g., 'openai/gpt-4')")
    print("   - Store company/type info in metadata if needed")
    print()

    # Export detailed report
    report_path = repo_root / "docs" / "MODEL_NAME_AUDIT.md"
    print(f"📄 Detailed report will be saved to: {report_path}")

    with open(report_path, "w") as f:
        f.write("# Model Name Formatting Audit Report\n\n")
        f.write(f"**Generated:** {__import__('datetime').datetime.now().isoformat()}\n\n")
        f.write(f"**Total Models Analyzed:** {len(all_models)}\n\n")

        f.write("## Pattern Distribution\n\n")
        f.write("| Pattern | Count | Percentage |\n")
        f.write("|---------|-------|------------|\n")
        for pattern, count in sorted(pattern_counts.items(), key=lambda x: x[1], reverse=True):
            percentage = (count / len(all_models)) * 100
            f.write(f"| {pattern} | {count} | {percentage:.1f}% |\n")

        f.write("\n## Samples by Pattern\n\n")
        for pattern in sorted(pattern_counts.keys()):
            f.write(f"\n### {pattern}\n\n")
            for sample in samples[pattern][:5]:  # Save more samples to file
                f.write(f"- **{sample['name']}** (Provider: {sample['provider']})\n")
                if sample['analysis']['company']:
                    f.write(f"  - Company: {sample['analysis']['company']}\n")
                f.write(f"  - Model: {sample['analysis']['model']}\n")
                if sample['analysis']['type']:
                    f.write(f"  - Type: {sample['analysis']['type']}\n")

        f.write("\n## Recommendations\n\n")
        f.write("1. Standardize model_name to contain only the model name\n")
        f.write("2. Store provider/company prefix in model_id or metadata\n")
        f.write("3. Move type information (e.g., 'Chat', 'Instruct') to metadata\n")
        f.write("4. Update all fetch functions to normalize names consistently\n\n")

    print(f"✅ Report saved to: {report_path}")


if __name__ == "__main__":
    main()
