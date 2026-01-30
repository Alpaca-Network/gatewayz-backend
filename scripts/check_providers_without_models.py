#!/usr/bin/env python3
"""
Script to find providers in the providers table that have no models in the models table
"""
import os
import sys
from pathlib import Path

# Add src to path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from src.config.supabase_config import get_supabase_client


def main():
    """Find providers without models"""
    supabase = get_supabase_client()

    print("=" * 80)
    print("PROVIDERS WITHOUT MODELS")
    print("=" * 80)
    print()

    # Query providers with no models using LEFT JOIN
    query = """
        SELECT
            p.id,
            p.name,
            p.slug,
            p.is_active,
            p.health_status,
            p.created_at
        FROM providers p
        LEFT JOIN models m ON m.provider_id = p.id
        WHERE m.id IS NULL
        ORDER BY p.name
    """

    try:
        result = supabase.rpc('exec_sql', {'sql': query}).execute()
        providers_without_models = result.data

        if not providers_without_models:
            print("✓ All providers have models!")
        else:
            print(f"Found {len(providers_without_models)} provider(s) without models:\n")
            for i, provider in enumerate(providers_without_models, 1):
                print(f"{i}. {provider['name']}")
                print(f"   Slug: {provider['slug']}")
                print(f"   ID: {provider['id']}")
                print(f"   Active: {provider['is_active']}")
                print(f"   Health: {provider['health_status']}")
                print(f"   Created: {provider['created_at']}")
                print()
    except Exception as e:
        # If RPC doesn't work, try direct query
        print("RPC method failed, trying direct query...")

        # Get all providers
        all_providers = supabase.table('providers').select('*').execute()

        # Get all provider IDs that have models
        models_with_providers = supabase.table('models').select('provider_id').execute()
        provider_ids_with_models = set(m['provider_id'] for m in models_with_providers.data if m['provider_id'])

        # Find providers without models
        providers_without_models = [
            p for p in all_providers.data
            if p['id'] not in provider_ids_with_models
        ]

        if not providers_without_models:
            print("✓ All providers have models!")
        else:
            print(f"Found {len(providers_without_models)} provider(s) without models:\n")
            for i, provider in enumerate(sorted(providers_without_models, key=lambda x: x['name']), 1):
                print(f"{i}. {provider['name']}")
                print(f"   Slug: {provider['slug']}")
                print(f"   ID: {provider['id']}")
                print(f"   Active: {provider['is_active']}")
                print(f"   Health: {provider['health_status']}")
                print(f"   Created: {provider['created_at']}")
                print()

    # Also show summary
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)

    total_providers = supabase.table('providers').select('id', count='exact').execute()
    providers_with_models = supabase.table('models').select('provider_id').execute()
    unique_provider_ids = len(set(m['provider_id'] for m in providers_with_models.data if m['provider_id']))

    print(f"Total Providers: {total_providers.count}")
    print(f"Providers with Models: {unique_provider_ids}")
    print(f"Providers without Models: {total_providers.count - unique_provider_ids}")


if __name__ == "__main__":
    main()
