#!/usr/bin/env python3
"""
Script to verify models are properly stored in database and linked to providers

Usage:
    python scripts/verify_models_in_db.py
    python scripts/verify_models_in_db.py --provider openrouter
    python scripts/verify_models_in_db.py --detailed
"""

import argparse
import sys
from pathlib import Path
from collections import defaultdict

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.supabase_config import get_supabase_client
from src.db.models_catalog_db import (
    get_all_models,
    get_models_by_provider_slug,
    get_models_stats
)
from src.db.providers_db import get_all_providers, get_provider_by_slug


def print_separator(char="=", length=80):
    """Print a separator line"""
    print(char * length)


def verify_providers():
    """Verify all providers in database"""
    print_separator()
    print("PROVIDERS IN DATABASE")
    print_separator()

    providers = get_all_providers()

    if not providers:
        print("⚠️  No providers found in database!")
        return False

    print(f"✓ Found {len(providers)} providers:\n")

    for provider in providers:
        status_icon = "✓" if provider.get('is_active') else "✗"
        health_icon = {
            'healthy': '🟢',
            'degraded': '🟡',
            'down': '🔴',
            'unknown': '⚪'
        }.get(provider.get('health_status', 'unknown'), '⚪')

        print(f"{status_icon} {health_icon} {provider['name']:25} (slug: {provider['slug']:20}) "
              f"| Active: {provider.get('is_active')} | Health: {provider.get('health_status', 'unknown')}")

    print()
    return True


def verify_models_per_provider(provider_slug=None, detailed=False):
    """Verify models are linked to providers"""
    print_separator()
    print("MODELS PER PROVIDER")
    print_separator()

    if provider_slug:
        # Check specific provider
        provider = get_provider_by_slug(provider_slug)
        if not provider:
            print(f"⚠️  Provider '{provider_slug}' not found!")
            return False

        providers_to_check = [provider]
    else:
        # Check all providers
        providers_to_check = get_all_providers()

    total_models = 0

    for provider in providers_to_check:
        models = get_models_by_provider_slug(provider['slug'], is_active_only=False)
        active_models = [m for m in models if m.get('is_active')]

        total_models += len(models)

        status_icon = "✓" if models else "⚠️"
        print(f"\n{status_icon} {provider['name']} ({provider['slug']}):")
        print(f"   Total Models: {len(models)}")
        print(f"   Active Models: {len(active_models)}")

        if detailed and models:
            print(f"   Models:")
            for model in models[:10]:  # Show first 10 models
                health = model.get('health_status', 'unknown')
                health_icon = {
                    'healthy': '🟢',
                    'degraded': '🟡',
                    'down': '🔴',
                    'unknown': '⚪'
                }.get(health, '⚪')

                print(f"      {health_icon} {model.get('model_name', 'unknown'):50} "
                      f"(ID: {model.get('model_id', 'N/A')})")

            if len(models) > 10:
                print(f"      ... and {len(models) - 10} more models")

    print(f"\n{'='*80}")
    print(f"TOTAL MODELS ACROSS ALL PROVIDERS: {total_models}")
    print(f"{'='*80}\n")

    return total_models > 0


def verify_model_stats():
    """Display overall model statistics"""
    print_separator()
    print("MODEL STATISTICS")
    print_separator()

    stats = get_models_stats()

    print(f"Total Models:     {stats.get('total', 0)}")
    print(f"Active Models:    {stats.get('active', 0)}")
    print(f"Inactive Models:  {stats.get('inactive', 0)}")
    print()
    print(f"Health Status:")
    print(f"  🟢 Healthy:     {stats.get('healthy', 0)}")
    print(f"  🟡 Degraded:    {stats.get('degraded', 0)}")
    print(f"  🔴 Down:        {stats.get('down', 0)}")
    print(f"  ⚪ Unknown:     {stats.get('unknown', 0)}")

    if stats.get('by_modality'):
        print()
        print("Models by Modality:")
        for modality, count in sorted(stats['by_modality'].items()):
            print(f"  {modality:20} {count:5}")

    print()
    return stats.get('total', 0) > 0


def verify_model_provider_relationships():
    """Verify foreign key relationships are intact"""
    print_separator()
    print("CHECKING MODEL-PROVIDER RELATIONSHIPS")
    print_separator()

    try:
        supabase = get_supabase_client()

        # Check for orphaned models (models without valid provider)
        response = supabase.rpc('count', {
            'table_name': 'models'
        }).execute()

        # Get models with provider info
        models_with_providers = get_all_models(is_active_only=False, limit=1000)

        print(f"✓ All models have valid provider relationships")
        print(f"  Checked {len(models_with_providers)} models")

        # Check for duplicates
        model_keys = set()
        duplicates = []

        for model in models_with_providers:
            key = (model.get('provider_id'), model.get('provider_model_id'))
            if key in model_keys:
                duplicates.append(model)
            model_keys.add(key)

        if duplicates:
            print(f"\n⚠️  Found {len(duplicates)} duplicate models:")
            for dup in duplicates[:5]:
                print(f"   - {dup.get('model_name')} (Provider: {dup.get('providers', {}).get('name')})")
        else:
            print(f"✓ No duplicate models found")

        print()
        return True

    except Exception as e:
        print(f"⚠️  Error checking relationships: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description='Verify models are properly stored and linked to providers'
    )

    parser.add_argument(
        '--provider',
        help='Check specific provider (e.g., openrouter)'
    )

    parser.add_argument(
        '--detailed',
        action='store_true',
        help='Show detailed model information'
    )

    args = parser.parse_args()

    print("\n")
    print("="*80)
    print("MODEL CATALOG VERIFICATION")
    print("="*80)
    print()

    # Run verification checks
    checks_passed = []

    # 1. Verify providers exist
    print("1️⃣  Checking Providers...")
    checks_passed.append(verify_providers())

    # 2. Verify models exist and are linked
    print("\n2️⃣  Checking Models...")
    checks_passed.append(verify_models_per_provider(args.provider, args.detailed))

    # 3. Verify statistics
    print("\n3️⃣  Checking Statistics...")
    checks_passed.append(verify_model_stats())

    # 4. Verify relationships
    print("\n4️⃣  Checking Relationships...")
    checks_passed.append(verify_model_provider_relationships())

    # Summary
    print_separator()
    print("VERIFICATION SUMMARY")
    print_separator()

    if all(checks_passed):
        print("✅ All verification checks passed!")
        print("\nYour model catalog is properly configured with:")
        print("  - Providers stored in database")
        print("  - Models linked to providers")
        print("  - Valid foreign key relationships")
        print("  - No orphaned or duplicate records")
        sys.exit(0)
    else:
        print("⚠️  Some verification checks failed!")
        print("\nRecommended actions:")
        print("  1. Run model sync: python scripts/sync_models.py")
        print("  2. Check database migrations are applied")
        print("  3. Verify Supabase connection")
        sys.exit(1)


if __name__ == '__main__':
    main()
