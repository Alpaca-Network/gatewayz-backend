#!/usr/bin/env python3
"""
Extract model pricing warnings from Railway deployment logs
"""
import re
from collections import defaultdict

def parse_model_warnings(log_text):
    """
    Parse model warnings from deployment logs
    Format: "⚠️ Model provider/model-name (normalized: provider/normalized-name) not found in catalog, using default pricing"
    """
    warnings = defaultdict(list)

    # Pattern to match model warnings
    pattern = r'⚠️\s+Model\s+([^\s]+)\s+\(normalized:\s+([^)]+)\)\s+not found in catalog,\s+using default pricing'

    matches = re.findall(pattern, log_text)

    for original_model, normalized_model in matches:
        # Extract provider from model name
        if '/' in original_model:
            provider = original_model.split('/')[0]
        else:
            provider = 'unknown'

        warnings[provider].append({
            'original': original_model,
            'normalized': normalized_model,
            'pricing': 'default'
        })

    return warnings

def format_warnings_report(warnings):
    """Format warnings as a markdown report"""
    if not warnings:
        return "## Models Not Found in Catalog\n\nNo models with missing pricing detected in recent deployment logs."

    report = "## Models Not Found in Catalog\n\n"
    report += f"**Total Providers with Warnings**: {len(warnings)}\n"
    report += f"**Total Models with Missing Pricing**: {sum(len(models) for models in warnings.values())}\n\n"

    report += "### By Provider\n\n"

    for provider in sorted(warnings.keys()):
        models = warnings[provider]
        report += f"#### {provider.upper()} ({len(models)} models)\n\n"
        report += "| Model ID | Normalized ID | Pricing |\n"
        report += "|----------|---------------|--------|\n"

        for model in models:
            report += f"| `{model['original']}` | `{model['normalized']}` | **{model['pricing']}** |\n"

        report += "\n"

    return report

if __name__ == "__main__":
    # This script would need the actual log text
    # For now, return a template
    print("Script ready to parse Railway logs")
    print("\nUsage: Pass Railway deployment logs as input to extract model warnings")
