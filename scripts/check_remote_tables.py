#!/usr/bin/env python3
"""Check tables on remote production Supabase"""

import os
from supabase import create_client

def check_remote_tables():
    # Get production credentials from environment
    url = os.getenv('SUPABASE_PROD_URL') or os.getenv('NEXT_PUBLIC_SUPABASE_URL')
    key = os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_PROD_SERVICE_KEY')

    if not url or not key:
        print("❌ Missing production credentials!")
        print("   Need: SUPABASE_PROD_URL and SUPABASE_SERVICE_ROLE_KEY")
        return

    print(f"🔍 Checking remote database: {url}")
    print("=" * 70)

    supabase = create_client(url, key)

    # Check providers
    try:
        providers = supabase.table('providers').select("*").limit(5).execute()
        print(f"\n✅ PROVIDERS TABLE:")
        print(f"   Count: {len(providers.data)}")
        if providers.data:
            for p in providers.data:
                print(f"   - {p.get('name')} (slug: {p.get('slug')})")
        else:
            print("   ⚠️  Table exists but is EMPTY!")
    except Exception as e:
        print(f"\n❌ PROVIDERS TABLE: {e}")

    # Check models
    try:
        models = supabase.table('models').select("*").limit(5).execute()
        print(f"\n✅ MODELS TABLE:")
        print(f"   Count: {len(models.data)}")
        if models.data:
            for m in models.data:
                print(f"   - {m.get('model_name')} (ID: {m.get('model_id')})")
        else:
            print("   ⚠️  Table exists but is EMPTY!")
    except Exception as e:
        print(f"\n❌ MODELS TABLE: {e}")

    print("=" * 70)

if __name__ == '__main__':
    check_remote_tables()
