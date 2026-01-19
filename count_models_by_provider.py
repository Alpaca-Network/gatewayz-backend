#!/usr/bin/env python3
"""Count models per provider from the database"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from src.config.supabase_config import get_supabase_client


def count_models_by_provider():
    """Count models per provider from database"""
    supabase = get_supabase_client()

    print("=" * 80)
    print("MODELS PER PROVIDER - DATABASE QUERY")
    print("=" * 80)

    try:
        # Check if 'models' table exists (new schema)
        try:
            count_response = supabase.table("models").select("*", count="exact").limit(0).execute()
            table_name = "models"
            print(f"\n✅ Using table: '{table_name}'")
        except Exception:
            # Fall back to 'latest_models' (old schema)
            table_name = "latest_models"
            print(f"\n✅ Using table: '{table_name}' (legacy)")

        # Get total count
        count_response = supabase.table(table_name).select("*", count="exact").limit(0).execute()
        total_count = count_response.count if hasattr(count_response, 'count') else 'Unknown'
        print(f"📊 Total models in database: {total_count:,}\n")

        # Fetch all models (with pagination if needed)
        all_models = []
        page_size = 1000
        offset = 0

        print("Fetching models from database...")

        while True:
            response = supabase.table(table_name).select("*").range(offset, offset + page_size - 1).execute()

            if not response.data:
                break

            all_models.extend(response.data)
            offset += page_size

            if len(response.data) < page_size:
                break

        print(f"✅ Fetched {len(all_models):,} models\n")

        # Count by provider
        provider_counts = {}

        for model in all_models:
            provider = None

            # Try multiple fields to extract provider
            if 'provider_slug' in model and model['provider_slug']:
                provider = model['provider_slug']
            elif 'provider' in model:
                if isinstance(model['provider'], dict):
                    provider = model['provider'].get('slug') or model['provider'].get('name', 'unknown')
                elif isinstance(model['provider'], str):
                    provider = model['provider']
            elif 'top_provider' in model:
                if isinstance(model['top_provider'], dict):
                    provider = model['top_provider'].get('slug') or model['top_provider'].get('name', 'unknown')
                elif isinstance(model['top_provider'], str):
                    provider = model['top_provider']
            elif 'id' in model and '/' in str(model['id']):
                # Extract from ID format: provider/model-name
                provider = str(model['id']).split('/')[0]
            elif 'source_gateway' in model and model['source_gateway']:
                provider = model['source_gateway']

            if provider:
                provider = str(provider).lower()
                provider_counts[provider] = provider_counts.get(provider, 0) + 1
            else:
                provider_counts['unknown'] = provider_counts.get('unknown', 0) + 1

        # Sort by count (descending)
        sorted_providers = sorted(
            provider_counts.items(),
            key=lambda x: x[1],
            reverse=True
        )

        # Print results
        print("=" * 80)
        print(f"{'Provider':<40} {'Model Count':>20}")
        print("-" * 80)

        counted_total = 0
        for provider, count in sorted_providers:
            print(f"{provider:<40} {count:>20,}")
            counted_total += count

        print("-" * 80)
        print(f"{'TOTAL COUNTED':<40} {counted_total:>20,}")
        print(f"{'UNIQUE PROVIDERS':<40} {len(provider_counts):>20,}")
        print("=" * 80)

        # Show top 10 for summary
        print("\n📊 TOP 10 PROVIDERS BY MODEL COUNT:")
        print("-" * 80)
        for i, (provider, count) in enumerate(sorted_providers[:10], 1):
            percentage = (count / counted_total * 100) if counted_total > 0 else 0
            print(f"{i:2}. {provider:<35} {count:>8,} models ({percentage:5.1f}%)")

        print("=" * 80)

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    count_models_by_provider()
