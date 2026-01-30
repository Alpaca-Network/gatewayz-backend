#!/usr/bin/env python3
"""
Script to count models per provider
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
    """Count models per provider"""
    supabase = get_supabase_client()

    print("=" * 80)
    print("MODELS PER PROVIDER")
    print("=" * 80)
    print()

    # Get all providers
    providers_result = supabase.table('providers').select('id,name,slug').execute()
    providers = {p['id']: p for p in providers_result.data}

    print(f"Total providers in database: {len(providers)}")
    print()

    # Get all models with provider_id (fetch ALL, not just default 1000)
    # Supabase has a default limit of 1000, need to paginate or use count
    all_models = []
    page_size = 1000
    offset = 0

    while True:
        models_result = supabase.table('models').select('provider_id').range(offset, offset + page_size - 1).execute()
        if not models_result.data:
            break
        all_models.extend(models_result.data)
        if len(models_result.data) < page_size:
            break
        offset += page_size

    # Count models per provider
    provider_counts = defaultdict(int)
    for model in all_models:
        if model['provider_id']:
            provider_counts[model['provider_id']] += 1

    print(f"Total models in database: {len(all_models)}")
    print(f"Providers with models: {len(provider_counts)}")
    print()

    print("=" * 80)
    print("PROVIDERS WITH MODELS (sorted by model count)")
    print("=" * 80)
    print()

    # Sort by model count
    sorted_providers = sorted(provider_counts.items(), key=lambda x: x[1], reverse=True)

    for provider_id, count in sorted_providers:
        provider = providers.get(provider_id, {})
        name = provider.get('name', f'Unknown (ID: {provider_id})')
        slug = provider.get('slug', 'unknown')
        print(f"{name:30} ({slug:20}) - {count:4} models")

    print()
    print("=" * 80)
    print("PROVIDERS WITHOUT MODELS")
    print("=" * 80)
    print()

    # Find providers without models
    providers_with_models = set(provider_counts.keys())
    providers_without_models = []

    for provider_id, provider in providers.items():
        if provider_id not in providers_with_models:
            providers_without_models.append(provider)

    if providers_without_models:
        providers_without_models.sort(key=lambda x: x['name'])
        for provider in providers_without_models:
            print(f"{provider['name']:30} ({provider['slug']:20})")
    else:
        print("All providers have models!")

    print()
    print("=" * 80)
    print(f"Summary: {len(provider_counts)}/{len(providers)} providers have models")
    print("=" * 80)


if __name__ == "__main__":
    main()
