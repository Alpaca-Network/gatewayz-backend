#!/usr/bin/env python3
"""Check providers and models tables"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.supabase_config import get_supabase_client

def check_tables():
    supabase = get_supabase_client()

    print("=" * 70)
    print("CHECKING PROVIDERS AND MODELS TABLES")
    print("=" * 70)

    # Check providers
    try:
        providers = supabase.table('providers').select("*").execute()
        print(f"\n✅ PROVIDERS TABLE:")
        print(f"   Count: {len(providers.data)}")
        if providers.data:
            print(f"   Sample providers:")
            for p in providers.data[:5]:
                print(f"   - {p.get('name')} (slug: {p.get('slug')})")
        else:
            print("   ⚠️  Table is empty!")
    except Exception as e:
        print(f"\n❌ PROVIDERS TABLE ERROR: {e}")

    # Check models
    try:
        models = supabase.table('models').select("*").execute()
        print(f"\n✅ MODELS TABLE:")
        print(f"   Count: {len(models.data)}")
        if models.data:
            print(f"   Sample models:")
            for m in models.data[:5]:
                print(f"   - {m.get('model_name')} (ID: {m.get('model_id')})")
        else:
            print("   ⚠️  Table is empty!")
    except Exception as e:
        print(f"\n❌ MODELS TABLE ERROR: {e}")

    print("=" * 70)

if __name__ == '__main__':
    check_tables()
