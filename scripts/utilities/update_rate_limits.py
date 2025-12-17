#!/usr/bin/env python3
"""
Update rate limit configuration for a specific API key
"""

import os
from supabase import create_client, Client

# Load credentials from environment variables
API_KEY = os.environ.get("GATEWAYZ_API_KEY", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

# New rate limit configuration with higher concurrency
NEW_CONFIG = {
    'requests_per_minute': 250,
    'requests_per_hour': 5000,
    'requests_per_day': 50000,
    'tokens_per_minute': 50000,
    'tokens_per_hour': 500000,
    'tokens_per_day': 5000000,
    'burst_limit': 500,
    'concurrency_limit': 100,  # Increased from 5 to 100
    'window_size_seconds': 60
}

def main():
    # Create Supabase client
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    print(f"Updating rate limits for API key: {API_KEY[:20]}...")

    try:
        # Try api_keys_new table (newer system)
        result_new = supabase.table('api_keys_new').select('*').eq('api_key', API_KEY).execute()

        if result_new.data:
            print(f"[OK] Found API key in api_keys_new table")
            key_id = result_new.data[0]['id']
            print(f"[OK] API Key ID: {key_id}")

            # Show current data
            current_data = result_new.data[0]
            print(f"[INFO] Current max_requests: {current_data.get('max_requests')}")
            print(f"[INFO] Current max_tokens: {current_data.get('max_tokens')}")

            # Update the api_keys_new table directly with higher limits
            print(f"[OK] Updating api_keys_new table...")
            update_result = supabase.table('api_keys_new').update({
                'max_requests': NEW_CONFIG['requests_per_hour'],
                'max_tokens': NEW_CONFIG['tokens_per_hour']
            }).eq('id', key_id).execute()

            print(f"[OK] Updated api_keys_new entry")
            print(f"[OK] Rate limit configuration updated:")
            print(f"  - Requests per hour: {NEW_CONFIG['requests_per_hour']}")
            print(f"  - Tokens per hour: {NEW_CONFIG['tokens_per_hour']}")
        else:
            print(f"[ERROR] API key not found in api_keys_new table")

        print("\n[OK] Rate limit update complete!")
        print("\nNew configuration:")
        for key, value in NEW_CONFIG.items():
            print(f"  {key}: {value}")

    except Exception as e:
        print(f"[ERROR] Error updating rate limits: {e}")
        raise

if __name__ == '__main__':
    main()
