#!/usr/bin/env python3
"""Get API key for admin user"""
import os

import requests
import json

SUPABASE_URL = "https://ynleroehyrmaafkgjgmr.supabase.co"
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

headers = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}"
}

# Get admin users
print("Getting admin users...")
response = requests.get(
    f"{SUPABASE_URL}/rest/v1/users?role=eq.admin&select=id,email,api_key&limit=5",
    headers=headers
)

if response.status_code == 200:
    admins = response.json()
    if admins:
        print(f"\nFound {len(admins)} admin users:\n")
        for admin in admins:
            print(f"Admin ID: {admin['id']}")
            print(f"Email: {admin['email']}")
            print(f"API Key: {admin.get('api_key', 'N/A')}")
            print()

            # Save the first admin's API key to a file
            if admin.get('api_key'):
                with open('.admin_key_staging', 'w') as f:
                    f.write(admin['api_key'])
                print(f"✅ Saved admin API key to .admin_key_staging")
                break
    else:
        print("No admin users found")
else:
    print(f"Error: {response.status_code}")
    print(response.text)
