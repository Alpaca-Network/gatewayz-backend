#!/usr/bin/env python3
"""
Create a test admin user for testing /admin/users endpoint
"""
import os
import sys

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.config.supabase_config import get_supabase_client
from src.db.users import create_enhanced_user
from src.db.roles import update_user_role, UserRole

def create_test_admin():
    """Create a test admin user and return their API key"""
    print("Creating test admin user...")

    # Create user
    user_data = create_enhanced_user(
        username="Test Admin",
        email="admin@test.com",
        auth_method="email",
        credits=1000.0
    )

    print(f"✅ User created:")
    print(f"   ID: {user_data['user_id']}")
    print(f"   Email: {user_data['email']}")
    print(f"   API Key: {user_data['primary_api_key']}")

    # Update to admin role
    update_user_role(
        user_id=user_data['user_id'],
        new_role=UserRole.ADMIN,
        reason="Test admin user for /admin/users endpoint testing"
    )

    print(f"\n✅ User upgraded to admin role")
    print(f"\n🔑 Use this API key for testing:")
    print(f"   {user_data['primary_api_key']}")
    print(f"\n📝 Postman Configuration:")
    print(f"   Authorization: Bearer {user_data['primary_api_key']}")
    print(f"   URL: http://localhost:8000/admin/users?limit=10")

    return user_data['primary_api_key']

if __name__ == "__main__":
    try:
        api_key = create_test_admin()
        print(f"\n✅ Success! Copy the API key above to use in Postman")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
