#!/usr/bin/env python3
"""
Script to run the exact SQL query and compare results
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
    """Compare SQL query results with Python query results"""
    supabase = get_supabase_client()

    print("=" * 80)
    print("METHOD 1: Using Python to fetch all models and count")
    print("=" * 80)
    print()

    # Get all providers
    providers_result = supabase.table('providers').select('id,name,slug').execute()
    all_providers = {p['id']: p for p in providers_result.data}
    print(f"Total providers: {len(all_providers)}")

    # Get ALL models with pagination
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

    providers_with_models_py = set(provider_counts.keys())
    providers_without_models_py = set(all_providers.keys()) - providers_with_models_py

    print(f"Total models: {len(all_models)}")
    print(f"Providers WITH models: {len(providers_with_models_py)}")
    print(f"Providers WITHOUT models: {len(providers_without_models_py)}")
    print()

    print("Providers WITHOUT models (Python method):")
    for pid in sorted(providers_without_models_py):
        p = all_providers[pid]
        print(f"  {pid:4} - {p['name']:30} ({p['slug']})")
    print()

    print("=" * 80)
    print("METHOD 2: Using LEFT JOIN SQL query (like you ran)")
    print("=" * 80)
    print()

    # Try LEFT JOIN approach using Supabase query
    # Get providers that don't have any models
    providers_no_models = (
        supabase.table('providers')
        .select('id,name,slug,is_active')
        .execute()
    )

    # Get distinct provider IDs from models
    models_providers = (
        supabase.table('models')
        .select('provider_id')
        .execute()
    )

    # Create set of provider IDs that have models (from first 1000)
    provider_ids_with_models_limited = set(m['provider_id'] for m in models_providers.data if m['provider_id'])

    providers_without_models_limited = [
        p for p in providers_no_models.data
        if p['id'] not in provider_ids_with_models_limited
    ]

    print(f"Providers without models (using first 1000 models only): {len(providers_without_models_limited)}")
    print()

    print("Providers WITHOUT models (SQL LEFT JOIN simulation - limited):")
    for p in sorted(providers_without_models_limited, key=lambda x: x['name']):
        print(f"  {p['id']:4} - {p['name']:30} ({p['slug']})")
    print()

    print("=" * 80)
    print("COMPARISON")
    print("=" * 80)
    print()

    limited_ids = set(p['id'] for p in providers_without_models_limited)
    python_ids = providers_without_models_py

    print(f"SQL query (limited) found: {len(limited_ids)} providers without models")
    print(f"Python (full scan) found: {len(python_ids)} providers without models")
    print()

    only_in_sql = limited_ids - python_ids
    only_in_python = python_ids - limited_ids

    if only_in_sql:
        print(f"Providers found by SQL but NOT by Python (these ACTUALLY HAVE models): {len(only_in_sql)}")
        for pid in sorted(only_in_sql):
            p = all_providers[pid]
            count = provider_counts.get(pid, 0)
            print(f"  {pid:4} - {p['name']:30} ({p['slug']:20}) - {count} models")
        print()

    if only_in_python:
        print(f"Providers found by Python but NOT by SQL: {len(only_in_python)}")
        for pid in sorted(only_in_python):
            p = all_providers[pid]
            print(f"  {pid:4} - {p['name']:30} ({p['slug']})")
        print()

    print("=" * 80)
    print("EXPLANATION")
    print("=" * 80)
    print()
    print("If your SQL query returned 30 providers without models,")
    print("but Python found only 16, it's likely because:")
    print("1. Your SQL query was run with a LIMIT (default 1000 rows)")
    print("2. Some providers have models beyond row 1000")
    print("3. So they appeared to have NO models in the limited result")
    print()
    print("The Python script fetches ALL models, so it's more accurate.")


if __name__ == "__main__":
    main()
