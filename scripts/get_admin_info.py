#!/usr/bin/env python3
"""Get admin user information from staging database"""

import requests
import json

SUPABASE_URL = "https://ynleroehyrmaafkgjgmr.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlubGVyb2VoeXJtYWFma2dqZ21yIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1OTY4Nzc3OSwiZXhwIjoyMDc1MjYzNzc5fQ.kIehmSJC9EX86rkhCbhzX6ZHiTfQO7k6ZM2wU4e6JNs"

headers = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}"
}

# First, get the schema of users table
print("Getting user table schema...")
response = requests.get(
    f"{SUPABASE_URL}/rest/v1/users?limit=1",
    headers=headers
)

if response.status_code == 200:
    users = response.json()
    if users:
        print("\nUser table fields:")
        print(json.dumps(list(users[0].keys()), indent=2))
        print("\nSample user:")
        # Redact sensitive fields
        sample = users[0].copy()
        for key in ['password_hash', 'api_key', 'key_hash']:
            if key in sample:
                sample[key] = "[REDACTED]"
        print(json.dumps(sample, indent=2))
    else:
        print("No users found")
else:
    print(f"Error: {response.status_code}")
    print(response.text)

# Check for users with role='admin'
print("\n\nChecking for admin users (role='admin')...")
response = requests.get(
    f"{SUPABASE_URL}/rest/v1/users?role=eq.admin&select=id,email,role&limit=5",
    headers=headers
)

if response.status_code == 200:
    admins = response.json()
    print(f"Found {len(admins)} admin users:")
    print(json.dumps(admins, indent=2))
else:
    print(f"Error: {response.status_code}")
    print(response.text)

# Check roles table
print("\n\nChecking roles table...")
response = requests.get(
    f"{SUPABASE_URL}/rest/v1/roles?limit=10",
    headers=headers
)

if response.status_code == 200:
    roles = response.json()
    print(f"Roles table:")
    print(json.dumps(roles, indent=2))
else:
    print(f"Error querying roles: {response.status_code}")
    print(response.text)
