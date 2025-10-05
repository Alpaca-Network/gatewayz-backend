import os
from src.supabase_config import get_supabase_client
import secrets
api_key = f"gw_live_{secrets.token_urlsafe(32)}"
client = get_supabase_client()
result = client.table('users').insert({
    'username': 'stripe_tester',
    'email': 'stripe@test.com',
    'api_key': api_key,
    'credits': 10.0,
    'auth_method': 'email'
}).execute()
if result.data:
    print(f"✅ Test user created!")
    print(f"📧 Email: stripe@test.com")
    print(f"🔑 API Key: {api_key}")
    print(f"\nSave this for testing:")
    print(f'export TEST_API_KEY="{api_key}"')
else:
    print("❌ Failed to create user")
