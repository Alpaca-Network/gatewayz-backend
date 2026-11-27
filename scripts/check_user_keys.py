#!/usr/bin/env python3
"""Check user's API keys in the database"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")

# supabase-py 2.12 rejects the new sb_secret_ keys unless this shim runs first.
import pathlib as _pathlib
import sys as _sys

_sys.path.append(str(_pathlib.Path(__file__).resolve().parents[1]))
from src.config.supabase_compat import allow_new_api_keys  # noqa: E402

allow_new_api_keys()
supabase = create_client(supabase_url, supabase_key)

# Check API keys
print("🔍 Checking API Keys Table...")
print("")

response = supabase.table("api_keys_new").select("*").eq("user_id", 1).execute()

if response.data:
    for key in response.data:
        print(f"API Key ID: {key.get('id')}")
        print(f"User ID: {key.get('user_id')}")
        print(f"Key Name: {key.get('key_name')}")
        print(f"API Key (plain): {key.get('api_key')}")
        print(f"Encrypted Key: {key.get('encrypted_key')}")
        print(f"Key Hash: {key.get('key_hash')}")
        print(f"Last 4: {key.get('last4')}")
        print(f"Key Version: {key.get('key_version')}")
        print(f"Environment: {key.get('environment_tag')}")
        print(f"Is Active: {key.get('is_active')}")
        print(f"Is Primary: {key.get('is_primary')}")
        print("")
        print("=" * 80)
        print("")
else:
    print("❌ No API keys found for user_id = 1")
