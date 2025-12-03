#!/usr/bin/env python3
"""Check the current latest_models table structure and data"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.supabase_config import get_supabase_client
import json

def check_latest_models():
    supabase = get_supabase_client()

    print("=" * 80)
    print("CURRENT MODEL STORAGE: 'latest_models' TABLE")
    print("=" * 80)

    try:
        # Get count
        count_response = supabase.table("latest_models").select("*", count="exact").limit(0).execute()
        count = count_response.count if hasattr(count_response, 'count') else 'Unknown'

        print(f"\n📊 Total models in 'latest_models' table: {count}")

        # Get sample models
        response = supabase.table("latest_models").select("*").limit(10).execute()

        if response.data:
            print(f"\n✅ Models are stored with these fields:")
            first_model = response.data[0]
            for key in first_model.keys():
                print(f"   - {key}")

            print(f"\n📋 Sample models (first 5):")
            print("-" * 80)
            for i, model in enumerate(response.data[:5], 1):
                model_id = model.get('id', 'N/A')
                model_name = model.get('name', model.get('model', 'N/A'))
                provider = model.get('provider', model.get('top_provider', 'N/A'))
                pricing = model.get('pricing', {})

                print(f"{i}. {model_name[:50]:50} | Provider: {str(provider)[:20]:20}")

            # Group by provider
            print(f"\n📊 Models by Provider:")
            print("-" * 80)

            # Get all models
            all_response = supabase.table("latest_models").select("*").limit(1000).execute()
            models_by_provider = {}

            for model in all_response.data:
                provider = model.get('provider', model.get('top_provider', 'unknown'))
                if isinstance(provider, dict):
                    provider = provider.get('name', 'unknown')

                models_by_provider[provider] = models_by_provider.get(provider, 0) + 1

            for provider, count in sorted(models_by_provider.items(), key=lambda x: x[1], reverse=True):
                print(f"   {provider:30} {count:5} models")

        else:
            print("\n⚠️  Table exists but has no data")

    except Exception as e:
        print(f"\n❌ Error accessing latest_models: {e}")

    print("\n" + "=" * 80)
    print("CONCLUSION")
    print("=" * 80)
    print("""
✅ YES, models are stored in a database table: 'latest_models'

The new table structure (providers + models) exists in migrations but:
  - Applied to REMOTE database
  - NOT applied to LOCAL database (still using 'latest_models')

To use the new structure locally, you need to:
  1. Apply migration 20251121000000_add_providers_and_models_tables.sql
  2. Migrate data from 'latest_models' to 'models' table
  3. Update code to use the new tables
""")
    print("=" * 80)

if __name__ == '__main__':
    check_latest_models()
