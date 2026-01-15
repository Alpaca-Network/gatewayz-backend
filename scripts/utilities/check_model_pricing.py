#!/usr/bin/env python3
"""
Check Model Pricing in Database

This script queries the pricing catalog database to verify if specific models
have pricing configuration, and reports which models are missing pricing data.

Usage:
    python scripts/utilities/check_model_pricing.py [--models MODEL1 MODEL2 ...]
    python scripts/utilities/check_model_pricing.py --from-report REPORT_FILE
"""

import os
import sys
import argparse
import asyncio
from pathlib import Path
from datetime import datetime

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Import configuration and database modules
try:
    from src.config.supabase_config import get_supabase_client
    from src.services.pricing_lookup import get_model_pricing
    from src.db.models_catalog_db import get_model_by_id
except ImportError as e:
    print(f"Error importing modules: {e}")
    print("Make sure to run this script from the project root")
    sys.exit(1)


async def check_single_model(model_id):
    """
    Check if a model has pricing configuration.

    Args:
        model_id (str): Model ID to check

    Returns:
        dict: Result with model_id, has_pricing, pricing_data
    """
    result = {
        'model_id': model_id,
        'has_pricing': False,
        'pricing_data': None,
        'in_catalog': False,
        'error': None
    }

    try:
        # Check if model exists in catalog
        supabase = get_supabase_client()

        # Query models_catalog table
        response = supabase.table('models_catalog').select('*').eq('id', model_id).execute()

        if response.data and len(response.data) > 0:
            result['in_catalog'] = True
            catalog_data = response.data[0]

            # Check if pricing fields are populated
            if catalog_data.get('input_cost_per_token') is not None or \
               catalog_data.get('output_cost_per_token') is not None:
                result['has_pricing'] = True
                result['pricing_data'] = {
                    'input_cost': catalog_data.get('input_cost_per_token'),
                    'output_cost': catalog_data.get('output_cost_per_token'),
                    'currency': catalog_data.get('pricing_currency', 'USD'),
                    'updated_at': catalog_data.get('updated_at')
                }

        # Try alternative lookups with normalization
        if not result['in_catalog']:
            # Try with various normalizations
            normalized_ids = [
                model_id,
                model_id.replace('_', '-'),
                model_id.replace('-', '_'),
            ]

            for norm_id in normalized_ids:
                if norm_id != model_id:
                    response = supabase.table('models_catalog').select('*').eq('id', norm_id).execute()
                    if response.data and len(response.data) > 0:
                        result['in_catalog'] = True
                        result['model_id'] = norm_id  # Update to found ID
                        catalog_data = response.data[0]

                        if catalog_data.get('input_cost_per_token') is not None:
                            result['has_pricing'] = True
                            result['pricing_data'] = {
                                'input_cost': catalog_data.get('input_cost_per_token'),
                                'output_cost': catalog_data.get('output_cost_per_token'),
                                'currency': catalog_data.get('pricing_currency', 'USD'),
                                'updated_at': catalog_data.get('updated_at')
                            }
                        break

    except Exception as e:
        result['error'] = str(e)

    return result


async def check_multiple_models(model_ids):
    """
    Check pricing for multiple models.

    Args:
        model_ids (list): List of model IDs to check

    Returns:
        list: List of results for each model
    """
    tasks = [check_single_model(model_id) for model_id in model_ids]
    results = await asyncio.gather(*tasks)
    return results


def format_results(results):
    """
    Format check results as a readable report.

    Args:
        results (list): List of check results

    Returns:
        str: Formatted report
    """
    report = f"""# Model Pricing Check Report

**Generated**: {datetime.now().isoformat()}
**Models Checked**: {len(results)}

## Summary

"""

    # Count statistics
    in_catalog = sum(1 for r in results if r['in_catalog'])
    has_pricing = sum(1 for r in results if r['has_pricing'])
    missing_pricing = sum(1 for r in results if r['in_catalog'] and not r['has_pricing'])
    not_in_catalog = sum(1 for r in results if not r['in_catalog'])
    errors = sum(1 for r in results if r['error'])

    report += f"- ✅ **Models in Catalog**: {in_catalog}/{len(results)}\n"
    report += f"- 💰 **Models with Pricing**: {has_pricing}/{len(results)}\n"
    report += f"- ⚠️  **Missing Pricing**: {missing_pricing}\n"
    report += f"- ❌ **Not in Catalog**: {not_in_catalog}\n"

    if errors > 0:
        report += f"- 🔴 **Errors**: {errors}\n"

    report += "\n---\n\n"

    # Models with pricing
    if has_pricing > 0:
        report += "## ✅ Models with Pricing Configuration\n\n"
        report += "| Model ID | Input Cost | Output Cost | Currency | Last Updated |\n"
        report += "|----------|------------|-------------|----------|-------------|\n"

        for result in results:
            if result['has_pricing'] and result['pricing_data']:
                pricing = result['pricing_data']
                report += f"| `{result['model_id']}` | {pricing.get('input_cost', 'N/A')} | {pricing.get('output_cost', 'N/A')} | {pricing.get('currency', 'N/A')} | {pricing.get('updated_at', 'N/A')} |\n"

        report += "\n"

    # Models missing pricing
    if missing_pricing > 0:
        report += "## ⚠️  Models in Catalog but Missing Pricing\n\n"
        report += "These models exist in the catalog but lack pricing configuration:\n\n"

        for result in results:
            if result['in_catalog'] and not result['has_pricing']:
                report += f"- `{result['model_id']}`\n"

        report += "\n**Action Required**: Add pricing data for these models\n\n"

    # Models not in catalog
    if not_in_catalog > 0:
        report += "## ❌ Models Not Found in Catalog\n\n"
        report += "These models are not in the models_catalog table:\n\n"

        for result in results:
            if not result['in_catalog']:
                report += f"- `{result['model_id']}`\n"

        report += "\n**Action Required**: Add these models to the catalog with pricing\n\n"

    # Errors
    if errors > 0:
        report += "## 🔴 Errors During Check\n\n"

        for result in results:
            if result['error']:
                report += f"- `{result['model_id']}`: {result['error']}\n"

        report += "\n"

    report += "---\n\n*Generated by `check_model_pricing.py`*\n"

    return report


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Check model pricing configuration in the database'
    )
    parser.add_argument(
        '--models',
        nargs='+',
        help='Model IDs to check'
    )
    parser.add_argument(
        '--from-warnings',
        action='store_true',
        help='Check models from the latest pricing warnings report'
    )
    parser.add_argument(
        '--output',
        type=str,
        help='Output file for the report (optional)'
    )

    args = parser.parse_args()

    # Determine which models to check
    models_to_check = []

    if args.models:
        models_to_check = args.models
    elif args.from_warnings:
        # Check the 8 models from the latest warnings
        models_to_check = [
            'alibaba/qwen-3-14b',
            'google/gemini-2.0-flash',
            'deepseek/deepseek-chat',
            'mistral/mistral-large',
            'meta/llama-3-8b-instruct',
            'bfl/flux-1-1-pro',
            'bytedance/sdxl-lightning-4step',
            'cohere/command-r-plus'
        ]
    else:
        print("Error: Must specify --models or --from-warnings")
        parser.print_help()
        return 1

    print(f"🔍 Checking pricing for {len(models_to_check)} models...")
    print()

    # Check models
    results = await check_multiple_models(models_to_check)

    # Format and display report
    report = format_results(results)
    print(report)

    # Save to file if requested
    if args.output:
        with open(args.output, 'w') as f:
            f.write(report)
        print(f"\n✅ Report saved to: {args.output}")

    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
