#!/usr/bin/env python3
"""
Script to show all providers that have models in the models table
"""
import os
import sys
from pathlib import Path
from collections import defaultdict

# Add src to path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from src.config.supabase_config import get_supabase_client


def main():
    """Show all providers that have models"""
    supabase = get_supabase_client()

    print("=" * 80)
    print("PROVIDERS WITH MODELS")
    print("=" * 80)
    print()

    # Get all providers
    providers_result = supabase.table('providers').select('id,name,slug,is_active,health_status').execute()
    providers = {p['id']: p for p in providers_result.data}

    # Get ALL models with pagination
    all_models = []
    page_size = 1000
    offset = 0

    print("Fetching all models from database...")
    while True:
        models_result = supabase.table('models').select('provider_id').range(offset, offset + page_size - 1).execute()
        if not models_result.data:
            break
        all_models.extend(models_result.data)
        if len(models_result.data) < page_size:
            break
        offset += page_size

    print(f"Total models fetched: {len(all_models)}")
    print()

    # Count models per provider
    provider_counts = defaultdict(int)
    for model in all_models:
        if model['provider_id']:
            provider_counts[model['provider_id']] += 1

    print("=" * 80)
    print(f"PROVIDERS WITH MODELS (sorted by model count)")
    print("=" * 80)
    print()

    # Sort by model count (descending)
    sorted_providers = sorted(provider_counts.items(), key=lambda x: x[1], reverse=True)

    print(f"{'#':<4} {'Provider Name':<30} {'Slug':<25} {'Models':<8} {'Active':<8} {'Health':<10}")
    print("-" * 95)

    for i, (provider_id, count) in enumerate(sorted_providers, 1):
        provider = providers.get(provider_id, {})
        name = provider.get('name', f'Unknown (ID: {provider_id})')
        slug = provider.get('slug', 'unknown')
        is_active = provider.get('is_active', False)
        health = provider.get('health_status', 'unknown')

        print(f"{i:<4} {name:<30} {slug:<25} {count:<8} {str(is_active):<8} {health:<10}")

    print()
    print("=" * 80)
    print(f"SUMMARY")
    print("=" * 80)
    print(f"Total providers in database: {len(providers)}")
    print(f"Providers WITH models: {len(provider_counts)}")
    print(f"Providers WITHOUT models: {len(providers) - len(provider_counts)}")
    print(f"Total models: {len(all_models)}")
    print("=" * 80)


if __name__ == "__main__":
    main()
