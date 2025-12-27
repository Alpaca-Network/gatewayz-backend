#!/usr/bin/env python3
"""
Query all GPT-4 models from the catalog
"""

import sys
from pathlib import Path

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.models_catalog_db import search_models

def main():
    """Search for all GPT-4 models"""
    print("Searching for GPT-4 models in catalog...\n")

    # Search for GPT-4
    models = search_models("gpt-4")

    if not models:
        print("No GPT-4 models found.")
        return

    print(f"Found {len(models)} GPT-4 models:\n")
    print(f"{'Provider':<20} {'Model Name':<50} {'API Name':<40} {'Active':<10}")
    print("=" * 120)

    for model in models:
        provider = model.get('providers', {}).get('slug', 'N/A') if isinstance(model.get('providers'), dict) else 'N/A'
        model_name = model.get('model_name', 'N/A')
        api_name = model.get('provider_model_id', 'N/A')
        is_active = "✓" if model.get('is_active') else "✗"

        print(f"{provider:<20} {model_name:<50} {api_name:<40} {is_active:<10}")

    # Group by provider
    print("\n" + "=" * 120)
    print("\nSummary by Provider:")
    print("=" * 120)

    providers = {}
    for model in models:
        provider = model.get('providers', {}).get('slug', 'Unknown') if isinstance(model.get('providers'), dict) else 'Unknown'
        providers[provider] = providers.get(provider, 0) + 1

    for provider, count in sorted(providers.items()):
        print(f"{provider:<20}: {count} GPT-4 models")

if __name__ == '__main__':
    main()
