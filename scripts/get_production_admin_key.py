#!/usr/bin/env python3
"""Get API key for admin user from PRODUCTION database"""

import requests
import json
import os

# Production Supabase configuration
# Note: These should be production credentials
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://ynleroehyrmaafkgjgmr.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_KEY:
    print("❌ Error: SUPABASE_KEY environment variable not set")
    print("   This should be the PRODUCTION service role key")
    print("   Export it with: export SUPABASE_KEY=your_production_key")
    exit(1)

headers = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}"
}

# Get admin users
print("Getting admin users from PRODUCTION database...")
print(f"Supabase URL: {SUPABASE_URL}")

response = requests.get(
    f"{SUPABASE_URL}/rest/v1/users?role=eq.admin&select=id,email,api_key&limit=5",
    headers=headers
)

if response.status_code == 200:
    admins = response.json()
    if admins:
        print(f"\n✅ Found {len(admins)} admin users:\n")
        for admin in admins:
            print(f"Admin ID: {admin['id']}")
            print(f"Email: {admin['email']}")
            print(f"API Key: {admin.get('api_key', 'N/A')}")
            print()

            # Save the first admin's API key to a file
            if admin.get('api_key'):
                with open('.admin_key_production', 'w') as f:
                    f.write(admin['api_key'])
                print(f"✅ Saved production admin API key to .admin_key_production")
                print(f"\nYou can now run the verification with:")
                print(f"  export PROD_ADMIN_KEY=$(cat .admin_key_production)")
                print(f"  python3 scripts/verify_production_readiness.py")
                break
    else:
        print("❌ No admin users found")
else:
    print(f"❌ Error: {response.status_code}")
    print(response.text)
